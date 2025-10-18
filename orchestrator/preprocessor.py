import requests
import pandas as pd
from collections import deque
from datetime import datetime
import time

class Preprocessor:
    """
    Fetches option chain, spot, candles, and builds snapshot for strategies and regime classifier.
    Configure endpoints and tokens in config/config.yaml (see config.example).
    """
    def __init__(self, config):
        self.config = config or {}
        up = self.config.get("upstox", {})
        self.access_token = up.get("access_token")
        self.option_chain_url = up.get("option_chain_url", up.get("url"))
        self.quote_url = up.get("quote_url")
        self.candles_url = up.get("candles_url")
        self.instrument_key = up.get("instrument_key") or up.get("symbol") or "SENSEX"
        self.expiry_date = up.get("expiry_date")
        self.headers = {"Accept": "application/json"}
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"

        iv_period = (self.config.get("regime_classifier") or {}).get("iv_period", 30)
        self.iv_period = max(5, int(iv_period))
        self._iv_history = deque(maxlen=self.iv_period)
        self._iv_time = deque(maxlen=self.iv_period)

        self.candles_interval = up.get("candles_interval", "5m")
        self.candles_lookback = up.get("candles_lookback", 200)

    def get_current_snapshot(self):
        chain = self._fetch_option_chain()
        spot = self._extract_spot_from_chain(chain) or self._fetch_spot_from_quote()
        atm_strike, ce_ltp, pe_ltp = self._extract_atm_and_ltps(chain, spot)
        dte_days = self._compute_dte_days(self.expiry_date) if self.expiry_date else None
        iv_est = self._extract_atm_iv(chain, atm_strike)
        if iv_est is not None:
            self._iv_history.append(float(iv_est))
            self._iv_time.append(datetime.utcnow())
        iv_series = None
        if len(self._iv_history):
            iv_series = pd.Series(list(self._iv_history), index=list(self._iv_time)).astype(float)
        ohlc_df = self._fetch_ohlc_df()
        snapshot = {
            "spot": float(spot) if spot is not None else 0.0,
            "atm_strike": int(atm_strike) if atm_strike is not None else 0,
            "ce_ltp": float(ce_ltp) if ce_ltp is not None else 0.0,
            "pe_ltp": float(pe_ltp) if pe_ltp is not None else 0.0,
            "total_premium": (float(ce_ltp or 0) + float(pe_ltp or 0)),
            "dte_days": float(dte_days) if dte_days is not None else None,
            "iv_estimates": float(iv_series.iloc[-1]) if iv_series is not None and not iv_series.empty else (float(iv_est) if iv_est is not None else None),
            "option_chain": chain,
            "ohlc_df": ohlc_df,
            "iv_series": iv_series
        }
        print("[Preprocessor] snapshot:", {
            "spot": snapshot["spot"],
            "atm": snapshot["atm_strike"],
            "ce_ltp": snapshot["ce_ltp"],
            "pe_ltp": snapshot["pe_ltp"],
            "iv": snapshot["iv_estimates"],
            "dte": snapshot["dte_days"]
        })
        return snapshot

    # ---- helpers ----
    def _fetch_option_chain(self):
        if not self.option_chain_url:
            print("[Preprocessor] No option_chain_url configured.")
            return []
        params = {"instrument_key": self.instrument_key}
        if self.expiry_date:
            params['expiry_date'] = self.expiry_date
        try:
            resp = requests.get(self.option_chain_url, headers=self.headers, params=params, timeout=8)
            if resp.status_code != 200:
                print(f"[Preprocessor] option_chain fetch status {resp.status_code}: {resp.text}")
                return []
            data = resp.json()
            if isinstance(data, dict) and 'data' in data:
                return data['data'] or []
            if isinstance(data, list):
                return data
            return data
        except Exception as e:
            print("[Preprocessor] exception fetching option_chain:", e)
            return []

    def _fetch_spot_from_quote(self):
        if not self.quote_url:
            return None
        params = {"instrument_key": self.instrument_key}
        try:
            resp = requests.get(self.quote_url, headers=self.headers, params=params, timeout=5)
            if resp.status_code != 200:
                print(f"[Preprocessor] quote fetch status {resp.status_code}: {resp.text}")
                return None
            j = resp.json()
            if isinstance(j, dict):
                candidates = []
                if 'data' in j and isinstance(j['data'], dict):
                    candidates.append(j['data'].get('last_price'))
                    candidates.append(j['data'].get('ltp'))
                candidates.append(j.get('last_price') or j.get('ltp') or j.get('lastPrice'))
                for c in candidates:
                    if c is not None:
                        try:
                            return float(c)
                        except:
                            continue
            return None
        except Exception as e:
            print("[Preprocessor] exception fetching quote:", e)
            return None

    def _fetch_ohlc_df(self):
        if not self.candles_url:
            return pd.DataFrame()
        params = {
            "instrument_key": self.instrument_key,
            "interval": self.candles_interval,
            "limit": self.candles_lookback
        }
        try:
            resp = requests.get(self.candles_url, headers=self.headers, params=params, timeout=8)
            if resp.status_code != 200:
                print(f"[Preprocessor] candles fetch status {resp.status_code}: {resp.text}")
                return pd.DataFrame()
            j = resp.json()
            candle_list = None
            if isinstance(j, dict):
                if 'data' in j and isinstance(j['data'], list):
                    candle_list = j['data']
                elif 'candles' in j:
                    candle_list = j['candles']
            elif isinstance(j, list):
                candle_list = j
            if not candle_list:
                return pd.DataFrame()
            records = []
            for row in candle_list:
                if isinstance(row, dict):
                    ts = row.get('timestamp') or row.get('time') or row.get('datetime') or row.get('date')
                    o = row.get('open') or row.get('o')
                    h = row.get('high') or row.get('h')
                    l = row.get('low') or row.get('l')
                    c = row.get('close') or row.get('c')
                elif isinstance(row, (list, tuple)):
                    if len(row) >= 5:
                        ts, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
                    else:
                        continue
                else:
                    continue
                try:
                    if isinstance(ts, (int, float)):
                        tsdt = datetime.utcfromtimestamp(int(ts))
                    else:
                        tsdt = pd.to_datetime(ts)
                    records.append({"datetime": tsdt, "open": float(o), "high": float(h), "low": float(l), "close": float(c)})
                except Exception:
                    continue
            if not records:
                return pd.DataFrame()
            df = pd.DataFrame.from_records(records).set_index('datetime').sort_index()
            return df
        except Exception as e:
            print("[Preprocessor] exception fetching candles:", e)
            return pd.DataFrame()

    def _extract_spot_from_chain(self, chain):
        if not chain:
            return None
        if isinstance(chain, dict):
            for key in ('underlying_spot_price', 'underlyingValue', 'underlying_spot', 'spot'):
                v = chain.get(key)
                if v is not None:
                    try:
                        return float(v)
                    except:
                        pass
            meta = chain.get('meta') or chain.get('metadata') or {}
            if isinstance(meta, dict):
                for k in ('underlying_spot_price', 'underlyingValue', 'spot'):
                    if k in meta:
                        try:
                            return float(meta[k])
                        except:
                            pass
        if isinstance(chain, list):
            for item in chain:
                for key in ('underlying_spot_price', 'underlyingValue', 'spot'):
                    v = item.get(key)
                    if v is not None:
                        try:
                            return float(v)
                        except:
                            pass
        return None

    def _extract_atm_and_ltps(self, chain, spot):
        if not chain:
            return None, 0.0, 0.0
        strikes = []
        strike_map = {}
        for item in chain:
            s = item.get('strike_price') or item.get('strike')
            if s is None:
                continue
            try:
                s_int = int(s)
            except:
                continue
            strikes.append(s_int)
            strike_map[s_int] = item
        if not strikes:
            return None, 0.0, 0.0
        if spot:
            atm = min(strikes, key=lambda x: abs(x - spot))
        else:
            atm = sorted(strikes)[len(strikes)//2]
        item = strike_map.get(atm, {})
        ce_ltp = 0.0
        pe_ltp = 0.0
        try:
            ce = item.get('call_options') or item.get('CE') or {}
            pe = item.get('put_options') or item.get('PE') or {}
            if isinstance(ce, dict):
                md = ce.get('market_data') or ce
                ce_ltp = md.get('ltp') or md.get('last_traded_price') or md.get('last_price') or 0.0
            if isinstance(pe, dict):
                md = pe.get('market_data') or pe
                pe_ltp = md.get('ltp') or md.get('last_traded_price') or md.get('last_price') or 0.0
            ce_ltp = float(ce_ltp or 0.0)
            pe_ltp = float(pe_ltp or 0.0)
        except Exception:
            ce_ltp = 0.0
            pe_ltp = 0.0
        return atm, ce_ltp, pe_ltp

    def _extract_atm_iv(self, chain, atm_strike):
        if not chain or atm_strike is None:
            return None
        for item in chain:
            s = item.get('strike_price') or item.get('strike')
            try:
                if int(s) == int(atm_strike):
                    ce = item.get('call_options') or item.get('CE') or {}
                    pe = item.get('put_options') or item.get('PE') or {}
                    ce_iv = None
                    pe_iv = None
                    if isinstance(ce, dict):
                        md = ce.get('market_data') or ce
                        ce_iv = md.get('implied_volatility') or md.get('iv') or md.get('impliedVolatility')
                    if isinstance(pe, dict):
                        md = pe.get('market_data') or pe
                        pe_iv = md.get('implied_volatility') or md.get('iv') or md.get('impliedVolatility')
                    ivs = []
                    if ce_iv is not None:
                        try: ivs.append(float(ce_iv))
                        except: pass
                    if pe_iv is not None:
                        try: ivs.append(float(pe_iv))
                        except: pass
                    if ivs:
                        return sum(ivs)/len(ivs)
                    return None
            except Exception:
                continue
        return None

    def _compute_dte_days(self, expiry_date_str):
        if not expiry_date_str:
            return None
        try:
            exp = datetime.strptime(expiry_date_str, "%Y-%m-%d")
            now = datetime.utcnow()
            diff = exp - now
            return max(0.0, diff.total_seconds() / 86400.0)
        except Exception:
            return None