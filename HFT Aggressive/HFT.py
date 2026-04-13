"""
HFT Bracket Scalper — MT5
─────────────────────────
Places buy-stop and sell-stop orders at ±k from CMP.
Re-centers every n ms. On fill, trails SL aggressively.
On SL hit, restarts bracket from current price.

Requirements:
    pip install MetaTrader5
"""

import MetaTrader5 as mt5
import time
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Optional


# ═════════════════════════════════════════════════════════
#  CONFIGURATION — EDIT THESE VALUES
# ═════════════════════════════════════════════════════════

# ── MT5 Connection ──
# Leave MT5_PATH empty to use default installation path
# Set LOGIN to 0 to use the currently logged-in account
MT5_PATH    = ""
LOGIN       = 334675134
PASSWORD    = "Soham@987*#"
SERVER      = "XMGlobal-MT5 9"

# ── Symbol & Sizing ──
SYMBOL      = "GOLD.i#"       # Trading pair
LOT_SIZE    = 0.01            # Position size per trade

# ── Core Strategy Parameters ──
K_POINTS        = 50          # Distance from CMP for stop orders (in points)
                               # ↑ Increase = fewer fills, only catches bigger moves
                               # ↓ Decrease = more fills, catches smaller moves but more noise
TRAIL_POINTS    = 30          # Trailing SL distance (in points)
                               # ↑ Increase = gives more room, captures bigger moves but gives back more on reversal
                               # ↓ Decrease = locks profit tighter, but gets stopped on normal pullbacks
UPDATE_MS       = 250         # How often to re-center bracket orders (milliseconds)
                               # ↓ Lower = more responsive, more API calls
                               # ↑ Higher = less responsive, orders stay stale longer
RECENTER_THRESHOLD_POINTS = 2  # Minimum drift before modifying bracket orders

# ── Order Management ──
MAGIC_NUMBER    = 888888      # Unique ID to identify our orders (change if running multiple bots)
SLIPPAGE        = 10          # Max allowed slippage in points on order send

# ── Safety Limits (set to 0 to disable) ──
MAX_TRADES      = 0           # Stop after this many trades (0 = unlimited)
MAX_LOSS        = 0.0         # Stop if cumulative loss exceeds this amount (0 = unlimited)

# ── Session Window (set to -1 to disable) ──
# Hours are in UTC. Bot only places new brackets within this window.
SESSION_START_HOUR  = -1      # e.g., 7 = start at 07:00 UTC
SESSION_END_HOUR    = -1      # e.g., 16 = stop at 16:00 UTC

# ═════════════════════════════════════════════════════════
#  END OF CONFIGURATION — NO NEED TO EDIT BELOW THIS LINE
# ═════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────────────────

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("HFTBracket")
    logger.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(ch)

    fh = logging.FileHandler(
        f"hft_bracket_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d │ %(levelname)-5s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    return logger


log = setup_logger()


# ─────────────────────────────────────────────────────────
# MT5 Helpers
# ─────────────────────────────────────────────────────────

def connect_mt5() -> bool:
    """Initialize MT5 connection and validate symbol."""
    init_kwargs = {}
    if MT5_PATH:
        init_kwargs["path"] = MT5_PATH
    if LOGIN:
        init_kwargs["login"] = LOGIN
        init_kwargs["password"] = PASSWORD
        init_kwargs["server"] = SERVER

    if not mt5.initialize(**init_kwargs):
        log.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False

    account = mt5.account_info()
    if account is None:
        log.error("Cannot retrieve account info")
        return False

    log.info(f"Connected: {account.login} @ {account.server}")
    log.info(f"Balance: {account.balance}  Leverage: 1:{account.leverage}")

    sym = mt5.symbol_info(SYMBOL)
    if sym is None:
        log.error(f"Symbol {SYMBOL} not found")
        return False
    if not sym.visible:
        mt5.symbol_select(SYMBOL, True)

    log.info(f"Symbol: {SYMBOL}  Point: {sym.point}  Spread: {sym.spread}")
    return True


def get_cmp() -> Optional[tuple]:
    """Returns (bid, ask, mid) or None."""
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return None
    mid = round((tick.bid + tick.ask) / 2, 10)
    return tick.bid, tick.ask, mid


def get_point() -> float:
    info = mt5.symbol_info(SYMBOL)
    return info.point if info else 0.00001


def get_digits() -> int:
    info = mt5.symbol_info(SYMBOL)
    return info.digits if info else 5


def get_our_pending_orders() -> list:
    orders = mt5.orders_get(symbol=SYMBOL)
    if orders is None:
        return []
    return [o for o in orders if o.magic == MAGIC_NUMBER]


def get_our_positions() -> list:
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return []
    return [p for p in positions if p.magic == MAGIC_NUMBER]


def cancel_order(ticket: int) -> bool:
    request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning(f"Cancel order {ticket} failed: {result}")
        return False
    return True


def cancel_all_our_orders():
    orders = get_our_pending_orders()
    for o in orders:
        cancel_order(o.ticket)
    if orders:
        log.debug(f"Cancelled {len(orders)} pending order(s)")


def place_stop_order(order_type: int, price: float, comment: str = "") -> Optional[int]:
    """Place a buy-stop or sell-stop. Returns ticket or None."""
    digits = get_digits()
    price = round(price, digits)

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": SYMBOL,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": price,
        "deviation": SLIPPAGE,
        "magic": MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
        # ↑ If orders fail to place on XM, try changing to one of these:
        #   mt5.ORDER_FILLING_FOK
        #   mt5.ORDER_FILLING_RETURN
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning(f"Place order failed: type={order_type} price={price} | {result}")
        return None

    log.debug(f"Order placed: ticket={result.order} type={order_type} price={price}")
    return result.order


def modify_order(order_ticket: int, price: float) -> bool:
    digits = get_digits()
    price = round(price, digits)

    request = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": order_ticket,
        "price": price,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning(f"Modify order failed: ticket={order_ticket} price={price} | {result}")
        return False

    log.debug(f"Order modified: ticket={order_ticket} price={price}")
    return True


def modify_sl(position_ticket: int, new_sl: float) -> bool:
    digits = get_digits()
    new_sl = round(new_sl, digits)

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position_ticket,
        "symbol": SYMBOL,
        "sl": new_sl,
        "tp": 0.0,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.debug(f"Modify SL failed: ticket={position_ticket} sl={new_sl} | {result}")
        return False
    return True


# ─────────────────────────────────────────────────────────
# Core Strategy
# ─────────────────────────────────────────────────────────

class BracketScalper:
    def __init__(self):
        self.point = get_point()
        self.digits = get_digits()
        self.k_price = K_POINTS * self.point
        self.trail_price = TRAIL_POINTS * self.point
        self.recenter_threshold = max(RECENTER_THRESHOLD_POINTS * self.point, self.point)

        # State
        self.in_trade = False
        self.position_ticket: Optional[int] = None
        self.position_type: Optional[int] = None      # 0 = BUY, 1 = SELL
        self.best_price: Optional[float] = None        # High watermark (long) / low watermark (short)
        self.entry_price: Optional[float] = None
        self.entry_time_ms: Optional[int] = None
        self.last_bracket_mid: Optional[float] = None

        # Session stats
        self.trade_count = 0
        self.cumulative_pnl = 0.0
        self.running = True

        log.info("─── Bracket Scalper Initialized ───")
        log.info(f"Symbol: {SYMBOL}  Lot: {LOT_SIZE}")
        log.info(f"k: {K_POINTS} pts ({self.k_price:.{self.digits}f})")
        log.info(f"Trail: {TRAIL_POINTS} pts ({self.trail_price:.{self.digits}f})")
        log.info(f"Update interval: {UPDATE_MS} ms")
        log.info(
            f"Recenter threshold: {RECENTER_THRESHOLD_POINTS} pts "
            f"({self.recenter_threshold:.{self.digits}f})"
        )
        log.info(f"Magic: {MAGIC_NUMBER}")

    # ── Safety ──

    def is_within_session(self) -> bool:
        if SESSION_START_HOUR < 0 and SESSION_END_HOUR < 0:
            return True
        now_utc = datetime.now(timezone.utc).hour
        if SESSION_START_HOUR >= 0 and now_utc < SESSION_START_HOUR:
            return False
        if SESSION_END_HOUR >= 0 and now_utc >= SESSION_END_HOUR:
            return False
        return True

    def limits_ok(self) -> bool:
        if MAX_TRADES > 0 and self.trade_count >= MAX_TRADES:
            log.warning(f"Max trades reached: {self.trade_count}")
            return False
        if MAX_LOSS > 0 and self.cumulative_pnl <= -MAX_LOSS:
            log.warning(f"Max loss reached: {self.cumulative_pnl:.2f}")
            return False
        return True

    # ── IDLE: Place / Update Bracket ──

    def _split_pending_orders(self):
        buy_orders = []
        sell_orders = []
        extra_orders = []

        for order in get_our_pending_orders():
            if order.type == mt5.ORDER_TYPE_BUY_STOP:
                buy_orders.append(order)
            elif order.type == mt5.ORDER_TYPE_SELL_STOP:
                sell_orders.append(order)
            else:
                extra_orders.append(order)

        return buy_orders, sell_orders, extra_orders

    def _cleanup_extra_pending_orders(self, buy_orders, sell_orders, extra_orders):
        for order in buy_orders[1:] + sell_orders[1:] + extra_orders:
            cancel_order(order.ticket)

    def _sync_pending_order(self, existing_order, order_type: int, target_price: float, comment: str) -> bool:
        target_price = round(target_price, self.digits)

        if existing_order is None:
            return place_stop_order(order_type, target_price, comment=comment) is not None

        if abs(existing_order.price_open - target_price) < self.recenter_threshold:
            return False

        return modify_order(existing_order.ticket, target_price)

    def update_bracket(self):
        prices = get_cmp()
        if prices is None:
            log.warning("No tick data")
            return

        bid, ask, mid = prices

        if self.last_bracket_mid is not None and abs(mid - self.last_bracket_mid) < self.recenter_threshold:
            return

        buy_price = ask + self.k_price
        sell_price = bid - self.k_price

        buy_orders, sell_orders, extra_orders = self._split_pending_orders()
        self._cleanup_extra_pending_orders(buy_orders, sell_orders, extra_orders)

        primary_buy = buy_orders[0] if buy_orders else None
        primary_sell = sell_orders[0] if sell_orders else None

        buy_changed = self._sync_pending_order(
            primary_buy, mt5.ORDER_TYPE_BUY_STOP, buy_price, comment="HFT-BUY"
        )
        sell_changed = self._sync_pending_order(
            primary_sell, mt5.ORDER_TYPE_SELL_STOP, sell_price, comment="HFT-SELL"
        )

        if buy_changed or sell_changed or self.last_bracket_mid is None:
            self.last_bracket_mid = mid
            log.debug(f"Bracket: BUY@{buy_price} | SELL@{sell_price} | mid={mid}")

    # ── Check for Fill ──

    def check_for_fill(self) -> bool:
        positions = get_our_positions()
        if not positions:
            return False

        pos = positions[0]
        self.position_ticket = pos.ticket
        self.position_type = pos.type
        self.entry_price = pos.price_open
        self.entry_time_ms = int(time.time() * 1000)

        # Initialize watermark
        prices = get_cmp()
        if prices:
            bid, ask, _ = prices
            self.best_price = bid if pos.type == mt5.ORDER_TYPE_BUY else ask

        cancel_all_our_orders()

        direction = "LONG" if pos.type == mt5.ORDER_TYPE_BUY else "SHORT"
        log.info(f"▶ FILLED {direction} @ {self.entry_price}")

        # Set initial SL
        if pos.type == mt5.ORDER_TYPE_BUY:
            sl = self.entry_price - self.trail_price
        else:
            sl = self.entry_price + self.trail_price
        modify_sl(pos.ticket, sl)
        log.debug(f"Initial SL: {sl}")

        self.in_trade = True
        self.last_bracket_mid = None
        self.trade_count += 1
        return True

    # ── IN_TRADE: Trail SL ──

    def trail_stop_loss(self):
        positions = get_our_positions()

        # Position gone — SL hit or closed externally
        if not positions:
            self._on_position_closed()
            return

        pos = positions[0]
        prices = get_cmp()
        if prices is None:
            return

        bid, ask, _ = prices
        current_sl = pos.sl

        if pos.type == mt5.ORDER_TYPE_BUY:
            if bid > self.best_price:
                self.best_price = bid
            ideal_sl = self.best_price - self.trail_price
            # Only move SL up, never down
            if current_sl == 0.0 or ideal_sl > current_sl:
                if modify_sl(pos.ticket, ideal_sl):
                    log.debug(f"Trail SL ↑ {ideal_sl:.5f}  (bid={bid})")
        else:
            if ask < self.best_price:
                self.best_price = ask
            ideal_sl = self.best_price + self.trail_price
            # Only move SL down, never up
            if current_sl == 0.0 or ideal_sl < current_sl:
                if modify_sl(pos.ticket, ideal_sl):
                    log.debug(f"Trail SL ↓ {ideal_sl:.5f}  (ask={ask})")

    def _on_position_closed(self):
        pnl = self._get_last_pnl()
        self.cumulative_pnl += pnl

        duration_ms = int(time.time() * 1000) - (self.entry_time_ms or 0)
        direction = "LONG" if self.position_type == mt5.ORDER_TYPE_BUY else "SHORT"
        icon = "✅" if pnl >= 0 else "❌"

        log.info(
            f"{icon} CLOSED {direction} | "
            f"PnL: {pnl:+.2f} | "
            f"Duration: {duration_ms}ms | "
            f"Session: {self.cumulative_pnl:+.2f} | "
            f"Trades: {self.trade_count}"
        )

        # Reset
        self.in_trade = False
        self.position_ticket = None
        self.position_type = None
        self.best_price = None
        self.entry_price = None
        self.entry_time_ms = None
        self.last_bracket_mid = None

    def _get_last_pnl(self) -> float:
        from_time = datetime.now(timezone.utc).timestamp() - 60
        from_dt = datetime.fromtimestamp(from_time, tz=timezone.utc)
        deals = mt5.history_deals_get(from_dt, datetime.now(timezone.utc), group=SYMBOL)
        if deals is None or len(deals) == 0:
            return 0.0
        # entry=1 means an exit deal
        # ↑ If PnL always shows 0, XM might use a different value.
        #   Debug by adding:  print([d.entry for d in deals])
        our_deals = [d for d in deals if d.magic == MAGIC_NUMBER and d.entry == 1]
        if not our_deals:
            return 0.0
        return our_deals[-1].profit

    # ── Main Loop ──

    def run(self):
        log.info("━━━ STARTING BRACKET SCALPER ━━━")
        interval_sec = UPDATE_MS / 1000.0

        while self.running:
            loop_start = time.perf_counter()

            try:
                if not self.is_within_session():
                    cancel_all_our_orders()
                    time.sleep(1)
                    continue

                if not self.limits_ok():
                    cancel_all_our_orders()
                    log.info("Limits reached — shutting down.")
                    break

                if not self.in_trade:
                    self.check_for_fill()
                    if not self.in_trade:
                        self.update_bracket()
                else:
                    self.trail_stop_loss()

            except Exception as e:
                log.error(f"Loop error: {e}", exc_info=True)

            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0, interval_sec - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def shutdown(self):
        log.info("━━━ SHUTTING DOWN ━━━")
        cancel_all_our_orders()

        # Close any open position on shutdown
        positions = get_our_positions()
        for pos in positions:
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            prices = get_cmp()
            if prices:
                bid, ask, _ = prices
                close_price = bid if close_type == mt5.ORDER_TYPE_SELL else ask
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": SYMBOL,
                    "volume": pos.volume,
                    "type": close_type,
                    "price": close_price,
                    "position": pos.ticket,
                    "deviation": SLIPPAGE,
                    "magic": MAGIC_NUMBER,
                    "comment": "HFT-SHUTDOWN",
                }
                result = mt5.order_send(request)
                log.info(f"Close position {pos.ticket}: {result}")

        log.info(f"Final Session PnL: {self.cumulative_pnl:+.2f}")
        log.info(f"Total Trades: {self.trade_count}")
        mt5.shutdown()


# ─────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────

def main():
    if not connect_mt5():
        sys.exit(1)

    scalper = BracketScalper()

    def handle_signal(sig, frame):
        log.info("Interrupt received...")
        scalper.running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        scalper.run()
    except KeyboardInterrupt:
        pass
    finally:
        scalper.shutdown()


if __name__ == "__main__":
    main()
