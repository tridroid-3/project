import requests
import json
import re
import time
import uuid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class ExecutionAdapter:
    def __init__(self, config):
        self.config = config
        # Read webhook_url from config
        self.webhook_url = config.get("webhook_url", 
            config.get("upstox", {}).get("webhook_url", 
            "https://orders.algotest.in/webhook/tv/tk-trade?token=pLgMGjDyTluW1JkS4hbuN1HYCqGMmElv&tag=68f1af24611676c1c94ce1b0"))
        
        # Execution config
        exec_cfg = config.get('execution', {}) or {}
        self.max_retries = exec_cfg.get('max_retries', 3)
        self.initial_retry_delay = exec_cfg.get('initial_retry_delay', 1)
        self.max_retry_delay = exec_cfg.get('max_retry_delay', 30)
        self.simulation_mode = exec_cfg.get('simulation_mode', False)
        
        # Circuit breaker state
        self.circuit_breaker_threshold = exec_cfg.get('circuit_breaker_threshold', 5)
        self.circuit_breaker_timeout = exec_cfg.get('circuit_breaker_timeout', 300)
        self.consecutive_failures = 0
        self.circuit_open_time = None
        self.circuit_state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        
        # Fill tracking
        self.pending_orders = {}  # tag -> order details
        self.filled_orders = {}  # tag -> fill details
        
        # Validate webhook URL
        self._validate_webhook_url()

    def _validate_webhook_url(self):
        """
        Validate that webhook URL contains a valid 24-character hex tag.
        
        Expected format: https://example.com/webhook?token=XXX&tag=YYYYYYYYYYYYYYYYYYYYYYYY
        Where tag is exactly 24 hexadecimal characters (0-9, a-f, A-F).
        """
        if not self.webhook_url:
            logger.error("No webhook URL configured!")
            return False
        
        # Extract tag parameter from URL
        tag_match = re.search(r'[?&]tag=([a-fA-F0-9]{24})(?:&|$)', self.webhook_url)
        if not tag_match:
            # More helpful error message
            tag_attempt = re.search(r'[?&]tag=([a-fA-F0-9]+)', self.webhook_url)
            if tag_attempt:
                tag_value = tag_attempt.group(1)
                logger.error(f"Invalid webhook URL: tag must be exactly 24 hexadecimal characters, "
                           f"found {len(tag_value)} characters: {tag_value}")
            else:
                logger.error(f"Invalid webhook URL: missing 'tag' parameter with 24-character hex value. "
                           f"Expected format: ?tag=YYYYYYYYYYYYYYYYYYYYYYYY (24 hex chars)")
            return False
        
        logger.info(f"Webhook URL validated with tag: {tag_match.group(1)}")
        return True
    
    def _check_circuit_breaker(self):
        """Check circuit breaker state and manage transitions."""
        current_time = time.time()
        
        if self.circuit_state == "OPEN":
            # Check if timeout has elapsed
            if current_time - self.circuit_open_time > self.circuit_breaker_timeout:
                self.circuit_state = "HALF_OPEN"
                logger.warning("Circuit breaker transitioning to HALF_OPEN state")
            else:
                remaining = int(self.circuit_breaker_timeout - (current_time - self.circuit_open_time))
                logger.error(f"Circuit breaker OPEN - rejecting request. Retry in {remaining}s")
                return False
        
        return True
    
    def _record_success(self):
        """Record successful order execution."""
        self.consecutive_failures = 0
        if self.circuit_state == "HALF_OPEN":
            self.circuit_state = "CLOSED"
            logger.info("Circuit breaker CLOSED after successful request")
    
    def _record_failure(self):
        """Record failed order execution and manage circuit breaker."""
        self.consecutive_failures += 1
        
        if self.consecutive_failures >= self.circuit_breaker_threshold:
            self.circuit_state = "OPEN"
            self.circuit_open_time = time.time()
            logger.critical(f"Circuit breaker OPEN after {self.consecutive_failures} consecutive failures")
            self._send_alert(f"🔴 Circuit breaker OPEN - {self.consecutive_failures} consecutive failures")
    
    def _send_alert(self, message):
        """
        Send alert via configured channels (Telegram/Slack).
        Note: Credentials are not logged in error messages for security.
        """
        alert_config = self.config.get('alerting', {})
        if not alert_config.get('enabled', False):
            return
        
        # Telegram alerting
        telegram = alert_config.get('telegram', {})
        bot_token = telegram.get('bot_token', '')
        chat_id = telegram.get('chat_id', '')
        
        if bot_token and chat_id:
            # Basic validation of bot token format (should start with digits, contain colon)
            if ':' not in bot_token or not bot_token.split(':')[0].isdigit():
                logger.warning("Telegram bot token format appears invalid (expected format: '123456:ABC-DEF...'), skipping alert")
            else:
                try:
                    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    response = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
                    if response.status_code != 200:
                        logger.error(f"Telegram alert failed with status {response.status_code}")
                except Exception as e:
                    logger.error(f"Failed to send Telegram alert: {type(e).__name__}")
        
        # Slack alerting
        slack = alert_config.get('slack', {})
        if slack.get('webhook_url'):
            try:
                requests.post(slack['webhook_url'], json={"text": message}, timeout=5)
            except Exception as e:
                logger.error(f"Failed to send Slack alert: {type(e).__name__}")
    
    def send_orders(self, orders, tag=""):
        """
        Send orders to webhook endpoint with detailed logging, retries, and fill tracking.
        
        Args:
            orders: List of order dicts with keys: instrument, action, lots
            tag: Optional tag for order tracking (unique idempotency key generated if not provided)
            
        Returns:
            tuple: (any_success: bool, responses: list of dicts)
        """
        if not self.webhook_url:
            logger.error("No webhook URL configured!")
            return False, []
        
        # Check circuit breaker
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
        
        # Generate unique tag if not provided
        if not tag:
            tag = f"order-{uuid.uuid4().hex[:12]}"
        
        responses = []
        any_success = False
        
        for i, order in enumerate(orders):
            # Generate unique idempotency key for each order
            idempotency_key = f"{tag}-{i}-{uuid.uuid4().hex[:8]}"
            
            # Build order as JSON string
            # Use timezone-aware UTC timestamp for consistency
            from datetime import timezone
            order_dict = {
                "instrument": order["instrument"],
                "action": order["action"],
                "lots": order.get("lots", order.get("quantity", 1)),
                "idempotency_key": idempotency_key,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Stringify as JSON for payload
            payload = json.dumps(order_dict)
            
            # Retry logic with exponential backoff
            attempt = 0
            retry_delay = self.initial_retry_delay
            order_success = False
            last_error = None
            
            while attempt < self.max_retries and not order_success:
                attempt += 1
                
                try:
                    headers = {"Content-Type": "text/plain"}
                    resp = requests.post(self.webhook_url, data=payload, headers=headers, timeout=15)
                    
                    # Log detailed response information
                    logger.info(f"Order {i+1}/{len(orders)} (attempt {attempt}): {order['instrument']} {order['action']} {order.get('lots', 1)}")
                    logger.info(f"  Status: {resp.status_code}")
                    logger.debug(f"  Response: {resp.text[:500]}")
                    
                    if resp.status_code == 200:
                        order_success = True
                        any_success = True
                        
                        # Parse response for order ID
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
                        
                        # Track pending order for fill confirmation
                        # Use timezone-aware UTC timestamp
                        from datetime import timezone
                        self.pending_orders[idempotency_key] = {
                            "order": order,
                            "order_id": order_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "status": "pending"
                        }
                        
                        logger.info(f"  ✅ Order placed successfully. Order ID: {order_id}")
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
            
            # Record failure if all retries exhausted
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
        
        # Update circuit breaker state
        if any_success:
            self._record_success()
        else:
            self._record_failure()
            self._send_alert(f"⚠️ All orders failed in batch (tag={tag})")
        
        # Summary
        success_count = sum(1 for r in responses if r.get("success", False))
        logger.info(f"Batch complete: {success_count}/{len(orders)} successful, tag={tag}")
        
        return any_success, responses
    
    def _parse_order_id(self, response_text):
        """
        Parse order ID from response text.
        Try multiple patterns to extract order ID.
        """
        if not response_text:
            return None
        
        try:
            # Try parsing as JSON first
            data = json.loads(response_text)
            # Common patterns for order ID in JSON
            for key in ['order_id', 'orderId', 'id', 'order_number', 'orderNumber']:
                if key in data:
                    return str(data[key])
            # Check nested data
            if 'data' in data:
                for key in ['order_id', 'orderId', 'id']:
                    if key in data['data']:
                        return str(data['data'][key])
        except:
            pass
        
        # Try regex patterns
        patterns = [
            r'order[_\s]?id["\s:]*([A-Za-z0-9\-]+)',
            r'id["\s:]*([A-Za-z0-9\-]+)',
            r'"([0-9]{10,})"',  # Long numeric IDs
        ]
        
        for pattern in patterns:
            match = re.search(pattern, response_text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def confirm_fill(self, idempotency_key, fill_price=None, fill_time=None):
        """
        Mark an order as filled.
        
        Args:
            idempotency_key: Unique key for the order
            fill_price: Actual fill price (optional)
            fill_time: Fill timestamp (optional)
        """
        if idempotency_key in self.pending_orders:
            order_info = self.pending_orders.pop(idempotency_key)
            order_info['status'] = 'filled'
            order_info['fill_price'] = fill_price
            # Use timezone-aware UTC timestamp
            from datetime import timezone
            order_info['fill_time'] = fill_time or datetime.now(timezone.utc).isoformat()
            self.filled_orders[idempotency_key] = order_info
            logger.info(f"Order filled: {idempotency_key}, price={fill_price}")
            return True
        return False
    
    def get_pending_orders(self):
        """Get list of pending orders awaiting fill confirmation."""
        return list(self.pending_orders.values())
    
    def get_filled_orders(self):
        """Get list of filled orders."""
        return list(self.filled_orders.values())

    def get_position_status(self):
        return {}