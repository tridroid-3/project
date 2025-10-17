class BaseStrategy:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.name = "base_strategy"

    def can_enter(self, snapshot, regime):
        return False, "Not implemented", {}

    def enter(self, snapshot, params):
        raise NotImplementedError

    def on_tick(self, snapshot, position):
        return None

    def exit(self, position, exits):
        return []

    def get_open_positions(self):
        return []