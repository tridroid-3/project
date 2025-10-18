import os
import csv
import json
import logging
from datetime import datetime

# Configure structured JSON logging
class JSONFormatter(logging.Formatter):
    """Custom formatter to output logs as JSON."""
    
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add extra fields if present
        if hasattr(record, 'extra'):
            log_data.update(record.extra)
        
        return json.dumps(log_data)

class Logger:
    def __init__(self, config):
        self.log_dir = config.get('log_dir', 'data/logs/')
        os.makedirs(self.log_dir, exist_ok=True)
        
        # CSV trade log (for backward compatibility)
        self.trade_log_path = os.path.join(self.log_dir, "trades.csv")
        if not os.path.exists(self.trade_log_path):
            with open(self.trade_log_path, "w", newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "strategy", "event_type", "spot", "atm", "ce_ltp", "pe_ltp",
                    "total_premium", "dte", "regime", "action", "size", "order_ids", "fill_prices", "pnl_after", "notes"
                ])
        
        # JSON structured log
        self.json_log_path = os.path.join(self.log_dir, "trades.jsonl")
        
        # Setup structured logging
        self.setup_structured_logging()
    
    def setup_structured_logging(self):
        """Setup structured JSON logging handlers."""
        # Get or create logger
        self.structured_logger = logging.getLogger("trading")
        self.structured_logger.setLevel(logging.INFO)
        
        # File handler for JSON logs
        json_handler = logging.FileHandler(self.json_log_path)
        json_handler.setLevel(logging.INFO)
        json_handler.setFormatter(JSONFormatter())
        
        # Console handler for human-readable logs
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        
        # Add handlers if not already added
        if not self.structured_logger.handlers:
            self.structured_logger.addHandler(json_handler)
            self.structured_logger.addHandler(console_handler)

    def log_entry(self, strategy, snapshot, params, orders, resp):
        # CSV log (backward compatibility)
        with open(self.trade_log_path, "a", newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.utcnow().isoformat(), strategy, "entry",
                snapshot.get('spot'), snapshot.get('atm_strike'),
                snapshot.get('ce_ltp'), snapshot.get('pe_ltp'),
                snapshot.get('total_premium'), snapshot.get('dte_days'),
                params.get('regime'), "enter", params.get('lot_size'),
                json.dumps(orders), json.dumps(resp), "", ""
            ])
        
        # Structured JSON log
        log_data = {
            "event_type": "entry",
            "strategy": strategy,
            "spot": snapshot.get('spot'),
            "atm_strike": snapshot.get('atm_strike'),
            "ce_ltp": snapshot.get('ce_ltp'),
            "pe_ltp": snapshot.get('pe_ltp'),
            "total_premium": snapshot.get('total_premium'),
            "dte_days": snapshot.get('dte_days'),
            "regime": params.get('regime'),
            "lot_size": params.get('lot_size'),
            "orders": orders,
            "response": resp
        }
        self.structured_logger.info(f"Strategy entry: {strategy}", extra={"extra": log_data})

    def log_exit(self, pos, exits):
        """Log position exit."""
        log_data = {
            "event_type": "exit",
            "position": pos,
            "exits": exits
        }
        self.structured_logger.info(f"Position exit", extra={"extra": log_data})
    
    def log_action(self, strategy, action_type, orders):
        """Log strategy action (roll, add_otm, etc.)."""
        log_data = {
            "event_type": "action",
            "strategy": strategy,
            "action_type": action_type,
            "orders": orders
        }
        self.structured_logger.info(f"Strategy action: {action_type}", extra={"extra": log_data})

    def log_filter(self, filter_type, snapshot, reason):
        """Log filter decision."""
        log_data = {
            "event_type": "filter",
            "filter_type": filter_type,
            "reason": reason,
            "spot": snapshot.get('spot'),
            "regime": snapshot.get('regime')
        }
        self.structured_logger.info(f"Filter: {filter_type}", extra={"extra": log_data})