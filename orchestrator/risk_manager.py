import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, config):
        self.config = config
        
        # Risk limits
        global_config = config.get('global', {})
        self.max_daily_loss = global_config.get('max_daily_loss', 0.03)  # 3% default
        self.account_equity = global_config.get('account_equity', 1000000)
        self.max_open_exposure = global_config.get('max_open_exposure', 0.10)  # 10% default
        
        # Daily tracking
        self.daily_pnl = {}  # date -> pnl
        self.daily_trades = {}  # date -> trade count
        self.current_exposure = 0.0
        
        # Emergency state
        self.emergency_mode = False
        self.daily_limit_breached = False
    
    def reset_daily_tracking(self):
        """Reset daily tracking for a new trading day."""
        today = date.today()
        if today not in self.daily_pnl:
            self.daily_pnl[today] = 0.0
            self.daily_trades[today] = 0
            self.daily_limit_breached = False
            logger.info(f"Daily tracking reset for {today}")
    
    def update_pnl(self, pnl):
        """Update daily PnL."""
        today = date.today()
        self.daily_pnl[today] = self.daily_pnl.get(today, 0.0) + pnl
        
        # Check if daily loss limit breached
        daily_loss_pct = abs(self.daily_pnl[today]) / self.account_equity
        if self.daily_pnl[today] < 0 and daily_loss_pct >= self.max_daily_loss:
            self.daily_limit_breached = True
            logger.critical(f"🔴 Daily loss limit breached! Loss: {self.daily_pnl[today]:.2f} "
                          f"({daily_loss_pct*100:.2f}% of equity)")
            return True
        return False
    
    def check_daily_loss_limit(self):
        """Check if daily loss limit has been breached."""
        if self.daily_limit_breached:
            logger.error("Trading halted: daily loss limit breached")
            return False
        
        today = date.today()
        daily_pnl = self.daily_pnl.get(today, 0.0)
        daily_loss_pct = abs(daily_pnl) / self.account_equity
        
        if daily_pnl < 0 and daily_loss_pct >= self.max_daily_loss:
            self.daily_limit_breached = True
            logger.critical(f"🔴 Daily loss limit breached! Loss: {daily_pnl:.2f} "
                          f"({daily_loss_pct*100:.2f}% of equity)")
            return False
        
        return True
    
    def check_exposure_limit(self, proposed_exposure):
        """Check if adding proposed exposure would breach limits."""
        total_exposure = self.current_exposure + proposed_exposure
        exposure_pct = total_exposure / self.account_equity
        
        if exposure_pct > self.max_open_exposure:
            logger.warning(f"Exposure limit would be breached: {exposure_pct*100:.2f}% > "
                         f"{self.max_open_exposure*100:.2f}%")
            return False
        
        return True
    
    def update_exposure(self, delta):
        """Update current exposure."""
        self.current_exposure += delta
        logger.info(f"Current exposure: {self.current_exposure:.2f} "
                   f"({self.current_exposure/self.account_equity*100:.2f}% of equity)")
    
    def check_margin_requirement(self, orders, snapshot):
        """
        Check if account has sufficient margin for orders.
        
        WARNING: This is a simplified placeholder implementation that uses estimated premiums.
        For production use, this should be enhanced with:
        1. Actual LTP lookup from snapshot data
        2. Real margin requirements from broker API
        3. Account margin status queries
        
        Args:
            orders: List of order dicts
            snapshot: Market snapshot with option chain data
            
        Returns:
            bool: True if margin appears sufficient (always True in this placeholder)
        """
        # TODO: Implement actual margin calculation with real LTP values
        # For now, log a warning and return True to not block trades
        logger.debug("Margin check: Using simplified placeholder logic. "
                    "Implement actual margin calculation for production.")
        
        # Estimate required margin (simplified)
        total_premium = 0.0
        for order in orders:
            # Placeholder: In production, look up actual LTP from snapshot
            estimated_premium = 100  # This should be actual LTP
            lots = order.get('lots', 1)
            lot_size = self.config.get('LOT_SIZE', 20)
            total_premium += estimated_premium * lots * lot_size
        
        # Check if we have enough buffer (assume 20% of equity as available margin)
        available_margin = self.account_equity * 0.20
        required_margin = total_premium * 0.10  # Assume 10% margin requirement
        
        if required_margin > available_margin:
            logger.warning(f"Estimated margin may be insufficient: required~{required_margin:.2f}, "
                         f"available~{available_margin:.2f}")
            # Return True anyway since this is just an estimate
            # In production, this should return False with actual calculations
        
        return True
    
    def enter_emergency_mode(self, reason):
        """Enter emergency mode - halt all trading."""
        self.emergency_mode = True
        logger.critical(f"🚨 EMERGENCY MODE ACTIVATED: {reason}")
    
    def is_emergency_mode(self):
        """Check if in emergency mode."""
        return self.emergency_mode

    def compute_size(self, strategy_name, snapshot):
        """
        Compute position size with risk checks.
        
        Returns:
            dict with lot_size, max_loss, take_profit, or None if risk checks fail
        """
        # Reset daily tracking if needed
        self.reset_daily_tracking()
        
        # Check emergency mode
        if self.is_emergency_mode():
            logger.error("Cannot enter trade: emergency mode active")
            return None
        
        # Check daily loss limit
        if not self.check_daily_loss_limit():
            logger.error("Cannot enter trade: daily loss limit breached")
            return None
        
        # Get strategy config
        strategy_config = self.config.get(strategy_name, {})
        stoploss_per_lot = strategy_config.get('stoploss_per_lot', 3000)
        target_per_lot = strategy_config.get('target_per_lot', 10000)
        
        # Compute lot size (simplified - could be more sophisticated)
        lot_size = self.config.get('MESSAGE_LOTS', 1)
        
        # Check exposure limit
        estimated_exposure = stoploss_per_lot * lot_size
        if not self.check_exposure_limit(estimated_exposure):
            logger.error("Cannot enter trade: exposure limit would be breached")
            return None
        
        return {
            "lot_size": lot_size,
            "max_loss": stoploss_per_lot * lot_size,
            "take_profit": target_per_lot * lot_size
        }