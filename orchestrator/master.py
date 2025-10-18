#!/usr/bin/env python3
"""
Master Orchestrator — v2.1-orchestrator-master (Stable)
SENSEX / Index-focused orchestrator.
Maintains all original logic; includes safety, tag, and volatility improvements.
"""

import time
import logging
import secrets
from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from orchestrator.preprocessor import Preprocessor
from orchestrator.regime_classifier import RegimeClassifier
from orchestrator.execution_adapter import ExecutionAdapter
from orchestrator.logger import Logger
from orchestrator.risk_manager import RiskManager
from orchestrator.volatility_filter import VolatilityFilter
from strategies.rolling_straddle import RollingStraddleStrategy

logger = logging.getLogger(__name__)


class MasterOrchestrator:
    def __init__(self, config):
        self.config = config
        self.pp = Preprocessor(config)
        self.regime = RegimeClassifier(config)
        self.exec = ExecutionAdapter(config)
        self.logger = Logger(config)
        self.risk = RiskManager(config)
        self.vol_filter = VolatilityFilter(config)

        # Main strategy — handles ATM straddle + OTM wings internally (SENSEX-focused)
        self.strategies = [RollingStraddleStrategy(config, self.logger)]
        self.POLL_INTERVAL = config.get("global", {}).get("poll_interval", 30)
        self.PRIORITY = ["rolling_straddle"]

        # Timezone setup
        try:
            tz_str = config.get("global", {}).get("timezone", "Asia/Kolkata")
            self.timezone = ZoneInfo(tz_str)
        except Exception:
            logger.warning("Invalid timezone, defaulting to Asia/Kolkata")
            self.timezone = ZoneInfo("Asia/Kolkata")

        # EOD schedule setup
        self.eod_schedule = config.get("global", {}).get("eod_exit_schedule", [])
        self._processed_schedule = []
        for idx, item in enumerate(self.eod_schedule):
            tstr = item.get("time")
            try:
                hh, mm, ss = (list(map(int, tstr.split(":"))) + [0, 0, 0])[:3]
                t = dt_time(hh, mm, ss)
            except Exception:
                logger.warning(f"Invalid EOD schedule time format: {tstr}")
                continue
            self._processed_schedule.append(
                {
                    "id": idx,
                    "time": t,
                    "pct": item.get("pct", None),
                    "final": bool(item.get("final", False)),
                }
            )
        self._executed_for_date = {}

        exec_cfg = config.get("execution", {}) or {}
        self.exit_retry_count = exec_cfg.get("max_retries", 3)
        self.exit_retry_delay = exec_cfg.get("retry_delay", 1)

    # -------------------- INTERNAL HELPERS --------------------

    def _should_run_schedule_entry(self, sched_item, now_dt):
        today = now_dt.date()
        executed_ids = self._executed_for_date.get(today, set())
        if sched_item["id"] in executed_ids:
            return False
        scheduled_dt = datetime.combine(today, sched_item["time"], tzinfo=self.timezone)
        if now_dt < scheduled_dt:
            return False
        return True

    def _mark_schedule_executed(self, sched_item, now_dt):
        today = now_dt.date()
        executed_ids = self._executed_for_date.setdefault(today, set())
        executed_ids.add(sched_item["id"])
        keep = {today, today - timedelta(days=1)}
        for d in list(self._executed_for_date.keys()):
            if d not in keep:
                del self._executed_for_date[d]

    def _generate_tag(self):
        """Generate AlgoTest-compliant 24-hex tag."""
        return secrets.token_hex(12)

    def _perform_eod_exit(self, tag_prefix="exit-eod"):
        logger.info(f"Performing EOD exit: {tag_prefix}")

        for strat in self.strategies:
            open_positions = strat.get_open_positions()
            if not open_positions:
                logger.info(f"No open positions for {strat.name}")
                continue

            try:
                # safer call with open positions passed
                orders = strat.exit(None, open_positions)
            except Exception as e:
                logger.error(f"Error building exit orders for {strat.name}: {e}")
                continue

            if not orders:
                logger.warning(f"No exit orders generated for {strat.name}")
                continue

            any_ok, results = self.exec.send_orders(
                orders, tag=f"{tag_prefix}-{self._generate_tag()}"
            )
            self._log_order_results(results, tag_prefix=tag_prefix)
            time.sleep(0.2)

    def _log_order_results(self, results, tag_prefix="", attempt=1):
        now_tz = datetime.now(self.timezone)
        ts = now_tz.strftime("%H:%M:%S")
        for r in results or []:
            order = r.get("order", {})
            action = order.get("action", "")
            instr = order.get("instrument", "")
            lots = order.get("lots", order.get("quantity", 1))
            url = getattr(self.exec, "webhook_url", None) or "N/A"
            status = r.get("status")
            simulated = r.get("simulated", False)
            order_id = r.get("order_id", "N/A")
            status_str = status if status is not None else ("SIMULATED" if simulated else "ERR")
            logger.info(
                f"[{ts}] 🔹 {tag_prefix} | {instr} {action} {lots} | OrderID={order_id} | Status={status_str}"
            )

    # -------------------- MAIN LOOP --------------------

    def run(self):
        logger.info("Starting orchestrator loop...")
        while True:
            try:
                now = datetime.now(self.timezone)

                # ---- EMERGENCY EXIT ----
                if self.risk.is_emergency_mode():
                    logger.critical("System in emergency mode - performing emergency close")
                    self._perform_eod_exit(tag_prefix="emergency-exit")
                    logger.critical("Emergency close complete - stopping orchestrator")
                    return

                # ---- EOD EXIT CHECK ----
                for sched in self._processed_schedule:
                    if self._should_run_schedule_entry(sched, now):
                        pct = sched.get("pct")
                        ts = now.strftime("%H:%M:%S")
                        pct_str = f"[PCT={pct}%]" if pct is not None else ""
                        logger.info(f"[{ts}] {pct_str} 🛑 Exit time reached. Closing positions...")
                        self._perform_eod_exit(tag_prefix="exit-eod")
                        self._mark_schedule_executed(sched, now)
                        if sched.get("final"):
                            logger.info(f"[{ts}] ✅ Market closed. Exiting orchestrator.")
                            return

                # ---- NORMAL POLL ----
                snapshot = self.pp.get_current_snapshot()
                if snapshot is None:
                    time.sleep(self.POLL_INTERVAL)
                    continue

                # ---- REGIME + VOLATILITY ----
                regime_info = self.regime.classify(snapshot)
                snapshot.update(regime_info)

                try:
                    self.vol_filter.update(snapshot)
                    vol_ok, vol_reason = self.vol_filter.is_vol_ok(snapshot)
                except Exception as e:
                    logger.warning(f"Volatility filter error: {e}")
                    vol_ok, vol_reason = True, "vol_filter error or not configured"

                logger.info(
                    f"[{now.strftime('%H:%M:%S')}] Regime={snapshot.get('regime')} | VolOK={vol_ok} ({vol_reason})"
                )

                # ---- ENTRY LOGIC ----
                any_in_position = any(getattr(s, "in_position", False) for s in self.strategies)
                candidates = []

                if not self.risk.check_daily_loss_limit():
                    logger.warning("Daily loss limit breached - no new entries allowed")
                    vol_ok = False

                if vol_ok and not any_in_position:
                    for strat in self.strategies:
                        can_enter, reason, params = strat.can_enter(snapshot, snapshot.get("regime"))
                        if can_enter:
                            candidates.append((strat.name, strat, params))

                chosen = None
                for name in self.PRIORITY:
                    for c in candidates:
                        if c[0] == name:
                            chosen = c
                            break
                    if chosen:
                        break

                if chosen:
                    strat_name, strat, params = chosen
                    sizing = self.risk.compute_size(strat_name, snapshot)
                    if sizing is None:
                        logger.warning(f"Risk check failed for {strat_name} - skipping entry")
                    else:
                        params.update(sizing)
                        orders = strat.enter(snapshot, params)

                        if not self.risk.check_margin_requirement(orders, snapshot):
                            logger.error("Insufficient margin - skipping entry")
                        else:
                            success, resp = self.exec.send_orders(
                                orders, tag=f"{strat_name}-{self._generate_tag()}"
                            )
                            self._log_order_results(
                                resp if isinstance(resp, list) else (resp or []),
                                tag_prefix=strat_name,
                            )
                            self.logger.log_entry(strat_name, snapshot, params, orders, resp)

                # ---- POSITION MANAGEMENT ----
                for strat in self.strategies:
                    positions_copy = strat.get_open_positions()
                    for pos in positions_copy:
                        action = strat.on_tick(snapshot, pos)
                        if not action:
                            continue
                        reason = action.get("reason")
                        orders = action.get("orders", [])

                        if reason == "roll":
                            any_ok, results = self.exec.send_orders(
                                orders, tag=f"roll-{self._generate_tag()}"
                            )
                            self._log_order_results(results, tag_prefix="roll")
                            self.logger.log_action(strat.name, "roll", orders)

                        elif reason in ("add_otm", "remove_otm", "otm_exit"):
                            any_ok, results = self.exec.send_orders(
                                orders, tag=f"{reason}-{self._generate_tag()}"
                            )
                            self._log_order_results(results, tag_prefix=reason)
                            self.logger.log_action(strat.name, reason, orders)

                        elif reason in ("stoploss", "target"):
                            exit_orders = strat.exit(pos, action.get("positions") or [])
                            any_ok, results = self.exec.send_orders(
                                exit_orders, tag=f"exit-{self._generate_tag()}"
                            )
                            self._log_order_results(results, tag_prefix="exit")
                            self.logger.log_exit(pos, action)

                            mtm = pos.get("mtm", 0.0)
                            if self.risk.update_pnl(mtm):
                                logger.critical(
                                    "Daily loss limit breached during trade - entering emergency mode"
                                )
                                self.risk.enter_emergency_mode("Daily loss limit exceeded")

                time.sleep(self.POLL_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Orchestrator stopped by user.")
                break
            except Exception as e:
                logger.error(f"Orchestrator loop exception: {e}", exc_info=True)
                time.sleep(self.POLL_INTERVAL)
