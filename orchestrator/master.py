from orchestrator.preprocessor import Preprocessor
from orchestrator.volatility_filter import VolatilityFilter
from orchestrator.regime_classifier import RegimeClassifier
from orchestrator.risk_manager import RiskManager
from orchestrator.execution_adapter import ExecutionAdapter
from orchestrator.logger import Logger
from strategies.rolling_straddle import RollingStraddleStrategy
import time

class MasterOrchestrator:
    def __init__(self, config):
        self.config = config
        self.pp = Preprocessor(config)
        self.vol_filter = VolatilityFilter(config)
        self.regime_classifier = RegimeClassifier(config)
        self.risk = RiskManager(config)
        self.exec = ExecutionAdapter(config)
        self.logger = Logger(config)
        
        # Single strategy to avoid race conditions
        self.strategies = [
            RollingStraddleStrategy(config, self.logger),
        ]
        
        self.POLL_INTERVAL = config['global'].get('poll_interval', 30)
        self.PRIORITY = ["rolling_straddle", "iron_fly"]

    def run(self):
        print("=" * 80)
        print("Starting Master Orchestrator Loop")
        print("=" * 80)
        
        while True:
            try:
                print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] === Tick Start ===")
                
                # 1. Get current market snapshot with OHLC and IV data
                snapshot = self.pp.get_current_snapshot()
                
                if not snapshot:
                    print("[MasterOrchestrator] Failed to get snapshot, skipping tick")
                    time.sleep(self.POLL_INTERVAL)
                    continue
                
                # 2. Update volatility filter
                self.vol_filter.update(snapshot)
                vol_ok, vol_reason = self.vol_filter.is_vol_ok(snapshot)
                print(f"[MasterOrchestrator] Vol filter: {vol_ok} - {vol_reason}")
                
                # 3. Classify market regime based on technical indicators
                regime, regime_metrics = self.regime_classifier.classify(snapshot)
                
                # 4. Attach regime info to snapshot for strategy use
                snapshot['regime'] = regime
                snapshot['regime_metrics'] = regime_metrics
                
                print(f"[MasterOrchestrator] Market Regime: {regime}")
                
                # 5. Check for new entries (only if vol is ok)
                candidates = []
                if vol_ok:
                    for strat in self.strategies:
                        can_enter, reason, params = strat.can_enter(snapshot, regime)
                        if can_enter:
                            candidates.append((strat.name, strat, params))
                            print(f"[MasterOrchestrator] {strat.name} can enter: {reason}")
                        else:
                            print(f"[MasterOrchestrator] {strat.name} cannot enter: {reason}")
                
                # Pick by priority (only one strategy at a time to avoid race conditions)
                chosen = None
                for name in self.PRIORITY:
                    for c in candidates:
                        if c[0] == name:
                            chosen = c
                            break
                    if chosen: 
                        break
                
                # Execute entry if chosen
                if chosen:
                    strat_name, strat, params = chosen
                    print(f"[MasterOrchestrator] Chosen strategy: {strat_name}")
                    
                    sizing = self.risk.compute_size(strat_name, snapshot)
                    params.update(sizing)
                    params['regime'] = regime  # Pass regime to strategy
                    
                    orders = strat.enter(snapshot, params)
                    success, resp = self.exec.send_orders(orders, tag=f"{strat_name}-{int(time.time())}")
                    self.logger.log_entry(strat_name, snapshot, params, orders, resp)
                    
                    if success:
                        print(f"[MasterOrchestrator] Entry successful for {strat_name}")
                    else:
                        print(f"[MasterOrchestrator] Entry failed for {strat_name}: {resp}")
                
                # 6. Manage open positions for all strategies
                for strat in self.strategies:
                    open_positions = strat.get_open_positions()
                    
                    if not open_positions:
                        continue
                    
                    print(f"[MasterOrchestrator] Managing {len(open_positions)} positions for {strat.name}")
                    
                    # Call on_tick for each position (in practice, strategy manages all together)
                    for pos in open_positions:
                        exits = strat.on_tick(snapshot, pos)
                        
                        if exits:
                            reason = exits.get('reason', 'unknown')
                            orders = exits.get('orders', [])
                            positions_to_exit = exits.get('positions', [])
                            
                            print(f"[MasterOrchestrator] Exit signal from {strat.name}: {reason}")
                            
                            # For stop/target, exit completely
                            if reason in ['stoploss', 'target']:
                                exit_orders = strat.exit(pos, exits)
                                if exit_orders:
                                    success, resp = self.exec.send_orders(exit_orders, 
                                        tag=f"exit-{reason}-{pos.get('instrument', '')}")
                                    self.logger.log_exit(pos, exits)
                                    print(f"[MasterOrchestrator] Exit complete: {reason}")
                            
                            # For roll/adjust, send orders directly
                            elif orders:
                                success, resp = self.exec.send_orders(orders, 
                                    tag=f"{reason}-{int(time.time())}")
                                print(f"[MasterOrchestrator] Order execution: {reason}, success={success}")
                        
                        # Only process first position (strategy handles all together)
                        break
                
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] === Tick End ===\n")
                
            except Exception as e:
                print(f"[MasterOrchestrator] ERROR in main loop: {e}")
                import traceback
                traceback.print_exc()
            
            time.sleep(self.POLL_INTERVAL)