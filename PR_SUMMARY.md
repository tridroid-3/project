# PR Summary: Regime Classification & OTM Wings

## Branch: fix/regime-otm-wings (merged to copilot/add-otm-iron-fly-wings)

## Overview
Added comprehensive regime classification system with 5 technical indicators to intelligently manage OTM protection wings for the Rolling Straddle strategy during rapid market moves.

## Statistics
- **Files Changed**: 10
- **Lines Added**: ~1,500+
- **Lines Removed**: ~80
- **Net Change**: ~1,420 lines
- **Commits**: 3

## Files Modified

### 1. orchestrator/preprocessor.py (+183 lines)
**Changes**:
- Added OHLC candle fetching from Upstox historical API
- Implemented IV history tracking with deque (configurable length)
- Returns pandas DataFrame for OHLC data
- Returns pandas Series for IV history
- Enhanced error handling and defensive parsing for Upstox responses
- Added robust DTE calculation
- Added detailed logging of snapshot data

**Key Methods**:
- `_fetch_ohlc_data()`: Fetches historical candles
- `_safe_get_ltp()`: Defensive LTP extraction
- `_safe_get_iv()`: Defensive IV extraction
- `_calculate_dte()`: Days to expiry calculation

### 2. orchestrator/regime_classifier.py (+239 lines)
**Changes**:
- Complete implementation of technical indicator calculations
- Regime classification logic based on multiple indicators
- Returns regime label and detailed metrics

**Technical Indicators Implemented**:
- **ATR (Average True Range)**: 14-period EMA of true range
- **ADX (Average Directional Index)**: Trend strength with DI+/DI-
- **Bollinger Band Width**: Volatility bands (20-period, 2 std dev)
- **SMA Slope**: Trend direction (20-period with 5-bar lookback)
- **IV Rank**: Percentile rank in historical IV range

**Regime States**:
1. CALM - Low volatility, no strong trend
2. VOLATILE - High volatility, unpredictable
3. TRENDING_UP - Strong upward trend
4. TRENDING_DOWN - Strong downward trend
5. TRANSITION - Mixed/changing signals

### 3. strategies/rolling_straddle.py (+127 lines)
**Changes**:
- Added `_find_available_otm_strike()` for robust strike selection
- Enhanced `_should_have_otm_wings()` with regime and IV rank logic
- Improved `_add_otm_wings()` with:
  - Robust strike lookup (handles missing exact strikes)
  - LTP==0 defensive handling
  - Comprehensive logging of wing additions
- Enhanced `_remove_otm_wings()` with PnL logging
- Improved entry logging with regime information
- Enhanced roll logging with percentage changes
- Added OTM emergency exit logging with PnL details

**Key Additions**:
- Regime-aware wing decisions
- Strike availability checking
- Detailed order logging at each decision point

### 4. orchestrator/execution_adapter.py (+75 lines)
**Changes**:
- Enhanced order sending with detailed logging
- Full webhook response body logging (first 500 chars)
- Individual order status tracking
- Timeout and exception handling with detailed errors
- Batch summary statistics (X/Y successful)

**Improvements**:
- Each order logged separately with status
- Non-200 responses log full body
- Timeout exceptions caught and logged
- Success rate tracked per batch

### 5. orchestrator/master.py (+147 lines)
**Changes**:
- Added regime classification to main loop
- Enhanced logging with timestamps and sections
- Regime info attached to snapshot before strategy calls
- Improved entry decision logging
- Better position management with reason tracking
- Exception handling in main loop with traceback

**Flow Changes**:
1. Get snapshot (with OHLC/IV)
2. Update volatility filter
3. **Classify regime** (NEW)
4. **Attach regime to snapshot** (NEW)
5. Check entry candidates
6. Execute chosen strategy
7. Manage open positions with regime context

### 6. config/config.yaml (+37 lines)
**Changes**:
- Added complete regime configuration section
- Added global strategy parameters (STRIKE_STEP, LOT_SIZE, etc.)
- Added instrument_key and candles_url to upstox section
- Added log_dir configuration
- **Removed hardcoded credentials** (security fix)

**New Sections**:
- `regime`: Complete indicator configuration and thresholds
- Global strategy params: STRIKE_STEP, LOT_SIZE, MESSAGE_LOTS, BUFFER, SYMBOL
- `log_dir`: Logging directory path

### 7. config/config.example.yaml (NEW, +65 lines)
**Purpose**: Template configuration with all parameters documented

**Includes**:
- All regime classifier parameters with descriptions
- Strategy configuration examples
- Placeholder credentials (YOUR_TOKEN_HERE)
- Comments explaining each parameter

### 8. .gitignore (NEW, +141 lines)
**Purpose**: Standard Python gitignore

**Key Exclusions**:
- `__pycache__/` and `*.pyc`
- Virtual environments
- `data/logs/*.csv`
- `config/config.yaml` (keeps credentials out of repo)
- IDE files, OS files

**Includes**:
- `!config/config.example.yaml` (keeps template in repo)

### 9. tests/test_regime_classifier.py (NEW, +306 lines)
**Purpose**: Unit tests for regime classifier with synthetic data

**Tests**:
1. `test_calm_market()`: Low volatility, flat market
2. `test_volatile_market()`: High volatility, choppy market
3. `test_trending_up_market()`: Upward trend detection
4. `test_empty_data()`: Graceful handling of missing data

**Features**:
- Synthetic OHLC generation with configurable trend/volatility
- Synthetic IV series generation
- Validates regime classification logic
- Tests defensive handling of edge cases

### 10. REGIME_WINGS_README.md (NEW, +245 lines)
**Purpose**: Comprehensive user documentation

**Sections**:
1. Overview and architecture
2. Regime classification explanation
3. Technical indicator descriptions
4. Configuration guide
5. **Post-merge troubleshooting** (detailed)
6. Log monitoring guide
7. Common issues and solutions
8. Debug mode instructions
9. Performance expectations

## Testing Performed

### Unit Tests
✅ All regime classifier tests pass
```
=== Test: Calm Market === ✓
=== Test: Volatile Market === ✓
=== Test: Trending Up Market === ✓
=== Test: Empty Data === ✓
```

### Integration Tests
✅ All imports successful
✅ Config loads correctly
✅ MasterOrchestrator initializes
✅ Strategy instantiation works
✅ No syntax errors

### Code Review
✅ Security issues addressed (credentials removed)
✅ All code follows existing patterns
✅ Minimal changes to unrelated code

## Dependencies Added
- pandas (for OHLC DataFrame)
- numpy (for technical calculations)
- requests (already used)
- pyyaml (already used)

## Breaking Changes
**None** - All changes are additive. Existing functionality preserved.

## Configuration Required Post-Merge

Users must:
1. Copy `config/config.example.yaml` to `config/config.yaml`
2. Add their Upstox access_token
3. Add their webhook_url
4. Update expiry_date for current expiry
5. Restart orchestrator

## Key Benefits

1. **Intelligent Risk Management**: OTM wings added automatically in volatile/trending markets
2. **Technical Analysis**: 5 indicators provide comprehensive market view
3. **Robust Execution**: Handles missing strikes, zero LTPs, API errors
4. **Enhanced Debugging**: Comprehensive logging at every decision point
5. **Security**: Credentials excluded from repository
6. **Testability**: Unit tests validate regime logic
7. **Documentation**: Complete troubleshooting guide

## Regime Decision Logic

```
IF high_volatility OR high_IV_rank:
    → Add OTM wings (protection needed)
ELIF strong_trend (ADX > 40):
    → Add OTM wings (directional risk)
ELIF low_volatility AND no_trend:
    → Remove OTM wings (no protection needed)
ELSE:
    → TRANSITION (maintain current state)
```

## Sample Log Output

```
[2025-10-17 10:30:00] === Tick Start ===
[Preprocessor] Snapshot: spot=75234.50, atm=75200, ce_ltp=245.30, pe_ltp=251.80, iv=22.45%, ohlc_rows=48
[RegimeClassifier] Regime=VOLATILE, ATR=2.85%, ADX=43.2, BB_Width=0.102, IV_Rank=85.0
[MasterOrchestrator] Market Regime: VOLATILE
[RollingStraddle] Adding OTM CE wing: SENSEX251023C76000 @ 48.50, regime=VOLATILE
[RollingStraddle] Adding OTM PE wing: SENSEX251023P74000 @ 52.30, regime=VOLATILE
[ExecutionAdapter] Order 1/2: SENSEX251023C76000 buy 1 | Status: 200
[ExecutionAdapter] Order 2/2: SENSEX251023P74000 buy 1 | Status: 200
[MasterOrchestrator] === Tick End ===
```

## Next Steps After Merge

1. ✅ Merge PR to main branch
2. ⚠️ Update production config with real credentials
3. ⚠️ Restart orchestrator service
4. 👀 Monitor logs for regime changes
5. 📊 Observe OTM wing additions in volatile markets
6. 📈 Track performance vs old logic

## Support Resources

- **Setup Guide**: `REGIME_WINGS_README.md`
- **Configuration Template**: `config/config.example.yaml`
- **Unit Tests**: `tests/test_regime_classifier.py`
- **Troubleshooting**: Section 4 in README

---

**PR Ready for Merge** ✅

All requirements met, tests pass, security issues resolved, documentation complete.
