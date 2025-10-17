#!/usr/bin/env python3
# Multi-% straddle roll + IV-adaptive Iron Fly monitor (SENSEX)
# ▪️ Robust ATM straddle rolling logic
# ▪️ Dynamic OTM Iron Fly wings (IV-adaptive, auto-exit/roll)
# ▪️ Per-leg, realized, and total MTM
# ▪️ Stoploss/target, logging, and retry loops
# ▪️ Designed for Upstox API and production deployment

import requests
import datetime
import time
from copy import deepcopy
from typing import Dict, Any, Optional, List
import re

# ---------------------------- CONFIG ----------------------------
STRATEGIES = [
    (5.0,  "https://orders.algotest.in/webhook/tv/tk-trade?token=pLgMGjDyTluW1JkS4hbuN1HYCqGMmElv&tag=68ef57bd748015596dd2cb6a"),
    # Add more (pct, webhook_url) tuples for more strategies
]

SYMBOL = "SENSEX"
MESSAGE_LOTS = 1
STRIKE_STEP = 100
LOT_SIZE = 20
START_TIME = datetime.time(9, 59)
EXIT_TIME = datetime.time(15, 29)
BUFFER = 10
HOLD_TIME = datetime.timedelta(minutes=1)
REFRESH_INTERVAL = 10
STOPLOSS_PER_LOT = 3000
TARGET_PER_LOT = 10000

ACCESS_TOKEN = "your_access_token_here"  # Set in production
EXPIRY_DATE = "2025-10-16"
UPSTOX_URL = "https://api.upstox.com/v2/option/chain"
UPSTOX_PARAMS_BASE = {"instrument_key": "BSE_INDEX|SENSEX"}
UPSTOX_HEADERS_TEMPLATE = {"Accept": "application/json", "Authorization": f"Bearer {ACCESS_TOKEN}"}

IV_WING_FACTOR = 1.0     # Multiplier for OTM wing distance
OTM_EXIT_PCT = 25.0      # Exit OTM wing if LTP moves ±X% from entry

# ---------------------------- UTILITIES ----------------------------
def nowstr() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")

def expiry_to_yymmdd(raw_expiry: str) -> str:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(raw_expiry, fmt)
            return dt.strftime("%y%m%d")
        except:
            continue
    try:
        dt = datetime.datetime.fromisoformat(raw_expiry)
        return dt.strftime("%y%m%d")
    except:
        return raw_expiry

def build_option_symbol(symbol: str, expiry_raw: str, strike: int, opt_type: str) -> str:
    yymmdd = expiry_to_yymmdd(expiry_raw)
    strike_str = str(int(strike))
    return f"{symbol}{yymmdd}{opt_type}{strike_str}"

def send_plain_to_url(url: str, payload: str, label: str = ""):
    try:
        headers = {"Content-Type": "text/plain"}
        resp = requests.post(url, data=payload, headers=headers, timeout=15)
        print(f"[{nowstr()}] 🔹 {label} | {payload} | URL={url.split('?')[0]} | Status={resp.status_code}")
        return (resp.status_code == 200, resp.status_code, resp.text)
    except Exception as e:
        print(f"[{nowstr()}] ❌ {label} payload error: {e} | URL={url.split('?')[0]}")
        return (False, None, str(e))

def get_ltp_for_instrument(data, instrument):
    m = re.match(r"([A-Z]+)(\d{6})([CP])(\d+)", instrument)
    if not m:
        return 0.0
    _, yymmdd, opt_type, strike = m.groups()
    strike = int(strike)
    for item in data:
        s = item.get("strike_price") or item.get("strike")
        if s is None: continue
        try:
            if int(s) != strike: continue
        except: continue
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

def get_atm_iv(item: dict) -> float:
    try:
        ce_iv = float(item.get("call_options", {}).get("market_data", {}).get("implied_volatility", 0.0))
        pe_iv = float(item.get("put_options", {}).get("market_data", {}).get("implied_volatility", 0.0))
        return (ce_iv + pe_iv) / 2.0
    except:
        return 0.0

def calculate_otm_distance(spot: float, iv_pct: float, step: int) -> int:
    distance = int(round(spot * iv_pct / 100.0 * IV_WING_FACTOR / step)) * step
    return max(distance, step)

def pick_atm_from_chain(data) -> (float, Optional[int], Optional[dict]):
    best_item = None
    min_diff = float("inf")
    spot_price = 0.0
    for item in data:
        strike = item.get("strike_price") or item.get("strike")
        if strike is None: continue
        try:
            ce_val = item.get("call_options", {}).get("market_data", {}).get("ltp")
            pe_val = item.get("put_options", {}).get("market_data", {}).get("ltp")
            ce_ltp = float(ce_val) if ce_val not in (None, "") else 0.0
            pe_ltp = float(pe_val) if pe_val not in (None, "") else 0.0
        except Exception:
            ce_ltp = pe_ltp = 0.0
        try:
            spot_price = float(item.get("underlying_spot_price", spot_price or 0))
        except Exception: pass
        if ce_ltp <= 0.0 or pe_ltp <= 0.0: continue
        diff = abs(ce_ltp - pe_ltp)
        if diff < min_diff:
            min_diff = diff
            best_item = item
    if best_item is not None:
        strike_val = best_item.get("strike_price") or best_item.get("strike")
        try:
            return spot_price, int(strike_val), best_item
        except Exception:
            return spot_price, strike_val, best_item
    return spot_price, None, None

def get_option_chain_from_upstox(expiry_date: str):
    params = deepcopy(UPSTOX_PARAMS_BASE)
    params["expiry_date"] = expiry_date
    headers = deepcopy(UPSTOX_HEADERS_TEMPLATE)
    try:
        resp = requests.get(UPSTOX_URL, params=params, headers=headers, timeout=10)
    except Exception as e:
        print(f"[{nowstr()}] ⚠️ Upstox connection error: {e}")
        return []
    if resp.status_code != 200:
        print(f"[{nowstr()}] ⚠️ Upstox returned {resp.status_code}: {resp.text}")
        return []
    payload = resp.json()
    return payload.get("data", payload)

# ---------------------------- STRATEGY CONTEXT ----------------------------
class StrategyContext:
    def __init__(self, trigger_pct: float, webhook_url: str):
        self.trigger_pct = trigger_pct
        self.webhook_url = webhook_url
        self.last_atm: Optional[int] = None
        self.last_roll_time: Optional[datetime.datetime] = None
        self.in_position: bool = False
        self.baseline_ce_ltp: Optional[float] = None
        self.baseline_pe_ltp: Optional[float] = None
        self.last_ce_action: Optional[str] = None
        self.last_pe_action: Optional[str] = None
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.realized_ce_mtm = 0.0
        self.realized_pe_mtm = 0.0
        self.otm_legs: Dict[str, float] = {}

    def log(self, msg: str):
        print(f"[{nowstr()}] [PCT={self.trigger_pct:g}%] {msg}")

    def build_entry_message(self, atm_strike: int, lots: int = MESSAGE_LOTS) -> str:
        ce = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "C")
        pe = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "P")
        return f"{ce} sell {lots}, {pe} sell {lots}"

    def build_single_entry(self, instrument: str, lots: int = MESSAGE_LOTS) -> str:
        return f"{instrument} sell {lots}"

    def build_single_exit(self, instrument: str, lots: int = MESSAGE_LOTS) -> str:
        return f"{instrument} buy {lots}"

    def exit_instrument_until_success(self, instrument: str, entry_price_for_mtm: Optional[float] = None, label: str = "exit"):
        payload = self.build_single_exit(instrument)
        attempt = 1
        while True:
            ok, status, text = send_plain_to_url(self.webhook_url, payload, f"{label} (attempt {attempt})")
            if ok:
                if instrument in self.positions and entry_price_for_mtm is not None:
                    try:
                        ent = self.positions[instrument].get("entry_price", 0.0)
                        qty = self.positions[instrument].get("quantity", 1)
                        side = self.positions[instrument].get("side", "S")
                        realized = (ent - entry_price_for_mtm) * qty * LOT_SIZE if side=="S" else (entry_price_for_mtm - ent) * qty * LOT_SIZE
                        if instrument.endswith("C"):
                            self.realized_ce_mtm += realized
                        elif instrument.endswith("P"):
                            self.realized_pe_mtm += realized
                        self.positions[instrument]["mtm"] = realized
                    except Exception:
                        self.positions[instrument]["mtm"] = 0.0
                self.positions.pop(instrument, None)
                self.otm_legs.pop(instrument, None)
                if instrument.endswith("C"):
                    self.last_ce_action = "B"
                elif instrument.endswith("P"):
                    self.last_pe_action = "B"
                return True
            self.log(f"❌ {label} failed (attempt {attempt}), retrying in 3s...")
            time.sleep(3)
            attempt += 1

    def enter_instrument_until_success(self, instrument: str, entry_price: Optional[float] = None, label: str = "entry"):
        if instrument in self.positions and self.positions[instrument].get("side") == "S":
            self.log(f"⚠️ Skipping duplicate sell for {instrument} (already short locally).")
            return True
        payload = self.build_single_entry(instrument)
        attempt = 1
        while True:
            ok, status, text = send_plain_to_url(self.webhook_url, payload, f"{label} (attempt {attempt})")
            if ok:
                self.positions[instrument] = {"side": "S", "entry_price": entry_price if entry_price is not None else 0.0, "quantity": MESSAGE_LOTS, "mtm": 0.0}
                if instrument.endswith("C"):
                    self.last_ce_action = "S"
                elif instrument.endswith("P"):
                    self.last_pe_action = "S"
                return True
            self.log(f"❌ {label} failed (attempt {attempt}), retrying in 3s...")
            time.sleep(3)
            attempt += 1

    def enter_straddle_until_success(self, atm_strike: int, ce_entry_price: Optional[float] = None, pe_entry_price: Optional[float] = None, lots: int = MESSAGE_LOTS):
        payload = self.build_entry_message(atm_strike, lots)
        attempt = 1
        while True:
            ok, status, text = send_plain_to_url(self.webhook_url, payload, f"entry-straddle (attempt {attempt})")
            if ok:
                ce_instr = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "C")
                pe_instr = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "P")
                self.positions[ce_instr] = {"side": "S", "entry_price": ce_entry_price if ce_entry_price is not None else 0.0, "quantity": lots, "mtm": 0.0}
                self.positions[pe_instr] = {"side": "S", "entry_price": pe_entry_price if pe_entry_price is not None else 0.0, "quantity": lots, "mtm": 0.0}
                self.last_ce_action = "S"
                self.last_pe_action = "S"
                return True
            self.log(f"❌ entry-straddle failed (attempt {attempt}), retrying in 3s...")
            time.sleep(3)
            attempt += 1

# ---------------------------- MAIN LOOP ----------------------------
def main_loop(strategies_config: List[StrategyContext]):
    NEAREST_EXPIRY = EXPIRY_DATE
    MAX_JUMP = 2 * STRIKE_STEP

    print(f"[{nowstr()}] 🚀 Starting multi-pct SENSEX straddle + IV-adaptive Iron Fly. Active PCTs: {', '.join(str(ctx.trigger_pct)+'%' for ctx in strategies_config)}")

    try:
        while True:
            now = datetime.datetime.now()
            if now.time() < START_TIME:
                time.sleep(10)
                continue
            if now.time() >= EXIT_TIME:
                for ctx in strategies_config:
                    if ctx.positions:
                        ctx.log("🛑 Exiting all positions at EOD...")
                        for instr in list(ctx.positions.keys()):
                            ctx.exit_instrument_until_success(instr, label="exit-eod")
                        ctx.in_position = False
                        ctx.baseline_ce_ltp = ctx.baseline_pe_ltp = None
                        ctx.last_ce_action = ctx.last_pe_action = None
                        ctx.otm_legs.clear()
                print(f"[{nowstr()}] ✅ Market closed. Exiting loop.")
                break

            data = get_option_chain_from_upstox(NEAREST_EXPIRY)
            if not data:
                print(f"[{nowstr()}] ⚠️ No option chain received, retrying in {REFRESH_INTERVAL}s")
                time.sleep(REFRESH_INTERVAL)
                continue

            for ctx in strategies_config:
                try:
                    spot, atm_strike, atm_item = pick_atm_from_chain(data)
                    if atm_strike is None:
                        ctx.log("⚠️ No ATM found, skipping cycle.")
                        continue

                    if ctx.last_atm and abs(atm_strike - ctx.last_atm) > MAX_JUMP:
                        ctx.log(f"⚠️ Abnormal ATM jump detected: {ctx.last_atm} → {atm_strike} (ignored)")
                        atm_strike = ctx.last_atm

                    # ATM LTPs
                    ce_md = atm_item.get("call_options", {}).get("market_data", {}) or {}
                    pe_md = atm_item.get("put_options", {}).get("market_data", {}) or {}
                    ce_ltp = float(ce_md.get("ltp") or 0.0)
                    pe_ltp = float(pe_md.get("ltp") or 0.0)

                    ctx.log(f"📊 Spot={spot} | ATM={atm_strike} | CE={ce_ltp} | PE={pe_ltp}")

                    # ---- IV-adaptive Iron Fly wings ----
                    atm_iv = get_atm_iv(atm_item)
                    wing_distance = calculate_otm_distance(spot, atm_iv, STRIKE_STEP)
                    otm_ce_strike = atm_strike + wing_distance
                    otm_pe_strike = atm_strike - wing_distance
                    otm_ce_instr = build_option_symbol(SYMBOL, NEAREST_EXPIRY, otm_ce_strike, "C")
                    otm_pe_instr = build_option_symbol(SYMBOL, NEAREST_EXPIRY, otm_pe_strike, "P")

                    # ---- Initial straddle entry ----
                    if not ctx.in_position:
                        if ce_ltp > 0 and pe_ltp > 0:
                            ctx.log("🚀 Entering initial ATM straddle...")
                            ctx.enter_straddle_until_success(atm_strike, ce_entry_price=ce_ltp, pe_entry_price=pe_ltp, lots=MESSAGE_LOTS)
                            ctx.last_atm = atm_strike
                            ctx.last_roll_time = now
                            ctx.in_position = True
                            ctx.baseline_ce_ltp = ce_ltp
                            ctx.baseline_pe_ltp = pe_ltp

                            # Enter Iron Fly wings
                            for otm_instr in [otm_ce_instr, otm_pe_instr]:
                                if otm_instr not in ctx.positions:
                                    ltp = get_ltp_for_instrument(data, otm_instr)
                                    if ltp > 0:
                                        ctx.enter_instrument_until_success(otm_instr, entry_price=ltp, label="entry-OTM")
                                        ctx.otm_legs[otm_instr] = ltp
                                        ctx.log(f"✅ OTM wing entered: {otm_instr} @ LTP={ltp}")
                            continue
                        else:
                            ctx.log(f"⏳ Waiting for valid ATM LTPs before entering...")
                            continue

                    # ---- Calculate MTM for all positions ----
                    total_mtm = 0.0
                    for instr, pos in list(ctx.positions.items()):
                        entry_price = pos.get("entry_price", 0.0)
                        side = pos.get("side", "S")
                        lots = pos.get("quantity", MESSAGE_LOTS)
                        curr_ltp = get_ltp_for_instrument(data, instr)
                        if side == "S":
                            mtm = (entry_price - curr_ltp) * lots * LOT_SIZE
                        else:
                            mtm = (curr_ltp - entry_price) * lots * LOT_SIZE
                        pos["mtm"] = mtm
                        total_mtm += mtm
                        ctx.log(f"Leg {instr}: entry={entry_price}, ltp={curr_ltp}, MTM={mtm:.2f}")

                    ctx.log(f"💰 Total MTM={total_mtm:.2f}")

                    # ---- Stoploss / Target ----
                    if total_mtm <= -STOPLOSS_PER_LOT * MESSAGE_LOTS * LOT_SIZE:
                        ctx.log(f"⚠️ Stoploss hit. Exiting all positions...")
                        for instr in list(ctx.positions.keys()):
                            ltp = get_ltp_for_instrument(data, instr)
                            ctx.exit_instrument_until_success(instr, entry_price_for_mtm=ltp, label="stoploss")
                        ctx.in_position = False
                        ctx.baseline_ce_ltp = ctx.baseline_pe_ltp = None
                        ctx.otm_legs.clear()
                        continue
                    elif total_mtm >= TARGET_PER_LOT * MESSAGE_LOTS * LOT_SIZE:
                        ctx.log(f"🎯 Target hit. Exiting all positions...")
                        for instr in list(ctx.positions.keys()):
                            ltp = get_ltp_for_instrument(data, instr)
                            ctx.exit_instrument_until_success(instr, entry_price_for_mtm=ltp, label="target")
                        ctx.in_position = False
                        ctx.baseline_ce_ltp = ctx.baseline_pe_ltp = None
                        ctx.otm_legs.clear()
                        continue

                    # ---- OTM wing exit logic ----
                    for otm_instr, baseline in list(ctx.otm_legs.items()):
                        curr_ltp = get_ltp_for_instrument(data, otm_instr)
                        if baseline <= 0: continue
                        change_pct = ((curr_ltp - baseline)/baseline)*100
                        if abs(change_pct) >= OTM_EXIT_PCT:
                            ctx.log(f"🔄 OTM wing {otm_instr} moved {change_pct:+.2f}%, exiting...")
                            ctx.exit_instrument_until_success(otm_instr, entry_price_for_mtm=curr_ltp, label="exit-OTM")
                            ctx.otm_legs.pop(otm_instr, None)
                            # (Optional: re-enter new OTM wing if you want continuous Iron Fly wings)

                    # ---- Rolling logic ----
                    ce_change_pct = ((ce_ltp - ctx.baseline_ce_ltp)/ctx.baseline_ce_ltp)*100 if ctx.baseline_ce_ltp else 0.0
                    pe_change_pct = ((pe_ltp - ctx.baseline_pe_ltp)/ctx.baseline_pe_ltp)*100 if ctx.baseline_pe_ltp else 0.0
                    buffer_ok = abs(spot - ctx.last_atm) >= BUFFER if ctx.last_atm else True
                    hold_ok = (now - ctx.last_roll_time) >= HOLD_TIME if ctx.last_roll_time else True
                    atm_changed = (atm_strike != ctx.last_atm)
                    triggered_ce = abs(ce_change_pct) >= ctx.trigger_pct
                    triggered_pe = abs(pe_change_pct) >= ctx.trigger_pct

                    ce_should_roll = triggered_ce and buffer_ok and hold_ok and atm_changed
                    pe_should_roll = triggered_pe and buffer_ok and hold_ok and atm_changed

                    if ce_should_roll:
                        ce_exit_instr = build_option_symbol(SYMBOL, NEAREST_EXPIRY, ctx.last_atm, "C")
                        ce_entry_instr = build_option_symbol(SYMBOL, NEAREST_EXPIRY, atm_strike, "C")
                        if ce_exit_instr in ctx.positions:
                            ltp_exit = get_ltp_for_instrument(data, ce_exit_instr)
                            ctx.exit_instrument_until_success(ce_exit_instr, entry_price_for_mtm=ltp_exit, label="roll-CE")
                        ctx.enter_instrument_until_success(ce_entry_instr, entry_price=ce_ltp, label="roll-CE")
                        ctx.last_ce_action = "S"
                        ctx.log("✅ CE rolled.")

                    if pe_should_roll:
                        pe_exit_instr = build_option_symbol(SYMBOL, NEAREST_EXPIRY, ctx.last_atm, "P")
                        pe_entry_instr = build_option_symbol(SYMBOL, NEAREST_EXPIRY, atm_strike, "P")
                        if pe_exit_instr in ctx.positions:
                            ltp_exit = get_ltp_for_instrument(data, pe_exit_instr)
                            ctx.exit_instrument_until_success(pe_exit_instr, entry_price_for_mtm=ltp_exit, label="roll-PE")
                        ctx.enter_instrument_until_success(pe_entry_instr, entry_price=pe_ltp, label="roll-PE")
                        ctx.last_pe_action = "S"
                        ctx.log("✅ PE rolled.")

                    # ---- Update baseline ----
                    ctx.baseline_ce_ltp = ce_ltp
                    ctx.baseline_pe_ltp = pe_ltp
                    ctx.last_atm = atm_strike
                    ctx.last_roll_time = now

                except Exception as e:
                    ctx.log(f"⚠️ Strategy error: {e}")
                    continue

            time.sleep(REFRESH_INTERVAL)

    except KeyboardInterrupt:
        print(f"[{nowstr()}] ⚠️ KeyboardInterrupt detected — shutting down...")
        for ctx in strategies_config:
            try:
                for instr in list(ctx.positions.keys()):
                    ctx.exit_instrument_until_success(instr, label="Manual Exit")
            except Exception as e:
                ctx.log(f"⚠️ Could not auto-exit positions: {e}")
        print(f"[{nowstr()}] ✅ Graceful shutdown complete.")

# ---------------------------- ENTRY POINT ----------------------------
if __name__ == "__main__":
    active_contexts = [StrategyContext(pct, url) for pct, url in STRATEGIES]
    main_loop(active_contexts)