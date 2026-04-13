"""
Dual-speed HFT bracket scalper for MT5.

Pending bracket updates are rate limited.
Fill detection and trailing SL management run on a much faster loop.
"""

import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5


# MT5 Connection
MT5_PATH = ""
LOGIN = 334675134
PASSWORD = "Soham@987*#"
SERVER = "XMGlobal-MT5 9"

# Symbol and sizing
SYMBOL = "GOLD.i#"
LOT_SIZE = 0.01

# Strategy parameters
K_POINTS = 50
TRAIL_POINTS = 30
BRACKET_UPDATE_MS = 250
FAST_POLL_MS = 10
RECENTER_THRESHOLD_POINTS = 2
MIN_SL_STEP_POINTS = 1
SPREAD_MULTIPLIER = 1.2

# Order management
MAGIC_NUMBER = 888888
SLIPPAGE = 10

# Safety limits
MAX_TRADES = 0
MAX_LOSS = 0.0

# Session window in UTC, -1 disables
SESSION_START_HOUR = -1
SESSION_END_HOUR = -1


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("HFTBracketDualSpeed")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(
        f"hft_modified_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)
    return logger


log = setup_logger()


def connect_mt5() -> bool:
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

    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        log.error(f"Symbol {SYMBOL} not found")
        return False
    if not symbol_info.visible and not mt5.symbol_select(SYMBOL, True):
        log.error(f"Failed to select symbol {SYMBOL}")
        return False

    log.info(f"Connected: {account.login} @ {account.server}")
    log.info(f"Symbol: {SYMBOL} | Point: {symbol_info.point} | Spread: {symbol_info.spread}")
    return True


def get_symbol_info():
    return mt5.symbol_info(SYMBOL)


def get_tick():
    return mt5.symbol_info_tick(SYMBOL)


def get_price_snapshot() -> Optional[tuple]:
    tick = get_tick()
    if tick is None:
        return None
    mid = round((tick.bid + tick.ask) / 2, 10)
    return tick.bid, tick.ask, mid


def get_point() -> float:
    info = get_symbol_info()
    return info.point if info else 0.00001


def get_digits() -> int:
    info = get_symbol_info()
    return info.digits if info else 5


def get_our_pending_orders() -> list:
    orders = mt5.orders_get(symbol=SYMBOL)
    if orders is None:
        return []
    return [order for order in orders if order.magic == MAGIC_NUMBER]


def get_our_positions() -> list:
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return []
    return [position for position in positions if position.magic == MAGIC_NUMBER]


def cancel_order(ticket: int) -> bool:
    result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning(f"Cancel order {ticket} failed: {result}")
        return False
    return True


def cancel_all_our_orders() -> None:
    orders = get_our_pending_orders()
    for order in orders:
        cancel_order(order.ticket)
    if orders:
        log.debug(f"Cancelled {len(orders)} pending order(s)")


def place_stop_order(order_type: int, price: float, comment: str = "") -> Optional[int]:
    price = round(price, get_digits())
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
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning(f"Place order failed: type={order_type} price={price} | {result}")
        return None
    log.debug(f"Order placed: ticket={result.order} type={order_type} price={price}")
    return result.order


def modify_order(order_ticket: int, price: float) -> bool:
    price = round(price, get_digits())
    request = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": order_ticket,
        "price": price,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning(f"Modify order failed: ticket={order_ticket} price={price} | {result}")
        return False
    return True


def modify_sl(position_ticket: int, new_sl: float) -> bool:
    new_sl = round(new_sl, get_digits())
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


class DualSpeedBracketScalper:
    def __init__(self):
        self.point = get_point()
        self.digits = get_digits()
        self.k_price = K_POINTS * self.point
        self.trail_price = TRAIL_POINTS * self.point
        self.recenter_threshold = max(RECENTER_THRESHOLD_POINTS * self.point, self.point)
        self.min_sl_step = max(MIN_SL_STEP_POINTS * self.point, self.point)
        self.fast_poll_sec = max(FAST_POLL_MS / 1000.0, 0.001)
        self.bracket_interval_sec = max(BRACKET_UPDATE_MS / 1000.0, self.fast_poll_sec)

        self.running = True
        self.in_trade = False
        self.position_ticket: Optional[int] = None
        self.position_type: Optional[int] = None
        self.entry_price: Optional[float] = None
        self.entry_time_ms: Optional[int] = None
        self.best_price: Optional[float] = None
        self.last_bracket_mid: Optional[float] = None
        self.last_bracket_update_ts = 0.0
        self.cached_position = None
        self.last_bid: Optional[float] = None
        self.last_ask: Optional[float] = None

        self.trade_count = 0
        self.cumulative_pnl = 0.0

        log.info("Dual-speed bracket scalper initialized")
        log.info(f"Symbol: {SYMBOL} | Lot: {LOT_SIZE}")
        log.info(f"Bracket update: {BRACKET_UPDATE_MS}ms | Fast loop: {FAST_POLL_MS}ms")
        log.info(f"k: {self.k_price:.{self.digits}f} | Trail: {self.trail_price:.{self.digits}f}")
        log.info(f"Recenter threshold: {self.recenter_threshold:.{self.digits}f}")
        log.info(f"Min SL step: {self.min_sl_step:.{self.digits}f}")

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

    def refresh_position_cache(self):
        positions = get_our_positions()
        if not positions:
            self.cached_position = None
            return None

        if len(positions) > 1:
            log.warning(f"Multiple positions detected for magic {MAGIC_NUMBER}; using first position")
        self.cached_position = positions[0]
        return self.cached_position

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

    def _cleanup_extra_pending_orders(self, buy_orders, sell_orders, extra_orders) -> None:
        has_duplicates = len(buy_orders) > 1 or len(sell_orders) > 1
        if not has_duplicates and not extra_orders:
            return

        for order in buy_orders[1:] + sell_orders[1:] + extra_orders:
            cancel_order(order.ticket)

    def _sync_pending_order(self, existing_order, order_type: int, target_price: float, comment: str) -> bool:
        target_price = round(target_price, self.digits)

        if existing_order is None:
            return place_stop_order(order_type, target_price, comment=comment) is not None

        existing_price = existing_order.price_open
        if abs(existing_price - target_price) < self.recenter_threshold:
            return False

        if order_type == mt5.ORDER_TYPE_BUY_STOP and target_price <= existing_price:
            return False

        if order_type == mt5.ORDER_TYPE_SELL_STOP and target_price >= existing_price:
            return False

        return modify_order(existing_order.ticket, target_price)

    def update_bracket(self) -> None:
        snapshot = get_price_snapshot()
        if snapshot is None:
            log.warning("No tick data for bracket update")
            return

        bid, ask, mid = snapshot
        if self.last_bracket_mid is not None and abs(mid - self.last_bracket_mid) < self.recenter_threshold:
            return

        spread = max(ask - bid, self.point)
        stop_buffer = max(self.k_price, spread * SPREAD_MULTIPLIER)
        buy_price = ask + stop_buffer
        sell_price = bid - stop_buffer

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
            log.debug(
                f"Bracket sync | BUY@{buy_price:.{self.digits}f} "
                f"| SELL@{sell_price:.{self.digits}f} | spread={spread:.{self.digits}f}"
            )

    def _start_trade_from_cached_position(self) -> bool:
        pos = self.cached_position
        if pos is None:
            return False

        self.position_ticket = pos.ticket
        self.position_type = pos.type
        self.entry_price = pos.price_open
        self.entry_time_ms = int(time.time() * 1000)

        snapshot = get_price_snapshot()
        if snapshot is not None:
            bid, ask, _ = snapshot
            self.best_price = bid if pos.type == mt5.ORDER_TYPE_BUY else ask
        else:
            self.best_price = pos.price_open

        cancel_all_our_orders()
        self.last_bracket_mid = None
        self.in_trade = True
        self.trade_count += 1

        if pos.type == mt5.ORDER_TYPE_BUY:
            initial_sl = self.entry_price - self.trail_price
            direction = "LONG"
        else:
            initial_sl = self.entry_price + self.trail_price
            direction = "SHORT"

        modify_sl(pos.ticket, initial_sl)
        log.info(f"FILLED {direction} @ {self.entry_price}")
        log.debug(f"Initial SL: {initial_sl:.{self.digits}f}")
        return True

    def check_for_fill(self) -> bool:
        if self.cached_position is None:
            return False
        if self.in_trade and self.position_ticket == self.cached_position.ticket:
            return False
        return self._start_trade_from_cached_position()

    def trail_stop_loss(self) -> None:
        pos = self.cached_position
        if pos is None:
            self._on_position_closed()
            return

        snapshot = get_price_snapshot()
        if snapshot is None:
            return

        bid, ask, _ = snapshot
        current_sl = pos.sl

        if pos.type == mt5.ORDER_TYPE_BUY:
            if self.best_price is None or bid > self.best_price:
                self.best_price = bid
            ideal_sl = self.best_price - self.trail_price
            should_move = current_sl == 0.0 or (ideal_sl - current_sl) >= self.min_sl_step
        else:
            if self.best_price is None or ask < self.best_price:
                self.best_price = ask
            ideal_sl = self.best_price + self.trail_price
            should_move = current_sl == 0.0 or (current_sl - ideal_sl) >= self.min_sl_step

        if should_move and modify_sl(pos.ticket, ideal_sl):
            self.cached_position = self.refresh_position_cache()
            if pos.type == mt5.ORDER_TYPE_BUY:
                log.debug(f"Trail SL up to {ideal_sl:.{self.digits}f} | bid={bid:.{self.digits}f}")
            else:
                log.debug(f"Trail SL down to {ideal_sl:.{self.digits}f} | ask={ask:.{self.digits}f}")

    def handle_fast_tick(self) -> None:
        tick = get_tick()
        if tick is None:
            return

        bid_changed = self.last_bid is None or tick.bid != self.last_bid
        ask_changed = self.last_ask is None or tick.ask != self.last_ask
        self.last_bid = tick.bid
        self.last_ask = tick.ask

        if self.in_trade:
            cancel_all_our_orders()
            if bid_changed or ask_changed:
                self.trail_stop_loss()
            return

        orders = get_our_pending_orders()
        if not orders:
            self.refresh_position_cache()
            self.check_for_fill()

    def _on_position_closed(self) -> None:
        if not self.in_trade:
            return

        pnl = self._get_last_pnl()
        self.cumulative_pnl += pnl
        duration_ms = int(time.time() * 1000) - (self.entry_time_ms or 0)
        direction = "LONG" if self.position_type == mt5.ORDER_TYPE_BUY else "SHORT"

        log.info(
            f"CLOSED {direction} | PnL: {pnl:+.2f} | Duration: {duration_ms}ms "
            f"| Session: {self.cumulative_pnl:+.2f} | Trades: {self.trade_count}"
        )

        self.in_trade = False
        self.position_ticket = None
        self.position_type = None
        self.entry_price = None
        self.entry_time_ms = None
        self.best_price = None
        self.last_bracket_mid = None
        self.cached_position = None
        self.last_bid = None
        self.last_ask = None

    def _get_last_pnl(self) -> float:
        to_dt = datetime.now(timezone.utc)
        from_dt = datetime.fromtimestamp(to_dt.timestamp() - 60, tz=timezone.utc)
        deals = mt5.history_deals_get(from_dt, to_dt, group=SYMBOL)
        if not deals:
            return 0.0
        our_deals = [deal for deal in deals if deal.magic == MAGIC_NUMBER and deal.entry == 1]
        if not our_deals:
            return 0.0
        return our_deals[-1].profit

    def shutdown(self) -> None:
        log.info("Shutting down dual-speed bracket scalper")
        cancel_all_our_orders()

        positions = get_our_positions()
        for pos in positions:
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = get_tick()
            if tick is None:
                continue
            close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
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

    def run(self) -> None:
        log.info("Starting dual-speed bracket scalper")

        while self.running:
            try:
                if not self.is_within_session():
                    cancel_all_our_orders()
                    self.last_bracket_mid = None
                    time.sleep(1.0)
                    continue

                if not self.limits_ok():
                    cancel_all_our_orders()
                    log.info("Limits reached, shutting down.")
                    break

                self.handle_fast_tick()

                if self.in_trade:
                    if self.cached_position is None:
                        self.refresh_position_cache()
                    cancel_all_our_orders()
                else:
                    self.refresh_position_cache()
                    self.check_for_fill()
                    now = time.perf_counter()
                    if (now - self.last_bracket_update_ts) >= self.bracket_interval_sec:
                        self.update_bracket()
                        self.last_bracket_update_ts = now

            except Exception as exc:
                log.error(f"Loop error: {exc}", exc_info=True)

            time.sleep(self.fast_poll_sec)


def main() -> None:
    if not connect_mt5():
        sys.exit(1)

    scalper = DualSpeedBracketScalper()

    def handle_signal(sig, frame):
        log.info("Interrupt received, stopping bot")
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
