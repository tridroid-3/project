import requests

class ExecutionAdapter:
    def __init__(self, config):
        self.config = config
        # Read webhook_url from config
        self.webhook_url = config.get("webhook_url", "https://orders.algotest.in/webhook/tv/tk-trade?token=pLgMGjDyTluW1JkS4hbuN1HYCqGMmElv&tag=68f1af24611676c1c94ce1b0")

    def send_orders(self, orders, tag=""):
        if not self.webhook_url:
            print("No webhook URL configured!")
            return False, "No webhook URL"
        for order in orders:
            # Build plain text payload (example: "SENSEX251016C75000 sell 1")
            instr = order["instrument"]
            action = order["action"]
            lots = order["lots"]
            side = "sell" if action == "sell" else "buy"
            payload = f"{instr} {side} {lots}"
            try:
                headers = {"Content-Type": "text/plain"}
                resp = requests.post(self.webhook_url, data=payload, headers=headers, timeout=15)
                print(f"[ExecutionAdapter] Sent {payload} | Status={resp.status_code}")
                if resp.status_code != 200:
                    print(f"Error: {resp.text}")
                    return False, resp.text
            except Exception as e:
                print(f"Exception sending order: {e}")
                return False, str(e)
        return True, "All orders sent"

    def get_position_status(self):
        return {}