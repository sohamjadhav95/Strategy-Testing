# HFT Bracket Scalper Strategy

This project runs a simple MT5 breakout bracket strategy.

It continuously watches the current market price and keeps two pending stop orders around it:

- one `BUY_STOP` above the current ask
- one `SELL_STOP` below the current bid

The idea is to catch whichever side breaks out first, then manage the open trade with a tight trailing stop loss.

## Strategy Summary

The bot has two phases.

### 1. Idle Phase

When there is no open position, the bot:

- reads the latest `bid`, `ask`, and midpoint
- calculates a buy trigger at `ask + K_POINTS`
- calculates a sell trigger at `bid - K_POINTS`
- keeps one buy-stop and one sell-stop pending
- modifies existing pending orders only when price drift is large enough

This is important because the strategy is designed to be stateful and low-latency. It does not blindly cancel and recreate orders on every loop.

### 2. In-Trade Phase

When one side gets filled, the bot:

- detects the open position
- cancels the opposite pending order
- sets an initial stop loss at `TRAIL_POINTS` distance from entry
- tracks the best favorable price seen after entry
- moves the stop loss only in the direction of profit

If the stop loss is hit or the position is closed externally, the bot resets and starts building a fresh bracket from the current market again.

## Core Logic

### Entry Logic

- `BUY_STOP` is always placed above the market using `ask + k`
- `SELL_STOP` is always placed below the market using `bid - k`

This separation is what keeps one order above and one below the current market price.

### Re-centering Logic

The bot checks for price movement every `UPDATE_MS`.

To avoid excessive MT5/API traffic, it only updates pending orders when:

- the midpoint has moved enough from the last synced level, or
- the current pending order price is far enough from the desired target

That makes the strategy more efficient than a cancel-and-recreate loop.

### Stop Loss Logic

For a long trade:

- initial SL = `entry - TRAIL_POINTS`
- as bid rises, the SL is raised
- the SL is never moved downward

For a short trade:

- initial SL = `entry + TRAIL_POINTS`
- as ask falls, the SL is lowered
- the SL is never moved upward

## Why This Strategy Exists

This bot is trying to capture fast directional moves after price breaks away from the current range.

The design goal is:

- quick entry around the live market
- minimal order-management lag
- low unnecessary broker/API load
- aggressive protection once a move starts

## Main Parameters

These values are configured at the top of `HFT.py`.

- `SYMBOL`: instrument to trade
- `LOT_SIZE`: volume per trade
- `K_POINTS`: breakout distance from current market price
- `TRAIL_POINTS`: trailing stop distance
- `UPDATE_MS`: loop frequency
- `RECENTER_THRESHOLD_POINTS`: minimum drift before modifying pending orders
- `MAGIC_NUMBER`: identifier for this bot's orders/positions
- `SLIPPAGE`: allowed execution deviation
- `MAX_TRADES`: optional max trades for the session
- `MAX_LOSS`: optional max session loss cutoff
- `SESSION_START_HOUR` / `SESSION_END_HOUR`: optional UTC session filter

## Order Lifecycle

The strategy tries to minimize unnecessary order operations.

Preferred behavior:

1. place pending orders if they do not exist
2. modify them if price moved enough
3. cancel only duplicates, invalid leftovers, or the opposite side after a fill

This is better than canceling all orders every cycle because cancel/recreate loops add latency and can cause unstable order placement around fast ticks.

## Risks and Limitations

This is still a live breakout strategy, so it has important risks:

- false breakouts can trigger and reverse quickly
- spreads can widen during volatility
- fast ticks can move beyond the intended stop distance
- broker execution rules can reject or adjust pending order requests
- very small thresholds can still create too many modifications

## Typical Flow

1. Bot starts and connects to MT5.
2. It places one buy-stop above price and one sell-stop below price.
3. Market breaks one side and opens a position.
4. Opposite pending order is removed.
5. Trailing SL follows the move.
6. Position closes on SL or manual/other closure.
7. Bot returns to idle phase and rebuilds the bracket.

## Notes

- This is not market making.
- This is not a hedge grid.
- This is a breakout bracket with trailing stop management.
- Efficiency depends on modifying state instead of rebuilding it repeatedly.
