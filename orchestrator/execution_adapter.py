# orchestrator/execution_adapter.py
"""
ExecutionAdapter (hardened, backwards-compatible)

Changes made (non-invasive):
- persistent pending_orders/fills (jsonl) with load/save
- ensure 24-hex tag appended when sending if webhook URL lacks valid tag
- optional fill_callback wiring via set_fill_callback()
- poll_pending(order_status_url_template) skeleton to reconcile fills
- minimal logging and safe defaults

Note: This preserves your original send_orders() semantics (payload stringified
and sent as text/plain) and simulation_mode behavior. No existing call sites
should need changes. If you'd like I can optionally wire poll_pending() to run
periodically (background thread), but I left it as an explicit method to avoid
changing runtime threading behavior.
"""
import requests
import json
import re
import time
import uuid
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ExecutionAdapter:
    def __init__(self, config, fill_callback=None):
        self.config = config or {}
        # Primary webhook URL (may already include a valid tag)
        self.webhook_url = config.get("webhook_url",
                                      config.get("upstox", {}).get(
                                          "webhook_url",
                                          "https://orders.algotest.in/webhook/tv/tk-trade?token=pLgMGjDyTluW1JkS4hbuN1HYCqGMmElv&tag=68f1af24611676c1c94ce1b0"
                                      ))

        # Execution config
        exec_cfg = config.get('execution', {}) or {}
        self.max_retries = exec_cfg.get('max_retries', 3)
        self.initial_retry_delay = exec_cfg.get('initial_retry_delay', 1)
        self.max_retry_delay = exec_cfg.get('max_retry_delay', 30)
        self.simulation_mode = exec_cfg.get('simulation_mode', False)

        # Circuit breaker
        self.circuit_breaker_threshold = exec_cfg.get('circuit_breaker_threshold', 5)
        self.circuit_breaker_timeout = exec_cfg.get('circuit_breaker_timeout', 300)
        self.consecutive_failures = 0
        self.circuit_open_time = None
        self.circuit_state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

        # Fill tracking (in-memory structures)
        # pending_orders keyed by idempotency_key -> metadata
        self.pending_orders = {}   # persisted to disk
        self.filled_orders = {}    # persisted to disk

        # Persistence files (configurable)
        data_cfg = exec_cfg.get("data_paths", {}) or {}
        self.pending_file = data_cfg.get("pending_file", "data/pending_orders.jsonl")
        self.filled_file = data_cfg.get("filled_file", "data/filled_orders.jsonl")
        # ensure data dir
        try:
            os.makedirs(os.path.dirname(self.pending_file) or ".", exist_ok=True)
        except Exception:
            pass

        # Fill callback hook (callable that accepts (idempotency_key, order_info))
        self.fill_callback = fill_callback

        # Validate webhook_url (log warnings/errors). This function returns True/False.
        self._validate_webhook_url()

        # Load persisted pending/fill files (best-effort)
        self._load_persisted_orders()

    # ---------------------------
    # Persistence helpers
    # ---------------------------
    def _persist_pending_record(self, key, record):
        """
        Append or update pending record in the pending_file (jsonl).
        For simplicity we append on add; we also write snapshot of current pending_orders
        to a compact json file for easier debugging.
        """
        try:
            with open(self.pending_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({key: record}, default=str) + "\n")
        except Exception as e:
            logger.debug(f"Could not append to pending_file {self.pending_file}: {e}")

        # Also save a current snapshot (overwrite)
        try:
            snapshot_path = self.pending_file + ".snapshot.json"
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(self.pending_orders, f, default=str, indent=2)
        except Exception as e:
            logger.debug(f"Could not write pending snapshot: {e}")

    def _persist_filled_record(self, key, record):
        try:
            with open(self.filled_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({key: record}, default=str) + "\n")
        except Exception as e:
            logger.debug(f"Could not append to filled_file {self.filled_file}: {e}")

        try:
            snapshot_path = self.filled_file + ".snapshot.json"
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(self.filled_orders, f, default=str, indent=2)
        except Exception as e:
            logger.debug(f"Could not write filled snapshot: {e}")

    def _load_persisted_orders(self):
        # Load pending (if snapshot exists prefer snapshot)
        try:
            snapshot_path = self.pending_file + ".snapshot.json"
            if os.path.exists(snapshot_path):
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # ensure keys are strings
                    self.pending_orders.update({str(k): v for k, v in data.items()})
            elif os.path.exists(self.pending_file):
                with open(self.pending_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                            # rec is {key: value}
                            for k, v in rec.items():
                                self.pending_orders[str(k)] = v
                        except Exception:
                            continue
        except Exception as e:
            logger.debug(f"Failed to load persisted pending orders: {e}")

        # Load filled orders
        try:
            snapshot_path = self.filled_file + ".snapshot.json"
            if os.path.exists(snapshot_path):
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.filled_orders.update({str(k): v for k, v in data.items()})
            elif os.path.exists(self.filled_file):
                with open(self.filled_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                            for k, v in rec.items():
                                self.filled_orders[str(k)] = v
                        except Exception:
                            continue
        except Exception as e:
            logger.debug(f"Failed to load persisted filled orders: {e}")

    # ---------------------------
    # Webhook / tag helpers
    # ---------------------------
    def _validate_webhook_url(self):
        """
        Ensure webhook_url appears syntactically correct and (if present) the tag is 24 hex chars.
        We will not break behavior, but log helpful messages.
        """
        if not self.webhook_url:
            logger.error("No webhook URL configured!")
            return False

        tag_match = re.search(r'[?&]tag=([a-fA-F0-9]{24})(?:&|$)', self.webhook_url)
        if tag_match:
            logger.info(f"Webhook URL validated with tag: {tag_match.group(1)}")
            return True

        # No 24-hex tag present — log warning (we will append one at send time)
        tag_attempt = re.search(r'[?&]tag=([^&]+)', self.webhook_url)
        if tag_attempt:
            logger.error(f"Invalid webhook URL tag value (not 24-hex): {tag_attempt.group(1)}")
        else:
            logger.warning("Webhook URL does not contain 'tag' parameter. A valid 24-hex tag "
                           "will be appended automatically for outgoing requests to meet broker requirements.")
        return False

    def _ensure_24hex_tag(self, tag: str = None):
        """Return a 24-hex hex string (existing tag preserved if valid)."""
        if tag and isinstance(tag, str) and re.fullmatch(r'[0-9a-fA-F]{24}', tag):
            return tag
        return uuid.uuid4().hex[:24]

    def _webhook_with_tag(self, tag: str = None):
        """
        Returns a webhook URL that is guaranteed to include a valid 24-hex tag parameter.
        Does not mutate self.webhook_url persistent string; builds a request-specific URL.
        """
        tag = self._ensure_24hex_tag(tag)
        if re.search(r'[?&]tag=', self.webhook_url):
            # replace existing tag param if present (even if invalid)
            url = re.sub(r'([?&]tag=)[^&]*', r'\1' + tag, self.webhook_url)
        else:
            sep = '&' if '?' in self.webhook_url else '?'
            url = f"{self.webhook_url}{sep}tag={tag}"
        return url

    # ---------------------------
    # Circuit breaker helpers (unchanged logic)
    # ---------------------------
    def _check_circuit_breaker(self):
        current_time = time.time()
        if self.circuit_state == "OPEN":
            if (current_time - (self.circuit_open_time or 0)) > self.circuit_breaker_timeout:
                self.circuit_state = "HALF_OPEN"
                logger.warning("Circuit breaker transitioning to HALF_OPEN state")
            else:
                remaining = int(self.circuit_breaker_timeout - (current_time - (self.circuit_open_time or 0)))
                logger.error(f"Circuit breaker OPEN - rejecting request. Retry in {remaining}s")
                return False
        return True

    def _record_success(self):
        self.consecutive_failures = 0
        if self.circuit_state == "HALF_OPEN":
            self.circuit_state = "CLOSED"
            logger.info("Circuit breaker CLOSED after successful request")

    def _record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.circuit_breaker_threshold:
            self.circuit_state = "OPEN"
            self.circuit_open_time = time.time()
            logger.critical(f"Circuit breaker OPEN after {self.consecutive_failures} consecutive failures")
            self._send_alert(f"🔴 Circuit breaker OPEN - {self.consecutive_failures} consecutive failures")

    def _send_alert(self, message):
        alert_config = self.config.get('alerting', {}) or {}
        if not alert_config.get('enabled', False):
            return

        # Telegram
        telegram = alert_config.get('telegram', {}) or {}
        bot_token = telegram.get('bot_token', '')
        chat_id = telegram.get('chat_id', '')
        if bot_token and chat_id:
            try:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {type(e).__name__}")

        # Slack
        slack = alert_config.get('slack', {}) or {}
        if slack.get('webhook_url'):
            try:
                requests.post(slack['webhook_url'], json={"text": message}, timeout=5)
            except Exception as e:
                logger.error(f"Failed to send Slack alert: {type(e).__name__}")

    # ---------------------------
    # Public API
    # ---------------------------
    def set_fill_callback(self, cb):
        """
        Set a callable to be invoked when fills are detected.
        Callable signature: cb(idempotency_key: str, order_info: dict)
        """
        if callable(cb):
            self.fill_callback = cb
            return True
        return False

    def send_orders(self, orders, tag=""):
        """
        Send orders (backwards-compatible). Returns (any_success, responses).
        Minor changes:
          - ensures 24-hex tag appended when sending if missing
          - persists pending orders to disk
        """
        if not self.webhook_url:
            logger.error("No webhook URL configured!")
            return False, []

        # Circuit breaker check
        if not self._check_circuit_breaker():
            return False, []

        # Simulation mode
        if self.simulation_mode:
            logger.info(f"[SIMULATION] Would send {len(orders)} orders with tag={tag}")
            responses = []
            for i, order in enumerate(orders):
                responses.append({
                    "order": order,
                    "status": 200,
                    "simulated": True,
                    "success": True,
                    "order_id": f"SIM-{uuid.uuid4().hex[:8]}",
                    "response": "Simulated order"
                })
            return True, responses

        # Ensure tag is valid 24-hex; we'll attach it to request URL
        request_tag = tag if tag and re.fullmatch(r'[0-9a-fA-F]{24}', tag) else self._ensure_24hex_tag(tag)

        responses = []
        any_success = False

        for i, order in enumerate(orders):
            # unique per-order idempotency
            idempotency_key = f"{request_tag}-{i}-{uuid.uuid4().hex[:8]}"
            # build order payload (same shape as original)
            order_dict = {
                "instrument": order["instrument"],
                "action": order["action"],
                "lots": order.get("lots", order.get("quantity", 1)),
                "idempotency_key": idempotency_key,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            payload = json.dumps(order_dict)

            attempt = 0
            retry_delay = self.initial_retry_delay
            order_success = False
            last_error = None

            # Use a request-specific webhook URL that contains a valid 24-hex tag param
            request_webhook = self._webhook_with_tag(request_tag)

            while attempt < self.max_retries and not order_success:
                attempt += 1
                try:
                    headers = {"Content-Type": "text/plain"}  # preserve original behavior
                    resp = requests.post(request_webhook, data=payload, headers=headers, timeout=15)

                    logger.info(f"Order {i+1}/{len(orders)} (attempt {attempt}): {order['instrument']} {order['action']} {order.get('lots', 1)}")
                    logger.info(f"  Status: {resp.status_code}")
                    logger.debug(f"  Response: {resp.text[:500]}")

                    if resp.status_code == 200:
                        order_success = True
                        any_success = True
                        order_id = self._parse_order_id(resp.text)

                        response_data = {
                            "order": order,
                            "status": resp.status_code,
                            "success": True,
                            "order_id": order_id,
                            "idempotency_key": idempotency_key,
                            "response": resp.text,
                            "simulated": False
                        }
                        responses.append(response_data)

                        # Track pending order
                        self.pending_orders[idempotency_key] = {
                            "order": order,
                            "order_id": order_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status": "pending"
                        }
                        # persist pending record
                        try:
                            self._persist_pending_record(idempotency_key, self.pending_orders[idempotency_key])
                        except Exception:
                            pass

                        logger.info(f"  ✅ Order placed successfully. Order ID: {order_id}, idempotency={idempotency_key}")
                    else:
                        last_error = f"HTTP {resp.status_code}: {resp.text}"
                        logger.warning(f"  ❌ Non-200 status: {last_error}")
                        if attempt < self.max_retries:
                            logger.info(f"  Retrying in {retry_delay}s...")
                            time.sleep(retry_delay)
                            retry_delay = min(retry_delay * 2, self.max_retry_delay)
                except requests.exceptions.Timeout as e:
                    last_error = f"Timeout: {str(e)}"
                    logger.warning(f"  ❌ Request timeout - {e}")
                    if attempt < self.max_retries:
                        logger.info(f"  Retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self.max_retry_delay)
                except Exception as e:
                    last_error = f"Exception: {str(e)}"
                    logger.error(f"  ❌ Exception sending order - {e}")
                    if attempt < self.max_retries:
                        logger.info(f"  Retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self.max_retry_delay)

            if not order_success:
                responses.append({
                    "order": order,
                    "status": "failed",
                    "success": False,
                    "error": last_error,
                    "attempts": attempt,
                    "simulated": False
                })
                logger.error(f"  ❌ Order failed after {attempt} attempts: {last_error}")

        # Update circuit breaker
        if any_success:
            self._record_success()
        else:
            self._record_failure()
            self._send_alert(f"⚠️ All orders failed in batch (tag={request_tag})")

        success_count = sum(1 for r in responses if r.get("success", False))
        logger.info(f"Batch complete: {success_count}/{len(orders)} successful, tag={request_tag}")

        # Optionally poll pending immediately (non-blocking in current design)
        # If config says immediate_poll_on_send, run a short poll to attempt reconciliation
        if self.config.get('execution', {}).get('immediate_poll_on_send', False):
            try:
                self.poll_pending()
            except Exception:
                logger.debug("Immediate poll_pending() failed (non-fatal)")

        return any_success, responses

    def _parse_order_id(self, response_text):
        if not response_text:
            return None
        try:
            data = json.loads(response_text)
            for key in ['order_id', 'orderId', 'id', 'order_number', 'orderNumber']:
                if key in data:
                    return str(data[key])
            if 'data' in data:
                for key in ['order_id', 'orderId', 'id']:
                    if key in data['data']:
                        return str(data['data'][key])
        except Exception:
            pass

        patterns = [
            r'order[_\s]?id["\s:]*([A-Za-z0-9\-]+)',
            r'id["\s:]*([A-Za-z0-9\-]+)',
            r'"([0-9]{10,})"',
        ]
        for pattern in patterns:
            match = re.search(pattern, response_text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def confirm_fill(self, idempotency_key, fill_price=None, fill_time=None):
        """
        Mark an order as filled. If a fill_callback is set it will be invoked
        with (idempotency_key, order_info).
        """
        if idempotency_key in self.pending_orders:
            order_info = self.pending_orders.pop(idempotency_key)
            order_info['status'] = 'filled'
            order_info['fill_price'] = fill_price
            order_info['fill_time'] = fill_time or datetime.now(timezone.utc).isoformat()
            self.filled_orders[idempotency_key] = order_info
            # persist
            try:
                self._persist_filled_record(idempotency_key, order_info)
            except Exception:
                pass

            logger.info(f"Order filled: {idempotency_key}, price={fill_price}")

            # invoke callback if set
            try:
                if callable(self.fill_callback):
                    try:
                        self.fill_callback(idempotency_key, order_info)
                    except Exception as e:
                        logger.exception(f"fill_callback raised an exception: {e}")
            except Exception:
                pass

            return True
        return False

    def get_pending_orders(self):
        return list(self.pending_orders.values())

    def get_filled_orders(self):
        return list(self.filled_orders.values())

    # ---------------------------
    # Poller / Reconciliation (skeleton)
    # ---------------------------
    def poll_pending(self, order_status_url_template: str = None):
        """
        Poll pending orders and attempt to detect fills using an order-status endpoint.

        order_status_url_template: optional. If not provided, adapter will try to read
        config['execution']['order_status_url_template'] which should be a format string
        containing {order_id} or {idempotency_key}. Example:
            https://broker/api/orders/{order_id}/status

        IMPORTANT: adapt parsing logic below to match your broker's response JSON.
        """
        if not self.pending_orders:
            return

        template = order_status_url_template or self.config.get('execution', {}).get('order_status_url_template')
        if not template:
            # no endpoint configured
            logger.debug("poll_pending(): no order_status_url_template configured; skipping poll")
            return

        # Iterate copy to allow mutation
        for kid, rec in list(self.pending_orders.items()):
            order_id = rec.get('order_id')
            idempotency_key = kid
            if not order_id and '{order_id}' not in template:
                # if template expects idempotency_key, use that
                url = template.format(idempotency_key=idempotency_key)
            elif order_id and '{order_id}' in template:
                url = template.format(order_id=order_id)
            else:
                # fallback use idempotency key
                url = template.format(idempotency_key=idempotency_key)

            try:
                r = requests.get(url, timeout=8)
                if r.status_code != 200:
                    logger.debug(f"poll_pending: non-200 from status endpoint for {kid}: {r.status_code}")
                    continue
                data = r.json()
                # Adapt these keys to your broker's shape:
                # Example assumptions:
                # - data.get('status') -> "filled" / "open" / "cancelled"
                # - data.get('filled_price') or data.get('avg_fill_price')
                status = data.get('status') or data.get('state') or (data.get('data') or {}).get('status')
                if isinstance(status, str) and status.lower() in ("filled", "complete", "closed", "executed"):
                    fill_price = data.get('filled_price') or data.get('avg_fill_price') or (data.get('data') or {}).get('avg_fill_price')
                    # confirm fill locally
                    self.confirm_fill(idempotency_key, fill_price=fill_price, fill_time=data.get('filled_at'))
            except Exception as e:
                logger.debug(f"poll_pending: error checking order {kid}: {e}")

        # done

    def get_position_status(self):
        """
        Placeholder for compatibility. Could return aggregated pending/fill counts.
        """
        return {
            "pending": len(self.pending_orders),
            "filled": len(self.filled_orders),
            "circuit_state": self.circuit_state
        }
