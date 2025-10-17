class VolatilityFilter:
    def __init__(self, config):
        self.alpha = config['vol_filter']['alpha']
        self.sigma_factor = config['vol_filter']['sigma_factor']

    def update(self, snapshot):
        pass  # TODO: Update EWMA etc.

    def is_vol_ok(self, snapshot):
        # TODO: Use EWMA, DTE, IV etc.
        return True, "stub"