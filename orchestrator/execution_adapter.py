import requests

class ExecutionAdapter:
    def __init__(self, config):
        self.config = config
        # Read webhook_url from config
        self.webhook_url = config.get("webhook_url", 
            config.get("upstox", {}).get("webhook_url", 
            "https://orders.algotest.in/webhook/tv/tk-trade?token=pLgMGjDyTluW1JkS4hbuN1HYCqGMmElv&tag=68f1af24611676c1c94ce1b0"))

    def send_orders(self, orders, tag=""):
        """
        Send orders to webhook endpoint with detailed logging.
        
        Args:
            orders: List of order dicts with keys: instrument, action, lots
            tag: Optional tag for order tracking
            
        Returns:
            tuple: (success: bool, message: str)
        """
        if not self.webhook_url:
            print("[ExecutionAdapter] ERROR: No webhook URL configured!")
            return False, "No webhook URL"
        
        all_success = True
        responses = []
        
        for i, order in enumerate(orders):
            # Build plain text payload (example: "SENSEX251016C75000 sell 1")
            instr = order["instrument"]
            action = order["action"]
            lots = order["lots"]
            side = "sell" if action == "sell" else "buy"
            payload = f"{instr} {side} {lots}"
            
            try:
                headers = {"Content-Type": "text/plain"}
                resp = requests.post(self.webhook_url, data=payload, headers=headers, timeout=15)
                
                # Log detailed response information
                print(f"[ExecutionAdapter] Order {i+1}/{len(orders)}: {payload}")
                print(f"[ExecutionAdapter]   Status: {resp.status_code}")
                print(f"[ExecutionAdapter]   Response: {resp.text[:500]}")  # First 500 chars
                
                if resp.status_code != 200:
                    print(f"[ExecutionAdapter]   ERROR: Non-200 status code")
                    print(f"[ExecutionAdapter]   Full Response Body: {resp.text}")
                    all_success = False
                    responses.append({
                        "order": payload,
                        "status": resp.status_code,
                        "success": False,
                        "response": resp.text
                    })
                else:
                    responses.append({
                        "order": payload,
                        "status": resp.status_code,
                        "success": True,
                        "response": resp.text
                    })
                    
            except requests.exceptions.Timeout as e:
                print(f"[ExecutionAdapter]   ERROR: Request timeout - {e}")
                all_success = False
                responses.append({
                    "order": payload,
                    "status": "timeout",
                    "success": False,
                    "response": str(e)
                })
            except Exception as e:
                print(f"[ExecutionAdapter]   ERROR: Exception sending order - {e}")
                all_success = False
                responses.append({
                    "order": payload,
                    "status": "error",
                    "success": False,
                    "response": str(e)
                })
        
        # Summary
        success_count = sum(1 for r in responses if r["success"])
        print(f"[ExecutionAdapter] Batch complete: {success_count}/{len(orders)} successful, tag={tag}")
        
        if all_success:
            return True, f"All {len(orders)} orders sent successfully"
        else:
            return False, f"Only {success_count}/{len(orders)} orders successful"

    def get_position_status(self):
        return {}