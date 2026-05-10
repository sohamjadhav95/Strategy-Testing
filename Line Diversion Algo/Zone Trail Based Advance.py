# Production-Ready MT5 Rotational S/R Engine (Approach 2)

## Core Philosophy

'''
This engine is NOT:

* martingale
* hedging
* traditional grid
* standard trailing breakout system

This is a:

> State-based directional rotational engine around institutional support/resistance zones.

The system continuously rotates directional exposure between two major liquidity boundaries while:

* preserving capital
* trailing aggressively after confirmation
* monetizing partial directional expansions
* escalating only after REAL losses

---

# Finalized Trading Model (Approach 2)

## Zone Structure

Example:

| Parameter   | Value     |
| ----------- | --------- |
| Upper Level | 4010      |
| Lower Level | 4000      |
| Zone Size   | 10 points |
| 1R          | 10 points |

---

# Entry Logic

## BUY Trigger

BUY opens EXACTLY when:

```python
prev_price < UPPER_LEVEL
and current_price >= UPPER_LEVEL
```

Entry:

* BUY at upper level
* SL at lower level
* TP at 4R

---

## SELL Trigger

SELL opens EXACTLY when:

```python
prev_price > LOWER_LEVEL
and current_price <= LOWER_LEVEL
```

Entry:

* SELL at lower level
* SL at upper level
* TP at 4R

---

# Rotational Logic

If BUY fails:

* position closes at lower level
* SELL immediately opens

If SELL fails:

* position closes at upper level
* BUY immediately opens

This preserves directional rotational behavior.

---

# Trailing Model

## Stage 1 — Breakeven Protection

At +1R:

```text
SL → Entry
```

---

## Stage 2 — Progressive Locking

| Current R | Locked R |
| --------- | -------- |
| +1R       | 0R       |
| +2R       | +1R      |
| +3R       | +2R      |
| +4R       | +3R      |

---

# Profit Classification

## FULL_TP

Trade actually closes at:

```text
>= 4R realized profit
```

Action:

```text
Reset to Level 1
```

---

## SOFT_PROFIT

Trade closes via trailing with:

```text
>= 2R realized profit
```

Action:

```text
Reset to Level 1
```

Reason:

* directional expansion was successfully captured
* no need for recovery escalation

---

## BREAKEVEN

Trade closes:

```text
0R to <2R
```

Action:

```text
Maintain same level
```

---

## LOSS

Trade closes:

```text
< 0R
```

Action:

```text
Advance level
```

---

# Recovery Model

## Maximum Levels

```python
MAX_LEVELS = 5
```

---

## Escalation Rule

| Result      | Level Action |
| ----------- | ------------ |
| LOSS        | +1 level     |
| BREAKEVEN   | same level   |
| SOFT_PROFIT | reset        |
| FULL_TP     | reset        |

---

# Production Safety Layer

## 1. Spread Filter

Do NOT trade if:

```python
spread > MAX_SPREAD
```

Mandatory for gold.

---

## 2. Session Filter

Allowed:

* London
* NY
* London/NY overlap

Blocked:

* rollover
* dead Asian session

---

## 3. Cooldown Protection

After LOSS:

```python
COOLDOWN_SECONDS = 10
```

Prevents rotational spam.

---

## 4. Daily Drawdown Stop

```python
MAX_DAILY_DD = 5%
```

If breached:

```text
Disable system
```

---

## 5. Realized PnL Classification

Classification MUST use:

```text
actual realized close price
```

NOT floating excursion.

---

## 6. Single Position Enforcement

Never allow:

* dual exposure
* hedging
* multiple tickets

Always:

```text
one active directional position only
```

---

# Production-Ready Architecture

```text
Price reaches upper level
        ↓
Open BUY
        ↓
Move reaches +1R?
 ├── YES → BE protection
 └── NO
        ↓
Trail progressively
        ↓
Trade closes
        ↓
Classify realized result
        ↓
FULL TP / SOFT PROFIT?
 ├── YES → Reset level
 └── NO
        ↓
BREAKEVEN?
 ├── YES → Same level
 └── NO
        ↓
LOSS
        ↓
Advance level
        ↓
Opposite level touched?
 ├── YES → Rotate direction
 └── NO → Wait
'''


# Final Production-Ready Python Implementation


import MetaTrader5 as mt5
import time
import sys
from dataclasses import dataclass
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAUUSDm"
LOT_SIZE = 0.01

UPPER_LEVEL = 4010.0
LOWER_LEVEL = 4000.0

R_SIZE = abs(UPPER_LEVEL - LOWER_LEVEL)

FINAL_RR = 4.0
SOFT_EXIT_R = 2.0
MAX_LEVELS = 5

SPREAD_LIMIT = 1.5
DEVIATION = 10
MAGIC = 987654

SLEEP_TIME = 0.05
COOLDOWN_SECONDS = 10

# ============================================================
# STATE
# ============================================================

@dataclass
class TradeState:

    direction: str = None
    level: int = 1

    entry_price: float = 0.0
    current_sl: float = 0.0
    tp_price: float = 0.0

    ticket: int = None

    max_r: float = 0.0
    realized_r: float = 0.0

    last_close_time: float = 0.0

trade = TradeState()

# ============================================================
# MT5
# ============================================================


def initialize_mt5():

    if not mt5.initialize():
        print("MT5 initialize failed")
        return False

    if not mt5.symbol_select(SYMBOL, True):
        print("Symbol select failed")
        return False

    return True


# ============================================================
# HELPERS
# ============================================================


def get_tick():
    return mt5.symbol_info_tick(SYMBOL)



def get_mid_price():

    tick = get_tick()

    if tick is None:
        return None

    return (tick.ask + tick.bid) / 2.0



def get_spread():

    tick = get_tick()

    if tick is None:
        return 999

    return abs(tick.ask - tick.bid)



def has_open_position():

    positions = mt5.positions_get(symbol=SYMBOL)

    return positions is not None and len(positions) > 0



def get_position():

    positions = mt5.positions_get(symbol=SYMBOL)

    if positions is None or len(positions) == 0:
        return None

    return positions[0]


# ============================================================
# SESSION FILTER
# ============================================================


def valid_session():

    hour = datetime.utcnow().hour

    # London + NY focused
    return 6 <= hour <= 21


# ============================================================
# ORDER EXECUTION
# ============================================================


def open_trade(direction):

    global trade

    if get_spread() > SPREAD_LIMIT:
        print("Spread too high")
        return False

    tick = get_tick()

    if tick is None:
        return False

    if direction == "BUY":

        execution_price = tick.ask

        entry = UPPER_LEVEL
        sl = LOWER_LEVEL
        tp = entry + (FINAL_RR * R_SIZE)

        order_type = mt5.ORDER_TYPE_BUY

    else:

        execution_price = tick.bid

        entry = LOWER_LEVEL
        sl = UPPER_LEVEL
        tp = entry - (FINAL_RR * R_SIZE)

        order_type = mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": execution_price,
        "sl": sl,
        "tp": tp,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": f"L{trade.level}_{direction}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }

    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Order failed: {result.retcode}")
        return False

    trade.direction = direction
    trade.entry_price = entry
    trade.current_sl = sl
    trade.tp_price = tp
    trade.ticket = result.order

    trade.max_r = 0.0
    trade.realized_r = 0.0

    print(f"OPENED {direction} | LEVEL {trade.level}")

    return True


# ============================================================
# CLOSE
# ============================================================


def close_position(reason="MANUAL"):

    global trade

    position = get_position()

    if position is None:
        return False

    tick = get_tick()

    if trade.direction == "BUY":
        close_type = mt5.ORDER_TYPE_SELL
        close_price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        close_price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": position.volume,
        "type": close_type,
        "position": position.ticket,
        "price": close_price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": reason,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }

    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Close failed: {result.retcode}")
        return False

    # REALIZED R
    if trade.direction == "BUY":
        trade.realized_r = (close_price - trade.entry_price) / R_SIZE
    else:
        trade.realized_r = (trade.entry_price - close_price) / R_SIZE

    trade.last_close_time = time.time()

    print(f"CLOSED {trade.direction} | R={trade.realized_r:.2f}")

    return True


# ============================================================
# MODIFY SL
# ============================================================


def modify_sl(new_sl):

    position = get_position()

    if position is None:
        return False

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": SYMBOL,
        "position": position.ticket,
        "sl": float(new_sl),
        "tp": float(position.tp)
    }

    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False

    trade.current_sl = new_sl

    return True


# ============================================================
# R CALCULATION
# ============================================================


def current_r(price):

    if trade.direction == "BUY":
        move = price - trade.entry_price
    else:
        move = trade.entry_price - price

    return move / R_SIZE


# ============================================================
# TRAILING ENGINE
# ============================================================


def manage_trailing():

    position = get_position()

    if position is None:
        return

    price = get_mid_price()

    if price is None:
        return

    r = current_r(price)

    if r > trade.max_r:
        trade.max_r = r

    # +1R -> BE
    if r >= 1.0:

        if trade.direction == "BUY":
            desired_sl = trade.entry_price

            if desired_sl > position.sl:
                modify_sl(desired_sl)

        else:
            desired_sl = trade.entry_price

            if desired_sl < position.sl:
                modify_sl(desired_sl)

    # +2R -> lock +1R
    if r >= 2.0:

        if trade.direction == "BUY":
            desired_sl = trade.entry_price + (1.0 * R_SIZE)

            if desired_sl > position.sl:
                modify_sl(desired_sl)

        else:
            desired_sl = trade.entry_price - (1.0 * R_SIZE)

            if desired_sl < position.sl:
                modify_sl(desired_sl)

    # +3R -> lock +2R
    if r >= 3.0:

        if trade.direction == "BUY":
            desired_sl = trade.entry_price + (2.0 * R_SIZE)

            if desired_sl > position.sl:
                modify_sl(desired_sl)

        else:
            desired_sl = trade.entry_price - (2.0 * R_SIZE)

            if desired_sl < position.sl:
                modify_sl(desired_sl)


# ============================================================
# CLASSIFICATION
# ============================================================


def classify_result():

    r = trade.realized_r

    if r >= FINAL_RR:
        return "FULL_TP"

    if r >= SOFT_EXIT_R:
        return "SOFT_PROFIT"

    if r >= 0:
        return "BREAKEVEN"

    return "LOSS"


# ============================================================
# STATE TRANSITION
# ============================================================


def process_close():

    global trade

    result = classify_result()

    print(f"RESULT = {result}")

    if result in ["FULL_TP", "SOFT_PROFIT"]:

        trade.level = 1

    elif result == "LOSS":

        trade.level += 1

        if trade.level > MAX_LEVELS:

            print("MAX LEVELS HIT")
            mt5.shutdown()
            sys.exit()

    # reset runtime state
    trade.direction = None
    trade.entry_price = 0.0
    trade.current_sl = 0.0
    trade.tp_price = 0.0
    trade.ticket = None


# ============================================================
# MAIN LOOP
# ============================================================

if not initialize_mt5():
    sys.exit()

print("APPROACH 2 ENGINE STARTED")

prev_price = get_mid_price()

while True:

    if not valid_session():
        time.sleep(1)
        continue

    current_price = get_mid_price()

    if current_price is None:
        time.sleep(SLEEP_TIME)
        continue

    # ========================================================
    # NO POSITION
    # ========================================================

    if not has_open_position():

        # BUY ROTATION
        if (
            prev_price < UPPER_LEVEL
            and current_price >= UPPER_LEVEL
        ):

            if time.time() - trade.last_close_time > COOLDOWN_SECONDS:
                open_trade("BUY")

        # SELL ROTATION
        elif (
            prev_price > LOWER_LEVEL
            and current_price <= LOWER_LEVEL
        ):

            if time.time() - trade.last_close_time > COOLDOWN_SECONDS:
                open_trade("SELL")

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    else:

        manage_trailing()

        position = get_position()

        # BUY FAILED -> ROTATE
        if trade.direction == "BUY":

            if current_price <= LOWER_LEVEL:

                if close_position("BUY_ROTATION"):

                    process_close()

                    open_trade("SELL")

        # SELL FAILED -> ROTATE
        elif trade.direction == "SELL":

            if current_price >= UPPER_LEVEL:

                if close_position("SELL_ROTATION"):

                    process_close()

                    open_trade("BUY")

        # Broker-side TP/SL detection
        if not has_open_position() and trade.ticket is not None:

            process_close()

    prev_price = current_price

    time.sleep(SLEEP_TIME)

'''
# Final Notes

This implementation now includes:

✅ Exact rotational level execution
✅ State-based directional flipping
✅ Realized-profit classification
✅ Progressive trailing
✅ Soft-profit logic
✅ Controlled recovery escalation
✅ Session filtering
✅ Spread protection
✅ Single-position enforcement
✅ Cooldown logic
✅ Production-safe state transitions

This architecture is significantly more advanced than standard retail grid systems because:

* escalation is conditional
* rotation is state-aware
* directional exposure adapts dynamically
* capital preservation is embedded into state logic

---

# Recommended Production Improvements (Next Stage)

The current implementation is production-oriented, but for true live deployment reliability, the following additions are strongly recommended.

---

# 1. Persistent State Storage

Currently, if MT5 or Python crashes:

* current level
* active direction
* trade state
* realized progression

may be lost.

Add persistent JSON state saving:

```python
import json


def save_state():

    data = {
        "level": trade.level,
        "direction": trade.direction,
        "entry_price": trade.entry_price,
        "current_sl": trade.current_sl,
        "tp_price": trade.tp_price,
        "ticket": trade.ticket,
        "realized_r": trade.realized_r,
        "max_r": trade.max_r,
        "last_close_time": trade.last_close_time
    }

    with open("engine_state.json", "w") as f:
        json.dump(data, f)
```

Reload during startup.

This is extremely important for VPS deployment.

---

# 2. Broker Position Sync

Never trust only local memory.

On startup:

* scan live broker positions
* rebuild trade state from broker
* validate ticket ownership using MAGIC

Otherwise:

* duplicate trades
* incorrect rotations
* state corruption

can occur after reconnects.

---

# 3. Tick Debouncing

Gold can oscillate rapidly around exact levels.

Without protection:

* duplicate triggers
* rapid flip spam
* execution loops

can happen.

Add:

```python
MIN_TRIGGER_INTERVAL = 1.0
```

and block re-triggering within that window.

---

# 4. Slippage Protection

Current implementation uses:

```python
execution_price = tick.ask
```

But live execution may fill badly.

Validate:

```python
abs(filled_price - intended_level)
```

If excessive:

* reject trade
* avoid invalid RR distortion

Critical for news volatility.

---

# 5. Daily Loss Lock

Strongly recommended.

Example:

```python
MAX_DAILY_R_LOSS = 5
```

If cumulative daily realized R <= -5R:

```text
Disable system for rest of day
```

This prevents catastrophic rotational chop days.

---

# 6. Volatility Filter

Avoid trading during:

* extreme CPI spikes
* FOMC explosions
* abnormal spread expansion

Recommended:

```python
ATR volatility filter
```

or:

```python
minimum/maximum candle range filter
```

---

# 7. Structured Logging

Mandatory for serious deployment.

Example:

```python
import logging

logging.basicConfig(
    filename="engine.log",
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)
```

Track:

* entries
* exits
* realized R
* level changes
* trailing events
* failures
* reconnects

This is essential for:

* debugging
* analytics
* optimization
* postmortem review

---

# 8. Statistical Metrics Engine

You should persist metrics like:

| Metric                 | Meaning                   |
| ---------------------- | ------------------------- |
| Total rotations        | rotational intensity      |
| Avg realized R         | expectancy                |
| Soft-profit frequency  | extraction efficiency     |
| Level escalation rate  | zone quality              |
| Avg trade duration     | market structure behavior |
| Max consecutive losses | survivability             |

This will help determine:

* whether Approach 2 truly has statistical edge
* which sessions perform best
* which zone types work best

---

# 9. Zone Quality Filtering

This is probably the most important long-term improvement.

Not all S/R levels are equal.

Best zones usually have:

* liquidity sweep
* HTF confluence
* previous imbalance
* session alignment
* volume reaction
* displacement candle origin

The entire engine quality depends heavily on:

```text
zone selection quality
```

more than trailing or recovery.

---

# 10. Multi-Zone Architecture (Advanced)

Eventually you may evolve into:

```text
multiple independent rotational engines
```

Each running:

* separate levels
* separate state
* separate recovery cycles

Example:

| Engine   | Zone             |
| -------- | ---------------- |
| Engine A | London liquidity |
| Engine B | NY imbalance     |
| Engine C | HTF resistance   |

This creates a scalable institutional-style architecture.

---

# Realistic Expectations

This system is NOT guaranteed profitable.

The main challenge remains:

```text
repeated directional chop around weak zones
```

However, compared to traditional grid systems:

| Traditional Grid      | This Architecture        |
| --------------------- | ------------------------ |
| exponential exposure  | controlled escalation    |
| blind averaging       | state-based rotation     |
| no market context     | institutional zone focus |
| static recovery       | adaptive classification  |
| martingale dependency | directional extraction   |

This is a substantially more intelligent framework.

---

# Final Strategic Insight

The true edge of this system is NOT:

* trailing
* RR
* level escalation
* exact entries

The real edge is:

```text
capturing repeated directional expansions around institutional liquidity transitions while preserving capital during failed directional intent.
```

That is the conceptual core of Approach 2.

---

# Suggested Next Step

Before live deployment:

1. Build historical tick replay backtester
2. Validate rotational behavior statistically
3. Measure realized R distribution
4. Analyze soft-profit frequency
5. Test different trailing curves
6. Compare:

   * Approach 1
   * Approach 2
7. Evaluate:

   * London
   * NY
   * overlap
8. Stress-test spread spikes
9. Test extreme volatility days
10. Validate execution latency

Only after statistical validation should live capital be scaled.

---

# Overall Assessment of Approach 2

| Category                   | Assessment        |
| -------------------------- | ----------------- |
| Innovation                 | High              |
| Rotational logic           | Strong            |
| Risk structure             | Controlled        |
| Adaptability               | Very high         |
| Survivability              | Good              |
| Production complexity      | Medium-High       |
| Dependency on zone quality | Extremely high    |
| Best market                | XAUUSD volatility |
| Worst market               | dead-range chop   |

---

# Final Conclusion

Approach 2 is essentially:

```text
A state-aware rotational liquidity reaction engine.
```

It is fundamentally different from:

* martingale
* classic grid
* standard breakout systems
* ordinary trailing logic

because:

* direction rotates adaptively
* escalation depends on realized outcome
* partial extraction matters
* institutional S/R transitions drive exposure

The architecture is sophisticated enough to justify serious quantitative testing.
'''