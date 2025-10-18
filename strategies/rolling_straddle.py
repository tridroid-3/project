from strategies.base_strategy import BaseStrategy
import datetime
import re
import logging
import copy

logger = logging.getLogger(__name__)

class RollingStraddleStrategy(BaseStrategy):
    def __init__(self, config, logger):
        super().__init__(config, logger)
        self.name = "rolling_straddle"
        self.open_positions = []
        self.otm_legs = {}  # {instrument: entry_price}
        self.last_atm = None
        self.last_roll_time = None
        self.in_position = False
        self.has_otm_wings = False
        self.baseline_ce_ltp = None
        self.baseline_pe_ltp = None
        self.last_ce_action = None
        self.last_pe_action = None
        self.realized_ce_mtm = 0.0
        self.realized_pe_mtm = 0.0

        # Configs
        self.STRIKE_STEP = config.get("STRIKE_STEP", 100)
        self.LOT_SIZE = config.get("LOT_SIZE", 20)
        self.MESSAGE_LOTS = config.get("MESSAGE_LOTS", 1)
        self.STOPLOSS_PER_LOT = config.get("rolling_straddle", {}).get("stoploss_per_lot", 3000)
        self.TARGET_PER_LOT = config.get("rolling_straddle", {}).get("target_per_lot", 10000)
        self.ROLL_PCT = config.get("rolling_straddle", {}).get("roll_pct", 5.0)
        self.BUFFER = config.get("BUFFER", 10)
        self.HOLD_TIME = datetime.timedelta(minutes=1)
        self.SYMBOL = config.get("SYMBOL", "SENSEX")
        self.EXPIRY_DATE = config["upstox"]["expiry_date"]
        self.OTM_WING_FACTOR = config.get("iron_fly", {}).get("wing_factor", 1.0)
        self.OTM_EXIT_PCT = config.get("iron_fly", {}).get("otm_exit_pct", 25.0)

    def can_enter(self, snapshot, regime):
        ce_ltp = snapshot.get("ce_ltp", 0.0)
        pe_ltp = snapshot.get("pe_ltp", 0.0)
        if self.in_position:
            return False, "Already in position", {}
        # Only enter if regime/IV/vol is suitable
        if ce_ltp > 0.0 and pe_ltp > 0.0 and self._is_straddle_allowed(snapshot, regime):
            return True, "Entry conditions met", {}
        return False, "LTPs or regime not suitable", {}

    def enter(self, snapshot, params):
        # Enter ATM straddle only
        atm_strike = snapshot["atm_strike"]
        ce_instr = self._build_option_symbol(atm_strike, "C")
        pe_instr = self._build_option_symbol(atm_strike, "P")
        ce_ltp = snapshot["ce_ltp"]
        pe_ltp = snapshot["pe_ltp"]
        regime = snapshot.get("regime", "UNKNOWN")
        
        self.open_positions = [
            {"instrument": ce_instr, "side": "S", "entry_price": ce_ltp, "quantity": self.MESSAGE_LOTS, "mtm": 0.0},
            {"instrument": pe_instr, "side": "S", "entry_price": pe_ltp, "quantity": self.MESSAGE_LOTS, "mtm": 0.0}
        ]
        self.last_atm = atm_strike
        self.last_roll_time = datetime.datetime.now()
        self.in_position = True
        self.baseline_ce_ltp = ce_ltp
        self.baseline_pe_ltp = pe_ltp
        self.last_ce_action = "S"
        self.last_pe_action = "S"
        
        logger.info(f"[RollingStraddle] Entering straddle: ATM={atm_strike}, CE={ce_ltp:.2f}, "
                    f"PE={pe_ltp:.2f}, premium={ce_ltp + pe_ltp:.2f}, regime={regime}")
        
        return [
            {"action": "sell", "instrument": ce_instr, "lots": self.MESSAGE_LOTS},
            {"action": "sell", "instrument": pe_instr, "lots": self.MESSAGE_LOTS}
        ]

    def on_tick(self, snapshot, position):
        # 1. Manage main straddle MTM, roll, stop/target
        ce_leg_mtm = 0.0
        pe_leg_mtm = 0.0
        for pos in self.open_positions:
            instr = pos["instrument"]
            entry_price = pos["entry_price"]
            side = pos["side"]
            lots = pos["quantity"]
            curr_ltp = self._get_ltp_for_instrument(snapshot["option_chain"], instr)
            if entry_price is None or entry_price == 0.0:
                pos["mtm"] = 0.0
                continue
            mtm = (entry_price - curr_ltp) * lots * self.LOT_SIZE if side == "S" else (curr_ltp - entry_price) * lots * self.LOT_SIZE
            pos["mtm"] = mtm
            if instr.endswith("C"): ce_leg_mtm += mtm
            elif instr.endswith("P"): pe_leg_mtm += mtm
        total_mtm = ce_leg_mtm + pe_leg_mtm + self.realized_ce_mtm + self.realized_pe_mtm

        # 2. Stoploss/target on straddle
        if total_mtm <= -self.STOPLOSS_PER_LOT * self.MESSAGE_LOTS * self.LOT_SIZE:
            self.logger.log_exit(position, "stoploss")
            return {"reason": "stoploss", "positions": self.open_positions.copy()}
        elif total_mtm >= self.TARGET_PER_LOT * self.MESSAGE_LOTS * self.LOT_SIZE:
            self.logger.log_exit(position, "target")
            return {"reason": "target", "positions": self.open_positions.copy()}

        # 3. Rolling logic
        ce_ltp = snapshot["ce_ltp"]
        pe_ltp = snapshot["pe_ltp"]
        ce_change_pct = ((ce_ltp - self.baseline_ce_ltp)/self.baseline_ce_ltp)*100 if self.baseline_ce_ltp else 0.0
        pe_change_pct = ((pe_ltp - self.baseline_pe_ltp)/self.baseline_pe_ltp)*100 if self.baseline_pe_ltp else 0.0
        buffer_ok = abs(snapshot["spot"] - self.last_atm) >= self.BUFFER if self.last_atm else True
        hold_ok = (datetime.datetime.now() - self.last_roll_time) >= self.HOLD_TIME if self.last_roll_time else True
        atm_changed = (snapshot["atm_strike"] != self.last_atm)
        triggered_ce = abs(ce_change_pct) >= self.ROLL_PCT
        triggered_pe = abs(pe_change_pct) >= self.ROLL_PCT

        ce_should_roll = triggered_ce and buffer_ok and hold_ok and atm_changed
        pe_should_roll = triggered_pe and buffer_ok and hold_ok and atm_changed

        if ce_should_roll or pe_should_roll:
            orders = []
            if ce_should_roll:
                ce_exit_instr = self._build_option_symbol(self.last_atm, "C")
                ce_entry_instr = self._build_option_symbol(snapshot["atm_strike"], "C")
                orders.append({"action": "buy", "instrument": ce_exit_instr, "lots": self.MESSAGE_LOTS})
                orders.append({"action": "sell", "instrument": ce_entry_instr, "lots": self.MESSAGE_LOTS})
                logger.info(f"[RollingStraddle] Rolling CE: {self.last_atm} -> {snapshot['atm_strike']}, "
                           f"change={ce_change_pct:.2f}%")
                self.last_ce_action = "S"
            if pe_should_roll:
                pe_exit_instr = self._build_option_symbol(self.last_atm, "P")
                pe_entry_instr = self._build_option_symbol(snapshot["atm_strike"], "P")
                orders.append({"action": "buy", "instrument": pe_exit_instr, "lots": self.MESSAGE_LOTS})
                orders.append({"action": "sell", "instrument": pe_entry_instr, "lots": self.MESSAGE_LOTS})
                logger.info(f"[RollingStraddle] Rolling PE: {self.last_atm} -> {snapshot['atm_strike']}, "
                           f"change={pe_change_pct:.2f}%")
                self.last_pe_action = "S"
            self.last_atm = snapshot["atm_strike"]
            self.last_roll_time = datetime.datetime.now()
            self.baseline_ce_ltp = ce_ltp
            self.baseline_pe_ltp = pe_ltp
            return {"reason": "roll", "orders": orders}

        # 4. --- Dynamic OTM wings (Iron Fly logic) ---
        regime = snapshot.get("regime", "CALM")
        if self._should_have_otm_wings(snapshot, regime):
            if not self.has_otm_wings:
                otm_orders = self._add_otm_wings(snapshot)
                if otm_orders:
                    self.has_otm_wings = True
                    return {"reason": "add_otm", "orders": otm_orders}
        else:
            if self.has_otm_wings:
                otm_orders = self._remove_otm_wings(snapshot)
                if otm_orders:
                    self.has_otm_wings = False
                    return {"reason": "remove_otm", "orders": otm_orders}

        # 5. --- Auto exit OTM wings if they move too much (risk management) ---
        otm_exit_orders = []
        for otm_instr, baseline in list(self.otm_legs.items()):
            curr_ltp = self._get_ltp_for_instrument(snapshot["option_chain"], otm_instr)
            if baseline <= 0: 
                continue
            change_pct = ((curr_ltp - baseline)/baseline)*100
            if abs(change_pct) >= self.OTM_EXIT_PCT:
                pnl = (curr_ltp - baseline) * self.MESSAGE_LOTS * self.LOT_SIZE
                otm_exit_orders.append({"action": "sell", "instrument": otm_instr, "lots": self.MESSAGE_LOTS})
                logger.info(f"[RollingStraddle] OTM wing emergency exit: {otm_instr}, "
                           f"change={change_pct:.1f}%, baseline={baseline:.2f}, "
                           f"curr={curr_ltp:.2f}, pnl={pnl:.2f}")
                self.otm_legs.pop(otm_instr, None)
        if otm_exit_orders:
            return {"reason": "otm_exit", "orders": otm_exit_orders}
        return None

    def exit(self, position, exits):
        orders = []
        for pos in self.open_positions:
            orders.append({"action": "buy", "instrument": pos["instrument"], "lots": pos["quantity"]})
        for otm_instr in list(self.otm_legs.keys()):
            orders.append({"action": "sell", "instrument": otm_instr, "lots": self.MESSAGE_LOTS})
        self.open_positions = []
        self.in_position = False
        self.otm_legs.clear()
        self.has_otm_wings = False
        self.baseline_ce_ltp = None
        self.baseline_pe_ltp = None
        self.last_ce_action = None
        self.last_pe_action = None
        return orders

    def get_open_positions(self):
        """Return a copy of open positions to prevent external modification."""
        return copy.deepcopy(self.open_positions)

    # --- Helpers ---
    def _build_option_symbol(self, strike, opt_type):
        yymmdd = self.EXPIRY_DATE.replace("-", "")[2:]
        return f"{self.SYMBOL}{yymmdd}{opt_type}{int(strike)}"

    def _get_ltp_for_instrument(self, data, instrument):
        m = re.match(r"([A-Z]+)(\d{6})([CP])(\d+)", instrument)
        if not m:
            return 0.0
        _, yymmdd, opt_type, strike = m.groups()
        strike = int(strike)
        for item in data:
            s = item.get("strike_price") or item.get("strike")
            if s is None:
                continue
            try:
                if int(s) != strike:
                    continue
            except:
                continue
            if opt_type == "C":
                ce = item.get("call_options", {}).get("market_data", {})
                ltp = ce.get("ltp")
            else:
                pe = item.get("put_options", {}).get("market_data", {})
                ltp = pe.get("ltp")
            try:
                return float(ltp)
            except:
                return 0.0
        return 0.0

    def _should_have_otm_wings(self, snapshot, regime):
        """
        Determine if OTM wings should be added based on regime and IV.
        Wings provide protection during volatile or trending markets.
        """
        iv = snapshot.get("iv_estimates", 0)
        iv_rank = snapshot.get("regime_metrics", {}).get("iv_rank", 50)
        
        # Add wings in volatile or trending regimes
        if regime in ["VOLATILE", "TRENDING_UP", "TRENDING_DOWN"]:
            self.logger.log_filter("otm_decision", snapshot, 
                f"Adding OTM wings: regime={regime}, iv={iv:.2f}")
            return True
        
        # Add wings if IV is high (above 25% or IV rank > 70)
        if iv > 25 or iv_rank > 70:
            self.logger.log_filter("otm_decision", snapshot,
                f"Adding OTM wings: high IV={iv:.2f}, iv_rank={iv_rank:.1f}")
            return True
        
        return False

    def _add_otm_wings(self, snapshot):
        """
        Add OTM buy wings (Iron Fly structure) for downside protection.
        Uses robust strike selection to handle missing strikes.
        """
        atm_strike = snapshot["atm_strike"]
        spot = snapshot["spot"]
        iv = snapshot.get("iv_estimates", 15.0)
        regime = snapshot.get("regime", "CALM")
        
        # Calculate OTM distance based on IV and regime
        otm_distance = self._calculate_otm_distance(spot, iv, self.STRIKE_STEP)
        
        # Find available OTM strikes (may not be exact calculated strikes)
        otm_ce_strike = self._find_available_otm_strike(
            snapshot["option_chain"], atm_strike + otm_distance, "C", atm_strike)
        otm_pe_strike = self._find_available_otm_strike(
            snapshot["option_chain"], atm_strike - otm_distance, "P", atm_strike)
        
        otm_ce_instr = self._build_option_symbol(otm_ce_strike, "C")
        otm_pe_instr = self._build_option_symbol(otm_pe_strike, "P")
        
        # Get LTPs with defensive handling
        otm_ce_ltp = self._get_ltp_for_instrument(snapshot["option_chain"], otm_ce_instr)
        otm_pe_ltp = self._get_ltp_for_instrument(snapshot["option_chain"], otm_pe_instr)
        
        otm_orders = []
        
        # Add CE wing - attempt even if LTP==0 but log warning
        if otm_ce_instr not in self.otm_legs:
            if otm_ce_ltp == 0:
                logger.warning(f"[RollingStraddle] OTM CE {otm_ce_instr} has LTP=0, attempting order anyway")
            otm_orders.append({"action": "buy", "instrument": otm_ce_instr, "lots": self.MESSAGE_LOTS})
            self.otm_legs[otm_ce_instr] = max(otm_ce_ltp, 1.0)  # Store at least 1 for tracking
            logger.info(f"[RollingStraddle] Adding OTM CE wing: {otm_ce_instr} @ {otm_ce_ltp:.2f}, "
                       f"strike={otm_ce_strike}, regime={regime}, iv={iv:.2f}")
        
        # Add PE wing - attempt even if LTP==0 but log warning
        if otm_pe_instr not in self.otm_legs:
            if otm_pe_ltp == 0:
                logger.warning(f"[RollingStraddle] OTM PE {otm_pe_instr} has LTP=0, attempting order anyway")
            otm_orders.append({"action": "buy", "instrument": otm_pe_instr, "lots": self.MESSAGE_LOTS})
            self.otm_legs[otm_pe_instr] = max(otm_pe_ltp, 1.0)  # Store at least 1 for tracking
            logger.info(f"[RollingStraddle] Adding OTM PE wing: {otm_pe_instr} @ {otm_pe_ltp:.2f}, "
                       f"strike={otm_pe_strike}, regime={regime}, iv={iv:.2f}")
        
        return otm_orders
    
    def _find_available_otm_strike(self, option_chain, target_strike, opt_type, atm_strike):
        """
        Find the nearest available strike in option chain to target_strike.
        If exact strike not available, find closest one in the correct direction (OTM).
        
        Args:
            option_chain: List of option data from API
            target_strike: Desired strike price
            opt_type: "C" for call or "P" for put
            atm_strike: Current ATM strike for reference
            
        Returns:
            Available strike price closest to target
        """
        available_strikes = []
        
        for item in option_chain:
            strike = item.get("strike_price", item.get("strike", 0))
            if strike:
                try:
                    strike = float(strike)
                    # For calls, only consider strikes >= ATM
                    # For puts, only consider strikes <= ATM
                    if opt_type == "C" and strike >= atm_strike:
                        available_strikes.append(strike)
                    elif opt_type == "P" and strike <= atm_strike:
                        available_strikes.append(strike)
                except (ValueError, TypeError):
                    continue
        
        if not available_strikes:
            logger.warning(f"[RollingStraddle] No available {opt_type} strikes found, using target={target_strike}")
            return target_strike
        
        # Find closest available strike to target
        closest_strike = min(available_strikes, key=lambda x: abs(x - target_strike))
        
        if closest_strike != target_strike:
            logger.info(f"[RollingStraddle] Adjusted {opt_type} strike from {target_strike} to {closest_strike}")
        
        return closest_strike

    def _remove_otm_wings(self, snapshot):
        """
        Exit OTM wings - sell the bought protection.
        """
        otm_orders = []
        regime = snapshot.get("regime", "CALM")
        
        for otm_instr in list(self.otm_legs.keys()):
            entry_price = self.otm_legs[otm_instr]
            curr_ltp = self._get_ltp_for_instrument(snapshot["option_chain"], otm_instr)
            pnl = (curr_ltp - entry_price) * self.MESSAGE_LOTS * self.LOT_SIZE
            
            otm_orders.append({"action": "sell", "instrument": otm_instr, "lots": self.MESSAGE_LOTS})
            logger.info(f"[RollingStraddle] Removing OTM wing: {otm_instr}, "
                       f"entry={entry_price:.2f}, exit={curr_ltp:.2f}, pnl={pnl:.2f}, regime={regime}")
            self.otm_legs.pop(otm_instr, None)
        
        return otm_orders

    def _is_straddle_allowed(self, snapshot, regime):
        # Example: Only allow straddle entry if regime is not "VOLATILE"
        # (or whatever risk logic you want)
        if regime in ["CALM", "TRANSITION"]:
            return True
        return False

    def _calculate_otm_distance(self, spot, iv_pct, step):
        if iv_pct <= 0.0:
            iv_pct = 15.0
        distance = round(spot * iv_pct / 100.0 / step) * step
        return max(distance, step)