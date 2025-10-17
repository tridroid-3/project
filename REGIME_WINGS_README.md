# Regime-Based OTM Wings Enhancement

This document describes the recent enhancements to the Rolling Straddle strategy with regime classification and OTM wing management.

## Overview

The system now includes:
1. **Historical Data Fetching**: OHLC candles and IV history from Upstox API
2. **Regime Classification**: Five technical indicators classify market state
3. **Intelligent OTM Wings**: Iron Fly protection added/removed based on regime
4. **Enhanced Logging**: Detailed webhook responses and decision tracking

## Architecture

### Data Flow
```
Upstox API 
  ↓
Preprocessor (OHLC + IV data)
  ↓
RegimeClassifier (Technical Analysis)
  ↓
Snapshot (with regime info)
  ↓
RollingStraddle Strategy (OTM wing decisions)
  ↓
ExecutionAdapter (Order execution)
```

## Regime Classification

The system classifies markets into 5 regimes:

1. **CALM**: Low volatility, no strong trend
   - Low ATR, narrow Bollinger Bands, low IV rank
   - Strategy: Safe to enter straddles, no wings needed

2. **VOLATILE**: High volatility, unpredictable moves
   - High ATR or BB width, high IV rank
   - Strategy: Add OTM wings for protection

3. **TRENDING_UP**: Strong upward trend
   - High ADX, positive SMA slope, Plus DI > Minus DI
   - Strategy: Add OTM wings, may adjust entry

4. **TRENDING_DOWN**: Strong downward trend
   - High ADX, negative SMA slope, Minus DI > Plus DI
   - Strategy: Add OTM wings, may adjust entry

5. **TRANSITION**: Mixed signals, changing regime
   - Unclear or conflicting indicators
   - Strategy: Default behavior, cautious approach

### Technical Indicators

- **ATR (Average True Range)**: Measures volatility
- **ADX (Average Directional Index)**: Measures trend strength
- **Bollinger Band Width**: Measures volatility
- **SMA Slope**: Measures trend direction
- **IV Rank**: Percentile rank of current IV in history

## Configuration

### Required Config Sections

```yaml
regime:
  iv_history_length: 50
  atr_period: 14
  adx_period: 14
  bb_period: 20
  bb_std: 2.0
  sma_period: 20
  sma_lookback: 5
  atr_high_threshold: 2.0
  adx_trending_threshold: 25
  adx_strong_threshold: 40
  bb_width_high_threshold: 0.05
  sma_slope_threshold: 0.5
  iv_rank_high: 70
  iv_rank_low: 30

upstox:
  instrument_key: "BSE_INDEX|SENSEX"
  candles_url: "https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
  access_token: "YOUR_TOKEN"
  webhook_url: "YOUR_WEBHOOK_URL"
```

## Post-Merge Troubleshooting

### 1. Update Configuration

After merging, update your `config/config.yaml`:

```bash
# Copy example config as starting point
cp config/config.example.yaml config/config.yaml

# Edit with your credentials
nano config/config.yaml
```

Required changes:
- Set your Upstox `access_token`
- Set your `webhook_url`
- Adjust `expiry_date` for current expiry
- Verify `instrument_key` matches your trading instrument

### 2. Restart Orchestrator

```bash
# Stop existing process
pkill -f main.py

# Start with logging
python3 main.py 2>&1 | tee logs/orchestrator.log
```

### 3. Monitor Logs

Look for these key log lines:

**Successful Startup:**
```
Starting Master Orchestrator Loop
[Preprocessor] Snapshot: spot=75000.00, atm=75000, ce_ltp=250.00, pe_ltp=260.00
[RegimeClassifier] Regime=CALM, ATR=1.50%, ADX=20.0
[MasterOrchestrator] Market Regime: CALM
```

**OTM Wing Addition:**
```
[RollingStraddle] Adding OTM CE wing: SENSEX251023C76000 @ 50.00, regime=VOLATILE
[RollingStraddle] Adding OTM PE wing: SENSEX251023P74000 @ 55.00, regime=VOLATILE
[ExecutionAdapter] Order 1/2: SENSEX251023C76000 buy 1
[ExecutionAdapter]   Status: 200
```

**Regime Changes:**
```
[RegimeClassifier] Regime=VOLATILE, ATR=3.20%, ADX=45.0, IV_Rank=85.0
[RollingStraddle] Adding OTM wings: regime=VOLATILE, iv=32.50
```

### 4. Common Issues

#### Issue: No OHLC data fetched
```
[Preprocessor] Candles API error: 401 Unauthorized
```
**Solution**: Check access_token is valid and not expired

#### Issue: Empty option chain
```
[Preprocessor] Empty option chain data received
```
**Solution**: 
- Verify `instrument_key` is correct
- Check `expiry_date` matches available expiries
- Ensure market hours

#### Issue: Regime always TRANSITION
```
[RegimeClassifier] Regime=TRANSITION (all ticks)
```
**Solution**: 
- System needs 50+ OHLC bars for accurate classification
- Wait 1-2 poll cycles for data accumulation
- Check that candles API is returning data

#### Issue: OTM wings not added in volatile market
```
[RegimeClassifier] Regime=VOLATILE, ATR=3.50%
(but no wing orders)
```
**Solution**:
- Check strategy is in position: `in_position=True`
- Verify OTM strikes are available in option chain
- Look for strike adjustment messages in logs

### 5. Debug Mode

For detailed debugging, add print statements or reduce poll interval:

```yaml
global:
  poll_interval: 10  # Faster polling for testing
```

### 6. Testing Without Real Orders

To test without executing real orders, you can:

1. Comment out actual order execution in `execution_adapter.py`
2. Use a test webhook URL that logs but doesn't execute
3. Monitor logs to verify logic flow

## Key Decision Points to Monitor

1. **Entry Decision**: `can_enter` based on regime
2. **OTM Wing Addition**: Triggered by volatile/trending regimes
3. **OTM Wing Removal**: Triggered by calm regime return
4. **Roll Decisions**: Based on premium % change
5. **Emergency Exit**: OTM wings if they move > 25%

## Performance Expectations

- **First Few Ticks**: May see TRANSITION regime (accumulating data)
- **After 50+ Bars**: Accurate regime classification
- **Volatile Markets**: Expect frequent regime changes
- **Calm Markets**: Stable CALM regime, minimal wings

## Support

If you encounter issues:

1. Check all log sections mentioned above
2. Verify config against `config.example.yaml`
3. Ensure all dependencies installed: `pip3 install pandas numpy requests pyyaml`
4. Test regime classifier independently: `python3 tests/test_regime_classifier.py`

## Files Changed

- `orchestrator/preprocessor.py`: OHLC and IV data fetching
- `orchestrator/regime_classifier.py`: Technical indicator calculations
- `strategies/rolling_straddle.py`: OTM wing logic and logging
- `orchestrator/execution_adapter.py`: Enhanced response logging
- `orchestrator/master.py`: Regime classification integration
- `config/config.example.yaml`: Complete configuration template
- `.gitignore`: Standard Python gitignore
- `tests/test_regime_classifier.py`: Unit tests for regime logic
