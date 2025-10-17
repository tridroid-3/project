#!/usr/bin/env python3
# Multi-% straddle roll monitor (SENSEX) with dynamic Iron Fly

import requests
import datetime
import time
from copy import deepcopy
from typing import Dict, Any, Optional, List
import re

STRATEGIES = [
    (5.0,  "https://orders.algotest.in/webhook/tv/tk-trade?token=pLgMGjDyTluW1JkS4hbuN1HYCqGMmElv&tag=68ef57bd748015596dd2cb6a"),
]

SYMBOL = "SENSEX"
MESSAGE_LOTS = 1
STRIKE_STEP = 100
LOT_SIZE = 20

START_TIME = datetime.time(9, 59)
EXIT_TIME = datetime.time(15, 29)

BUFFER = 10
HOLD_TIME = datetime.timedelta(minutes=1)
REFRESH_INTERVAL = 10

STOPLOSS_PER_LOT = 3000
TARGET_PER_LOT = 10000

ACCESS_TOKEN = "your_access_token_here"
EXPIRY_DATE = "2025-10-16"
UPSTOX_URL = "https://api.upstox.com/v2/option/chain"
UPSTOX_PARAMS_BASE = {"instrument_key": "BSE_INDEX|SENSEX"}
UPSTOX_HEADERS_TEMPLATE = {"Accept": "application/json", "Authorization": f"Bearer {ACCESS_TOKEN}"}

def nowstr() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")

def expiry_to_yymmdd(raw_expiry: str) -> str:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(raw_expiry, fmt)
            return dt.strftime("%y%m%d")
        except:
            continue
    try:
        dt = datetime.datetime.fromisoformat(raw_expiry)
        return dt.strftime("%y%m%d")
    except:
        return raw_expiry

def build_option_symbol(symbol: str, expiry_raw: str, strike: int, opt_type: str) -> str:
    yymmdd = expiry_to_yymmdd(expiry_raw)
    strike_str = str(int(strike))
    return f"{symbol}{yymmdd}{opt_type}{strike_str}"

def send_plain_to_url(url: str, payload: str, label: str = ""):
    try:
        headers = {"Content-Type": "text/plain"}
        resp = requests.post(url, data=payload, headers=headers, timeout=15)
        print(f"[{nowstr()}] 🔹 {label} | {payload} | URL={url.split('?')[0]} | Status={resp.status_code}")
        return (resp.status_code == 200, resp.status_code, resp.text)
    except Exception as e:
        print(f"[{nowstr()}] ❌ {label} payload error: {e} | URL={url.split('?')[0]}")
        return (False, None, str(e))

def get_ltp_for_instrument(data, instrument):
    m = re.match(r"([A-Z]+)(\d{6})([CP])(\d+)", instrument)
    if not m:
        return 0.0
    _, yymmdd, opt_type, strike = m.groups()
    strike = int(strike)
    for item in data:
        s = item.get("strike_price") or item.get("strike")
        if s is None:
            continue
        try:
            if int(s) != strike:
                continue
        except:
            continue
        if opt_type == "C":
            ce = item.get("call_options", {}).get("market_data", {})
            ltp = ce.get("ltp")
        else:
            pe = item.get("put_options", {}).get("market_data", {})
            ltp = pe.get("ltp")
        try:
            return float(ltp)
        except:
            return 0.0
    return 0.0

class StrategyContext:
    def __init__(self, trigger_pct: float, webhook_url: str):
        self.trigger_pct = trigger_pct
        self.webhook_url = webhook_url
        self.last_atm: Optional[int] = None
        self.last_roll_time: Optional[datetime.datetime] = None
        self.in_position: bool = False
        self.baseline_ce_ltp: Optional[float] = None
        self.baseline_pe_ltp: Optional[float] = None
        self.last_ce_action: Optional[str] = None
        self.last_pe_action: Optional[str] = None
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.realized_ce_mtm = 0.0
        self.realized_pe_mtm = 0.0

        # Iron Fly state
        self.otm_ce_instr: Optional[str] = None
        self.otm_pe_instr: Optional[str] = None
        self.otm_active: bool = False

    def log(self, msg: str):
        print(f"[{nowstr()}] [PCT={self.trigger_pct:g}%] {msg}")

    def build_entry_message(self, atm_strike: int, lots: int = MESSAGE_LOTS) -> str:
        ce = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "C")
        pe = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "P")
        return f"{ce} sell {lots}, {pe} sell {lots}"

    def build_single_entry(self, instrument: str, lots: int = MESSAGE_LOTS) -> str:
        return f"{instrument} sell {lots}"

    def build_single_exit(self, instrument: str, lots: int = MESSAGE_LOTS) -> str:
        return f"{instrument} buy {lots}"

    def exit_instrument_until_success(self, instrument: str, entry_price_for_mtm: Optional[float] = None, label: str = "exit"):
        payload = self.build_single_exit(instrument)
        attempt = 1
        while True:
            ok, status, text = send_plain_to_url(self.webhook_url, payload, f"{label} (attempt {attempt})")
            if ok:
                if instrument in self.positions and entry_price_for_mtm is not None:
                    try:
                        ent = self.positions[instrument].get("entry_price", 0.0)
                        qty = self.positions[instrument].get("quantity", 1)
                        side = self.positions[instrument].get("side", "S")
                        if side == "S":
                            realized = (ent - entry_price_for_mtm) * qty * LOT_SIZE
                        else:
                            realized = (entry_price_for_mtm - ent) * qty * LOT_SIZE
                        if instrument.endswith("C"):
                            self.realized_ce_mtm += realized
                            self.log(f"Realized CE MTM updated: {self.realized_ce_mtm}")
                        elif instrument.endswith("P"):
                            self.realized_pe_mtm += realized
                            self.log(f"Realized PE MTM updated: {self.realized_pe_mtm}")
                        self.positions[instrument]["mtm"] = realized
                    except Exception:
                        self.positions[instrument]["mtm"] = 0.0
                self.positions.pop(instrument, None)
                if instrument.endswith("C"):
                    self.last_ce_action = "B"
                elif instrument.endswith("P"):
                    self.last_pe_action = "B"
                return True
            self.log(f"❌ {label} failed (attempt {attempt}), retrying in 3s...")
            time.sleep(3)
            attempt += 1

    def enter_instrument_until_success(self, instrument: str, entry_price: Optional[float] = None, label: str = "entry"):
        if instrument in self.positions and self.positions[instrument].get("side") == "S":
            self.log(f"⚠️ Skipping duplicate sell for {instrument} (already short locally).")
            return True
        payload = self.build_single_entry(instrument)
        attempt = 1
        while True:
            ok, status, text = send_plain_to_url(self.webhook_url, payload, f"{label} (attempt {attempt})")
            if ok:
                self.positions[instrument] = {"side": "S", "entry_price": entry_price if entry_price is not None else 0.0, "quantity": MESSAGE_LOTS, "mtm": 0.0}
                if instrument.endswith("C"):
                    self.last_ce_action = "S"
                elif instrument.endswith("P"):
                    self.last_pe_action = "S"
                return True
            self.log(f"❌ {label} failed (attempt {attempt}), retrying in 3s...")
            time.sleep(3)
            attempt += 1

    def enter_straddle_until_success(self, atm_strike: int, ce_entry_price: Optional[float] = None, pe_entry_price: Optional[float] = None, lots: int = MESSAGE_LOTS):
        payload = self.build_entry_message(atm_strike, lots)
        attempt = 1
        while True:
            ok, status, text = send_plain_to_url(self.webhook_url, payload, f"entry-straddle (attempt {attempt})")
            if ok:
                ce_instr = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "C")
                pe_instr = build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, "P")
                self.positions[ce_instr] = {"side": "S", "entry_price": ce_entry_price if ce_entry_price is not None else 0.0, "quantity": lots, "mtm": 0.0}
                self.positions[pe_instr] = {"side": "S", "entry_price": pe_entry_price if pe_entry_price is not None else 0.0, "quantity": lots, "mtm": 0.0}
                self.last_ce_action = "S"
                self.last_pe_action = "S"
                return True
            self.log(f"❌ entry-straddle failed (attempt {attempt}), retrying in 3s...")
            time.sleep(3)
            attempt += 1

    # ------------------- Iron Fly Management -------------------
    def manage_otm_legs(self, atm_strike: int, data):
        # Enter OTM buy legs if not active
        if not self.otm_active:
            otm_ce_strike = atm_strike + 3*STRIKE_STEP
            otm_pe_strike = atm_strike - 3*STRIKE_STEP
            ce_instr = build_option_symbol(SYMBOL, EXPIRY_DATE, otm_ce_strike, "C")
            pe_instr = build_option_symbol(SYMBOL, EXPIRY_DATE, otm_pe_strike, "P")
            ce_ltp = get_ltp_for_instrument(data, ce_instr)
            pe_ltp = get_ltp_for_instrument(data, pe_instr)
            if ce_ltp > 0 and pe_ltp > 0:
                self.enter_instrument_until_success(ce_instr, entry_price=ce_ltp, label="entry-OTM-CE")
                self.enter_instrument_until_success(pe_instr, entry_price=pe_ltp, label="entry-OTM-PE")
                self.otm_ce_instr = ce_instr
                self.otm_pe_instr = pe_instr
                self.otm_active = True
                self.log(f"✅ Iron Fly OTM legs entered: {ce_instr}, {pe_instr}")

        # Exit OTM legs if market calms (CE/PE change < 1%)
        ce_change_pct = ((get_ltp_for_instrument(data, build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, 'C')) - self.baseline_ce_ltp)/self.baseline_ce_ltp)*100
        pe_change_pct = ((get_ltp_for_instrument(data, build_option_symbol(SYMBOL, EXPIRY_DATE, atm_strike, 'P')) - self.baseline_pe_ltp)/self.baseline_pe_ltp)*100
        if self.otm_active and abs(ce_change_pct) < 1.0 and abs(pe_change_pct) < 1.0:
            if self.otm_ce_instr:
                ltp = get_ltp_for_instrument(data, self.otm_ce_instr)
                self.exit_instrument_until_success(self.otm_ce_instr, entry_price_for_mtm=ltp, label="exit-OTM-CE")
            if self.otm_pe_instr:
                ltp = get_ltp_for_instrument(data, self.otm_pe_instr)
                self.exit_instrument_until_success(self.otm_pe_instr, entry_price_for_mtm=ltp, label="exit-OTM-PE")
            self.otm_ce_instr = self.otm_pe_instr = None
            self.otm_active = False
            self.log("✅ Iron Fly OTM legs squared off (market calmed)")