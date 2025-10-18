# Quick Start Guide

This guide will help you get started with the improved trading system.

## Prerequisites

- Python 3.9+ (for `zoneinfo` support)
- Required packages: `pandas`, `numpy`, `requests`, `pyyaml`

## Installation

```bash
# Install dependencies
pip install pandas numpy requests pyyaml

# Verify installation
python3 -c "from orchestrator.master import MasterOrchestrator; print('✅ Installation OK')"
```

## Configuration

### 1. Update Webhook URL

Edit `config/config.yaml` and set your webhook URL with a valid 24-character hex tag:

```yaml
upstox:
  webhook_url: "https://orders.algotest.in/webhook/tv/tk-trade?token=YOUR_TOKEN&tag=68f1af24611676c1c94ce1b0"
  #                                                                                   ^^^^^^^^^^^^^^^^^^^^^^^^
  #                                                                                   Must be 24 hex chars
```

### 2. Configure Risk Limits

Adjust risk limits to match your risk tolerance:

```yaml
global:
  max_daily_loss: 0.03  # 3% of account equity
  account_equity: 1000000  # Your account size in INR
  max_open_exposure: 0.10  # 10% max exposure
```

### 3. Set EOD Schedule

Configure end-of-day exit times (in IST - Asia/Kolkata timezone):

```yaml
global:
  timezone: "Asia/Kolkata"
  eod_exit_schedule:
    - time: "15:15:00"  # 3:15 PM IST
      pct: 50
      final: false
    - time: "15:29:00"  # 3:29 PM IST
      pct: 100
      final: true  # Stops the orchestrator after this
```

### 4. Enable Simulation Mode (Recommended for Testing)

```yaml
execution:
  simulation_mode: true  # Set to false for live trading
```

### 5. (Optional) Configure Alerting

For Telegram alerts:

```yaml
alerting:
  enabled: true
  telegram:
    bot_token: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    chat_id: "123456789"
```

For Slack alerts:

```yaml
alerting:
  enabled: true
  slack:
    webhook_url: "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

## Running the System

### Test Mode (Simulation)

```bash
# Set simulation_mode: true in config.yaml first
python3 main.py
```

This will:
- Run the orchestrator without sending real orders
- Log all actions to `data/logs/trades.jsonl` and `data/logs/trades.csv`
- Show console output for monitoring

### Live Trading

```bash
# Set simulation_mode: false in config.yaml
python3 main.py
```

⚠️ **Warning**: Only use this after thorough testing in simulation mode!

## Monitoring

### Watch Structured Logs (JSON)

```bash
# Install jq for JSON formatting
tail -f data/logs/trades.jsonl | jq .
```

### Watch CSV Logs

```bash
tail -f data/logs/trades.csv
```

### Check Order Status

```python
from orchestrator.execution_adapter import ExecutionAdapter
import yaml

config = yaml.safe_load(open('config/config.yaml'))
exec_adapter = ExecutionAdapter(config)

# Check pending orders
pending = exec_adapter.get_pending_orders()
print(f"Pending orders: {len(pending)}")

# Check filled orders
filled = exec_adapter.get_filled_orders()
print(f"Filled orders: {len(filled)}")
```

## Key Features

### 1. Fill Confirmation

Orders are tracked from placement to fill:

```python
# Order placement returns idempotency key
success, results = exec_adapter.send_orders(orders, tag="test-123")

# Later, when fill is confirmed (via webhook or polling)
for result in results:
    if result['success']:
        exec_adapter.confirm_fill(
            result['idempotency_key'],
            fill_price=100.50,
            fill_time="2024-01-15T10:30:00Z"
        )
```

### 2. Circuit Breaker

Automatically opens after consecutive failures:

```
5 consecutive failures → Circuit OPEN (5 minutes)
Circuit HALF_OPEN → Test request
Success → Circuit CLOSED
```

### 3. Exponential Backoff

Failed orders retry with increasing delays:

```
Attempt 1: immediate
Attempt 2: wait 1s
Attempt 3: wait 2s
Attempt 4: wait 4s
...up to max 30s
```

### 4. Daily Loss Limit

Trading halts when daily loss exceeds limit:

```python
# Configured as percentage of account equity
max_daily_loss = 0.03  # 3%

# When breached:
# 1. No new entries allowed
# 2. Alert sent (if configured)
# 3. Emergency mode activated
```

### 5. Emergency Mode

Manual or automatic activation:

```python
risk_manager.enter_emergency_mode("Manual intervention required")
# → Closes all positions immediately
# → Stops orchestrator
```

## Troubleshooting

### "Invalid webhook URL" Error

**Problem**: Webhook URL validation fails

**Solution**: Ensure tag parameter is exactly 24 hexadecimal characters:
```
✅ Good: ?tag=68f1af24611676c1c94ce1b0
❌ Bad:  ?tag=68f1af24611676  (too short)
❌ Bad:  ?tag=mytagname123456  (not hex)
```

### Circuit Breaker Opens Unexpectedly

**Problem**: Circuit breaker opens after few failures

**Solution**: Increase threshold in config:
```yaml
execution:
  circuit_breaker_threshold: 10  # More tolerant
```

### Daily Loss Limit Too Strict

**Problem**: Trading stops too early

**Solution**: Adjust limit or account equity:
```yaml
global:
  max_daily_loss: 0.05  # 5% instead of 3%
  account_equity: 1000000  # Update to actual equity
```

### Timezone Issues

**Problem**: EOD exit not triggering at expected time

**Solution**: Verify timezone setting:
```yaml
global:
  timezone: "Asia/Kolkata"  # IST
```

And ensure system time is correct:
```bash
timedatectl  # Check system timezone
```

## Testing Checklist

Before going live:

- [ ] Test in simulation mode for at least 1 week
- [ ] Verify EOD exit times trigger correctly
- [ ] Test emergency close functionality
- [ ] Verify webhook URL receives test orders
- [ ] Monitor structured logs for errors
- [ ] Test alerting (Telegram/Slack) if configured
- [ ] Verify daily loss limit triggers at threshold
- [ ] Check circuit breaker recovers after failures
- [ ] Validate order fill tracking
- [ ] Review risk limits match your strategy

## Best Practices

1. **Always Test First**: Use simulation mode before live trading
2. **Monitor Logs**: Watch `data/logs/trades.jsonl` for issues
3. **Set Alerts**: Configure Telegram/Slack for critical events
4. **Review Daily**: Check daily loss tracking and PnL
5. **Gradual Rollout**: Start with small position sizes
6. **Document Changes**: Keep notes on config changes
7. **Backup Config**: Save working config before changes
8. **Emergency Plan**: Know how to trigger emergency close

## Support

For detailed information, see:
- `IMPROVEMENTS_SUMMARY.md` - Complete list of improvements
- `REGIME_WINGS_README.md` - Regime-driven OTM wings strategy
- `config/config.example.yaml` - Configuration examples

For issues:
1. Check structured logs: `tail -f data/logs/trades.jsonl | jq .`
2. Review console output for warnings/errors
3. Verify configuration settings
4. Test in simulation mode first

## Example: Full Workflow

```bash
# 1. Clone and setup
cd /path/to/project
pip install pandas numpy requests pyyaml

# 2. Configure
nano config/config.yaml
# - Set webhook URL with 24-char hex tag
# - Set simulation_mode: true
# - Configure risk limits

# 3. Test
python3 main.py

# 4. Monitor (in another terminal)
tail -f data/logs/trades.jsonl | jq .

# 5. After testing, go live
nano config/config.yaml
# - Set simulation_mode: false

python3 main.py
```

## Quick Reference

| Feature | Config Key | Default |
|---------|-----------|---------|
| Simulation Mode | `execution.simulation_mode` | `false` |
| Max Retries | `execution.max_retries` | `3` |
| Circuit Breaker | `execution.circuit_breaker_threshold` | `5` |
| Daily Loss Limit | `global.max_daily_loss` | `0.03` (3%) |
| Max Exposure | `global.max_open_exposure` | `0.10` (10%) |
| Timezone | `global.timezone` | `Asia/Kolkata` |
| Poll Interval | `global.poll_interval` | `30` seconds |

---

**Ready to start?** Follow the steps above and begin testing in simulation mode!
