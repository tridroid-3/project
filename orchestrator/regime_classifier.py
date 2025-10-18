import pandas as pd
import numpy as np

class RegimeClassifier:
    """
    Classifies market regime based on technical indicators:
    - ATR (Average True Range): volatility measure
    - ADX (Average Directional Index): trend strength
    - Bollinger Band Width: volatility bands
    - SMA Slope: trend direction
    - IV Rank: implied volatility percentile
    """
    
    def __init__(self, config):
        self.config = config
        regime_config = config.get('regime', {})
        
        # Indicator periods
        self.atr_period = regime_config.get('atr_period', 14)
        self.adx_period = regime_config.get('adx_period', 14)
        self.bb_period = regime_config.get('bb_period', 20)
        self.bb_std = regime_config.get('bb_std', 2.0)
        self.sma_period = regime_config.get('sma_period', 20)
        self.sma_lookback = regime_config.get('sma_lookback', 5)
        
        # Thresholds for regime classification
        self.atr_high_threshold = regime_config.get('atr_high_threshold', 2.0)  # % of close
        self.adx_trending_threshold = regime_config.get('adx_trending_threshold', 25)
        self.adx_strong_threshold = regime_config.get('adx_strong_threshold', 40)
        self.bb_width_high_threshold = regime_config.get('bb_width_high_threshold', 0.05)  # 5%
        self.sma_slope_threshold = regime_config.get('sma_slope_threshold', 0.5)  # % change
        self.iv_rank_high = regime_config.get('iv_rank_high', 70)
        self.iv_rank_low = regime_config.get('iv_rank_low', 30)

    def classify(self, snapshot):
        """
        Classify market regime based on snapshot data.
        
        Returns:
            dict: {"regime": regime_label, "regime_metrics": metrics_dict}
            
        Regime labels:
            - CALM: Low volatility, no strong trend
            - VOLATILE: High volatility, unpredictable moves
            - TRENDING_UP: Strong upward trend
            - TRENDING_DOWN: Strong downward trend
            - TRANSITION: Mixed signals, changing regime
        """
        if not snapshot:
            return {"regime": "UNKNOWN", "regime_metrics": {}}
        
        ohlc_df = snapshot.get('ohlc_df')
        iv_series = snapshot.get('iv_series')
        
        # Calculate metrics
        metrics = {}
        
        # Calculate ATR - use .iloc[-1] and handle NaN safely
        if ohlc_df is not None and len(ohlc_df) >= self.atr_period:
            atr_value = self._calculate_atr(ohlc_df)
            close_value = ohlc_df['close'].iloc[-1]
            # Handle NaN values
            if pd.isna(atr_value) or pd.isna(close_value) or close_value == 0:
                metrics['atr'] = 0
                metrics['atr_pct'] = 0
            else:
                metrics['atr'] = atr_value
                metrics['atr_pct'] = (atr_value / close_value * 100)
        else:
            metrics['atr'] = 0
            metrics['atr_pct'] = 0
        
        # Calculate ADX - use .iloc[-1] and handle NaN
        if ohlc_df is not None and len(ohlc_df) >= self.adx_period + 1:
            adx, plus_di, minus_di = self._calculate_adx(ohlc_df)
            metrics['adx'] = 0 if pd.isna(adx) else adx
            metrics['plus_di'] = 0 if pd.isna(plus_di) else plus_di
            metrics['minus_di'] = 0 if pd.isna(minus_di) else minus_di
        else:
            metrics['adx'] = 0
            metrics['plus_di'] = 0
            metrics['minus_di'] = 0
        
        # Calculate Bollinger Band Width - use .iloc[-1] and handle NaN
        if ohlc_df is not None and len(ohlc_df) >= self.bb_period:
            bb_width = self._calculate_bb_width(ohlc_df)
            metrics['bb_width'] = 0 if pd.isna(bb_width) else bb_width
        else:
            metrics['bb_width'] = 0
        
        # Calculate SMA Slope - use .iloc[-1] and handle NaN
        if ohlc_df is not None and len(ohlc_df) >= self.sma_period + self.sma_lookback:
            sma_slope = self._calculate_sma_slope(ohlc_df)
            metrics['sma_slope'] = 0 if pd.isna(sma_slope) else sma_slope
        else:
            metrics['sma_slope'] = 0
        
        # Calculate IV Rank - use .iloc[-1] and handle NaN
        if iv_series is not None and len(iv_series) >= 10:
            iv_rank = self._calculate_iv_rank(iv_series)
            metrics['iv_rank'] = 50 if pd.isna(iv_rank) else iv_rank
        else:
            metrics['iv_rank'] = 50  # Neutral default
        
        # Current IV - handle NaN
        current_iv = snapshot.get('iv_estimates', 0)
        metrics['current_iv'] = 0 if pd.isna(current_iv) else current_iv
        
        # Classify regime based on metrics
        regime = self._classify_regime(metrics)
        
        print(f"[RegimeClassifier] Regime={regime}, ATR={metrics['atr_pct']:.2f}%, "
              f"ADX={metrics['adx']:.1f}, BB_Width={metrics['bb_width']:.3f}, "
              f"SMA_Slope={metrics['sma_slope']:.2f}%, IV_Rank={metrics['iv_rank']:.1f}")
        
        return {"regime": regime, "regime_metrics": metrics}
    
    def _calculate_atr(self, df):
        """Calculate Average True Range"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR as exponential moving average
        atr = tr.ewm(span=self.atr_period, adjust=False).mean().iloc[-1]
        return atr
    
    def _calculate_adx(self, df):
        """Calculate Average Directional Index (ADX) and DI+/DI-"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate +DM and -DM
        up_move = high.diff()
        down_move = -low.diff()
        
        plus_dm = pd.Series(0.0, index=df.index)
        minus_dm = pd.Series(0.0, index=df.index)
        
        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
        
        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Smooth using Wilder's method
        atr = tr.ewm(span=self.adx_period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=self.adx_period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(span=self.adx_period, adjust=False).mean() / atr)
        
        # Calculate DX and ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.ewm(span=self.adx_period, adjust=False).mean().iloc[-1]
        
        return adx, plus_di.iloc[-1], minus_di.iloc[-1]
    
    def _calculate_bb_width(self, df):
        """Calculate Bollinger Band Width as percentage of middle band"""
        close = df['close']
        sma = close.rolling(window=self.bb_period).mean()
        std = close.rolling(window=self.bb_period).std()
        
        upper_band = sma + (self.bb_std * std)
        lower_band = sma - (self.bb_std * std)
        
        bb_width = ((upper_band - lower_band) / sma).iloc[-1]
        return bb_width
    
    def _calculate_sma_slope(self, df):
        """Calculate SMA slope as percentage change over lookback period"""
        close = df['close']
        sma = close.rolling(window=self.sma_period).mean()
        
        if len(sma) < self.sma_lookback:
            return 0.0
        
        current_sma = sma.iloc[-1]
        past_sma = sma.iloc[-self.sma_lookback]
        
        slope_pct = ((current_sma - past_sma) / past_sma * 100) if past_sma != 0 else 0.0
        return slope_pct
    
    def _calculate_iv_rank(self, iv_series):
        """Calculate IV Rank: where current IV stands in historical range (0-100)"""
        if len(iv_series) == 0:
            return 50.0
        
        # Use .iloc[-1] for latest value and handle NaN
        current_iv = iv_series.iloc[-1]
        if pd.isna(current_iv):
            return 50.0
        
        # Filter out NaN values before min/max
        valid_series = iv_series.dropna()
        if len(valid_series) == 0:
            return 50.0
        
        iv_min = valid_series.min()
        iv_max = valid_series.max()
        
        if iv_max == iv_min or pd.isna(iv_min) or pd.isna(iv_max):
            return 50.0
        
        iv_rank = ((current_iv - iv_min) / (iv_max - iv_min)) * 100
        return iv_rank
    
    def _classify_regime(self, metrics):
        """
        Classify regime based on calculated metrics.
        
        Logic:
        - VOLATILE: High ATR + High BB Width + High IV Rank
        - TRENDING_UP: High ADX + Positive SMA Slope + Plus DI > Minus DI
        - TRENDING_DOWN: High ADX + Negative SMA Slope + Minus DI > Plus DI
        - CALM: Low ATR + Low BB Width + Low/Medium IV Rank
        - TRANSITION: Mixed or unclear signals
        """
        atr_pct = metrics.get('atr_pct', 0)
        adx = metrics.get('adx', 0)
        bb_width = metrics.get('bb_width', 0)
        sma_slope = metrics.get('sma_slope', 0)
        iv_rank = metrics.get('iv_rank', 50)
        plus_di = metrics.get('plus_di', 0)
        minus_di = metrics.get('minus_di', 0)
        
        # High volatility conditions
        high_volatility = (atr_pct > self.atr_high_threshold or 
                          bb_width > self.bb_width_high_threshold or 
                          iv_rank > self.iv_rank_high)
        
        # Strong trend conditions
        strong_trend = adx > self.adx_strong_threshold
        moderate_trend = adx > self.adx_trending_threshold
        
        # Trend direction
        uptrend = sma_slope > self.sma_slope_threshold and plus_di > minus_di
        downtrend = sma_slope < -self.sma_slope_threshold and minus_di > plus_di
        
        # Low volatility conditions
        low_volatility = (atr_pct < self.atr_high_threshold / 2 and 
                         bb_width < self.bb_width_high_threshold / 2 and 
                         iv_rank < self.iv_rank_low)
        
        # Classification logic
        if high_volatility and not strong_trend:
            return "VOLATILE"
        elif strong_trend and uptrend:
            return "TRENDING_UP"
        elif strong_trend and downtrend:
            return "TRENDING_DOWN"
        elif low_volatility and not moderate_trend:
            return "CALM"
        else:
            return "TRANSITION"