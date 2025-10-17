class RiskManager:
    def __init__(self, config):
        self.config = config

    def compute_size(self, strategy_name, snapshot):
        # TODO: Add proper sizing logic
        return {"lot_size": 1, "max_loss": 10000, "take_profit": 10000}