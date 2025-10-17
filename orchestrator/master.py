from orchestrator.preprocessor import Preprocessor
from orchestrator.volatility_filter import VolatilityFilter
from orchestrator.regime_classifier import RegimeClassifier
from orchestrator.risk_manager import RiskManager
from orchestrator.execution_adapter import ExecutionAdapter
from orchestrator.logger import Logger
from strategies.rolling_straddle import RollingStraddleStrategy
from strategies.rolling_straddle import RollingStraddleStrategy
import time

class MasterOrchestrator:
    def __init__(self, config):
        self.config = config
        self.pp = Preprocessor(config)
        self.vol_filter = VolatilityFilter(config)
        self.regime = RegimeClassifier(config)
        self.risk = RiskManager(config)
        self.exec = ExecutionAdapter(config)
        self.logger = Logger(config)
        self.strategies = [
            RollingStraddleStrategy(config, self.logger),
            
        ]
        self.POLL_INTERVAL = config['global'].get('poll_interval', 30)
        self.PRIORITY = ["rolling_straddle", "iron_fly"]

    def run(self):
        print("Starting orchestrator loop...")
        while True:
            snapshot = self.pp.get_current_snapshot()
            self.vol_filter.update(snapshot)
            vol_ok, vol_reason = self.vol_filter.is_vol_ok(snapshot)
            regime = self.regime.classify(snapshot)
            candidates = []
            if vol_ok:
                for strat in self.strategies:
                    can_enter, reason, params = strat.can_enter(snapshot, regime)
                    if can_enter:
                        candidates.append((strat.name, strat, params))
            # Pick by priority
            chosen = None
            for name in self.PRIORITY:
                for c in candidates:
                    if c[0] == name:
                        chosen = c
                        break
                if chosen: break
            if chosen:
                strat_name, strat, params = chosen
                sizing = self.risk.compute_size(strat_name, snapshot)
                params.update(sizing)
                orders = strat.enter(snapshot, params)
                success, resp = self.exec.send_orders(orders, tag=f"{strat_name}-{int(time.time())}")
                self.logger.log_entry(strat_name, snapshot, params, orders, resp)
            # Manage open positions
            for strat in self.strategies:
                for pos in strat.get_open_positions():
                    exits = strat.on_tick(snapshot, pos)
                    if exits:
                        orders = strat.exit(pos, exits)
                        self.exec.send_orders(orders, tag=f"exit-{pos.get('id', '')}")
                        self.logger.log_exit(pos, exits)
            time.sleep(self.POLL_INTERVAL)