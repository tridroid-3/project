import os
import csv
from datetime import datetime

class Logger:
    def __init__(self, config):
        self.log_dir = config.get('log_dir', 'data/logs/')
        os.makedirs(self.log_dir, exist_ok=True)
        self.trade_log_path = os.path.join(self.log_dir, "trades.csv")
        if not os.path.exists(self.trade_log_path):
            with open(self.trade_log_path, "w", newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "strategy", "event_type", "spot", "atm", "ce_ltp", "pe_ltp",
                    "total_premium", "dte", "regime", "action", "size", "order_ids", "fill_prices", "pnl_after", "notes"
                ])

    def log_entry(self, strategy, snapshot, params, orders, resp):
        with open(self.trade_log_path, "a", newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.utcnow().isoformat(), strategy, "entry",
                snapshot.get('spot'), snapshot.get('atm_strike'),
                snapshot.get('ce_ltp'), snapshot.get('pe_ltp'),
                snapshot.get('total_premium'), snapshot.get('dte_days'),
                params.get('regime'), "enter", params.get('lot_size'),
                orders, resp, "", ""
            ])

    def log_exit(self, pos, exits):
        pass

    def log_filter(self, filter_type, snapshot, reason):
        pass