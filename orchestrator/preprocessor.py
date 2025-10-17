import requests
import pandas as pd
from collections import deque
from datetime import datetime, timedelta

class Preprocessor:
    def __init__(self, config):
        self.config = config
        self.upstox_url = config['upstox']['url']
        self.access_token = config['upstox']['access_token']
        self.expiry_date = config['upstox']['expiry_date']
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}"
        }
        self.instrument_key = config.get('upstox', {}).get('instrument_key', "BSE_INDEX|SENSEX")
        self.candles_url = config.get('upstox', {}).get('candles_url', 
            "https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}")
        
        # Store historical IV data for IV rank calculation
        self.iv_history = deque(maxlen=config.get('regime', {}).get('iv_history_length', 50))
        
    def get_current_snapshot(self):
        """
        Fetches current market snapshot with OHLC data and IV series.
        Returns dict with: spot, atm_strike, ce_ltp, pe_ltp, total_premium, 
        dte_days, iv_estimates, option_chain, ohlc_df, iv_series
        """
        params = {"instrument_key": self.instrument_key, "expiry_date": self.expiry_date}
        try:
            # Fetch option chain
            resp = requests.get(self.upstox_url, params=params, headers=self.headers, timeout=10)
            if resp.status_code != 200:
                print(f"[Preprocessor] Upstox API error: {resp.status_code} {resp.text[:200]}")
                return None
            
            response_data = resp.json()
            data = response_data.get("data", [])
            
            if not data:
                print(f"[Preprocessor] Empty option chain data received")
                return None
            
            # Extract spot price - handle different response formats
            spot = 0.0
            if isinstance(data, list) and len(data) > 0:
                # Try multiple possible keys for spot price
                first_item = data[0]
                spot = float(first_item.get("underlying_spot_price", 
                           first_item.get("underlying_value",
                           first_item.get("spot_price", 0))))
            
            if spot == 0.0:
                print(f"[Preprocessor] Warning: Could not extract spot price from response")
                
            # Find ATM strike (closest to spot)
            try:
                atm_strike = min(data, 
                    key=lambda x: abs(float(x.get("strike_price", x.get("strike", 0))) - spot)
                ).get("strike_price", None) or min(data, 
                    key=lambda x: abs(float(x.get("strike_price", x.get("strike", 0))) - spot)
                ).get("strike", 0)
            except (ValueError, TypeError) as e:
                print(f"[Preprocessor] Error finding ATM strike: {e}")
                atm_strike = round(spot / 100) * 100  # Fallback to rounded spot
            
            # Get ATM row
            atm_row = next((item for item in data 
                          if item.get("strike_price", item.get("strike")) == atm_strike), {})
            
            # Extract CE/PE LTP with defensive parsing
            ce_ltp = self._safe_get_ltp(atm_row, "call_options", "CE")
            pe_ltp = self._safe_get_ltp(atm_row, "put_options", "PE")
            
            total_premium = float(ce_ltp or 0) + float(pe_ltp or 0)
            
            # Calculate DTE
            dte_days = self._calculate_dte(self.expiry_date)
            
            # Extract IV estimates
            ce_iv = self._safe_get_iv(atm_row, "call_options")
            pe_iv = self._safe_get_iv(atm_row, "put_options")
            iv_estimates = (ce_iv + pe_iv) / 2.0 if (ce_iv > 0 and pe_iv > 0) else max(ce_iv, pe_iv)
            
            # Store IV in history
            if iv_estimates > 0:
                self.iv_history.append(iv_estimates)
            
            # Fetch OHLC data
            ohlc_df = self._fetch_ohlc_data()
            
            # Build IV series from history
            iv_series = pd.Series(list(self.iv_history)) if len(self.iv_history) > 0 else pd.Series([iv_estimates])
            
            print(f"[Preprocessor] Snapshot: spot={spot:.2f}, atm={atm_strike}, "
                  f"ce_ltp={ce_ltp:.2f}, pe_ltp={pe_ltp:.2f}, iv={iv_estimates:.2f}%, "
                  f"dte={dte_days:.2f}, ohlc_rows={len(ohlc_df)}, iv_hist={len(self.iv_history)}")
            
            return {
                "spot": spot,
                "atm_strike": atm_strike,
                "ce_ltp": ce_ltp,
                "pe_ltp": pe_ltp,
                "total_premium": total_premium,
                "dte_days": dte_days,
                "iv_estimates": iv_estimates,
                "option_chain": data,
                "ohlc_df": ohlc_df,
                "iv_series": iv_series
            }
        except Exception as e:
            print(f"[Preprocessor] Exception fetching option chain: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _safe_get_ltp(self, row, option_key, opt_type):
        """Safely extract LTP from option data with multiple fallback paths"""
        try:
            option_data = row.get(option_key, {})
            market_data = option_data.get("market_data", option_data)
            ltp = market_data.get("ltp", market_data.get("last_price", 0))
            return float(ltp) if ltp else 0.0
        except (TypeError, ValueError, AttributeError) as e:
            print(f"[Preprocessor] Warning: Could not extract {opt_type} LTP: {e}")
            return 0.0
    
    def _safe_get_iv(self, row, option_key):
        """Safely extract IV from option data"""
        try:
            option_data = row.get(option_key, {})
            market_data = option_data.get("market_data", option_data)
            iv = market_data.get("implied_volatility", market_data.get("iv", 0))
            return float(iv) if iv else 0.0
        except (TypeError, ValueError, AttributeError):
            return 0.0
    
    def _calculate_dte(self, expiry_str):
        """Calculate days to expiry from expiry date string"""
        try:
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d")
            now = datetime.now()
            dte = (expiry - now).total_seconds() / 86400.0
            return max(dte, 0.0)
        except Exception as e:
            print(f"[Preprocessor] Warning: Could not calculate DTE: {e}")
            return 3.0  # Default fallback
    
    def _fetch_ohlc_data(self):
        """
        Fetch historical OHLC candle data from Upstox API.
        Returns pandas DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            # Build candles URL - fetch last 50 daily candles for regime analysis
            to_date = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
            interval = "day"  # Can be configurable: day, 30minute, etc.
            
            url = self.candles_url.format(
                instrument_key=self.instrument_key,
                interval=interval,
                to_date=to_date,
                from_date=from_date
            )
            
            resp = requests.get(url, headers=self.headers, timeout=10)
            
            if resp.status_code != 200:
                print(f"[Preprocessor] Candles API error: {resp.status_code} {resp.text[:200]}")
                return self._create_empty_ohlc_df()
            
            candles_data = resp.json().get("data", {}).get("candles", [])
            
            if not candles_data:
                print(f"[Preprocessor] No candle data received, using empty DataFrame")
                return self._create_empty_ohlc_df()
            
            # Parse candles - Upstox format: [timestamp, open, high, low, close, volume, oi]
            df = pd.DataFrame(candles_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()
            
            # Convert to numeric
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df = df.dropna()
            print(f"[Preprocessor] Fetched {len(df)} OHLC candles")
            return df
            
        except Exception as e:
            print(f"[Preprocessor] Exception fetching OHLC data: {e}")
            import traceback
            traceback.print_exc()
            return self._create_empty_ohlc_df()
    
    def _create_empty_ohlc_df(self):
        """Create an empty OHLC DataFrame with proper structure"""
        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])