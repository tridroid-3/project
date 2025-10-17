import requests

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
        self.instrument_key = "BSE_INDEX|SENSEX"

    def get_current_snapshot(self):
        params = {"instrument_key": self.instrument_key, "expiry_date": self.expiry_date}
        try:
            resp = requests.get(self.upstox_url, params=params, headers=self.headers, timeout=10)
            if resp.status_code != 200:
                print(f"Upstox API error: {resp.status_code} {resp.text}")
                return None
            data = resp.json().get("data", [])
            # --- Find spot, ATM, etc. ---
            # This part depends on the Upstox response format!
            spot = float(data[0].get("underlying_spot_price", 0)) if data else 0
            # Find ATM strike (closest to spot)
            atm_strike = min(data, key=lambda x: abs(float(x.get("strike_price", 0)) - spot)).get("strike_price", 0)
            # Use ATM row to get CE/PE LTP
            atm_row = next((item for item in data if item.get("strike_price") == atm_strike), {})
            ce_ltp = atm_row.get("call_options", {}).get("market_data", {}).get("ltp", 0)
            pe_ltp = atm_row.get("put_options", {}).get("market_data", {}).get("ltp", 0)
            total_premium = float(ce_ltp or 0) + float(pe_ltp or 0)
            dte_days = 3  # You may need to compute days-to-expiry
            iv_estimates = (atm_row.get("call_options", {}).get("market_data", {}).get("implied_volatility", 0) +
                            atm_row.get("put_options", {}).get("market_data", {}).get("implied_volatility", 0)) / 2
            return {
                "spot": spot,
                "atm_strike": atm_strike,
                "ce_ltp": ce_ltp,
                "pe_ltp": pe_ltp,
                "total_premium": total_premium,
                "dte_days": dte_days,
                "iv_estimates": iv_estimates,
                "option_chain": data
            }
        except Exception as e:
            print(f"Exception fetching option chain: {e}")
            return None