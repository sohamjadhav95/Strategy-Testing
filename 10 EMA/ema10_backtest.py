"""
=============================================================
  10 EMA STRATEGY BACKTESTER — USDJPY 1-Min
=============================================================
  Logic:
    BUY : First non-touch candle after last EMA-touch sequence
          = Triggered Candle.  If NEXT non-touch candle breaks
          Triggered Candle's HIGH → BUY at (trig_high + TICK)
    SELL: Mirror. SELL at (trig_low - TICK)
    Reset: Any candle that touches EMA resets the sequence.
           Roll-forward: each subsequent non-touch candle
           becomes the new triggered candle for the next.

  Configure the 8 lines below and re-run.
=============================================================
"""

import pandas as pd
import numpy as np

# ============================================================
#  CONFIGURATION — edit these lines to test parameters
# ============================================================
TICK          = 0.001        # USDJPY pip/tick size

SL_MODE       = 'atr'        # 'atr' | 'triggered_candle' | 'signal_candle'
ATR_PERIOD    = 14           # used when SL_MODE = 'atr'

TP_MODE       = 'ratio'      # 'ratio' | 'trailing'
TP_RATIO      = 1.5            # 2 → 1:2 RR,  3 → 1:3 RR  (only for TP_MODE='ratio')
                             # trailing: grid = SL distance, no fixed TP target

ALLOW_OVERLAP = False        # True  → open new trade even if one is running
                             # False → wait for current trade to close first

INPUT_FILE    = r'E:\Projects\Experiments\Strategy Testing\10 EMA\USDJPY_with_EMA.csv'
OUTPUT_FILE   = r'E:\Projects\Experiments\Strategy Testing\10 EMA\strategy_output.csv'   # annotated OHLCV
TRADES_FILE   = r'E:\Projects\Experiments\Strategy Testing\10 EMA\trade_results.csv'     # per-trade log
# ============================================================


# -----------------------------------------------------------
#  Helpers
# -----------------------------------------------------------
def wilder_atr(H, L, C, period):
    n  = len(H)
    tr = np.zeros(n)
    tr[0] = H[0] - L[0]
    for i in range(1, n):
        tr[i] = max(H[i] - L[i],
                    abs(H[i] - C[i-1]),
                    abs(L[i] - C[i-1]))
    atr = np.full(n, np.nan)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr


class Trade:
    """Holds one trade's full lifecycle."""
    __slots__ = [
        'open_idx', 'dir', 'entry', 'sl', 'initial_sl',
        'sl_dist', 'tp_mode', 'tp_ratio', 'tp_val',
        'status', 'exit_price', 'exit_idx', 'pnl_pips',
        'exit_reason', 'best_price'
    ]

    def __init__(self, idx, direction, entry, sl, sl_dist,
                 tp_mode, tp_ratio, tp_val):
        self.open_idx   = idx
        self.dir        = direction       # 'BUY' | 'SELL'
        self.entry      = entry
        self.sl         = sl              # current (trailing) SL
        self.initial_sl = sl
        self.sl_dist    = sl_dist
        self.tp_mode    = tp_mode
        self.tp_ratio   = tp_ratio
        self.tp_val     = tp_val          # static TP level (ratio mode)
        self.status     = 'OPEN'
        self.exit_price = None
        self.exit_idx   = None
        self.pnl_pips   = None
        self.exit_reason= None
        self.best_price = entry           # tracks best excursion for trailing

    def update(self, idx, o, h, l):
        if self.dir == 'BUY':
            self._update_buy(idx, o, h, l)
        else:
            self._update_sell(idx, o, h, l)

    def _update_buy(self, idx, o, h, l):
        # --- Trailing SL update (BEFORE exit check) ---
        if self.tp_mode == 'trailing':
            self.best_price = max(self.best_price, h)
            if self.best_price > self.entry:
                steps  = int((self.best_price - self.entry) / self.sl_dist)
                new_sl = self.initial_sl + steps * self.sl_dist
                self.sl = max(self.sl, new_sl)   # SL can only move UP

        # --- Exit checks (SL priority over TP on same candle) ---
        if o <= self.sl:
            self._close(idx, o, 'SL')            # gap-down open below SL
        elif l <= self.sl:
            self._close(idx, self.sl, 'SL')      # wick hits SL
        elif self.tp_mode == 'ratio' and h >= self.tp_val:
            self._close(idx, self.tp_val, 'TP')  # TP reached

    def _update_sell(self, idx, o, h, l):
        # --- Trailing SL update ---
        if self.tp_mode == 'trailing':
            self.best_price = min(self.best_price, l)
            if self.best_price < self.entry:
                steps  = int((self.entry - self.best_price) / self.sl_dist)
                new_sl = self.initial_sl - steps * self.sl_dist
                self.sl = min(self.sl, new_sl)   # SL can only move DOWN

        # --- Exit checks ---
        if o >= self.sl:
            self._close(idx, o, 'SL')
        elif h >= self.sl:
            self._close(idx, self.sl, 'SL')
        elif self.tp_mode == 'ratio' and l <= self.tp_val:
            self._close(idx, self.tp_val, 'TP')

    def _close(self, idx, price, reason):
        self.status      = 'CLOSED'
        self.exit_price  = round(float(price), 5)
        self.exit_idx    = idx
        self.exit_reason = reason
        if self.dir == 'BUY':
            self.pnl_pips = round((price - self.entry) / TICK)
        else:
            self.pnl_pips = round((self.entry - price) / TICK)


# -----------------------------------------------------------
#  Main backtest
# -----------------------------------------------------------
def run_backtest():
    print(f"\nLoading data …")
    df = pd.read_csv(INPUT_FILE)
    df.columns = df.columns.str.upper()
    df['DATETIME'] = pd.to_datetime(df['DATE'] + ' ' + df['TIME'])
    df = df.sort_values('DATETIME').reset_index(drop=True)
    n  = len(df)
    print(f"  Rows : {n:,}  ({df['DATETIME'].iloc[0]} → {df['DATETIME'].iloc[-1]})")

    H  = df['HIGH'].values
    L  = df['LOW'].values
    O  = df['OPEN'].values
    C  = df['CLOSE'].values
    EMA= df['EMA_10'].values

    # ATR
    ATR = wilder_atr(H, L, C, ATR_PERIOD)
    df['ATR_14'] = ATR

    # ----------------------------------------------------------
    #  Signal columns
    # ----------------------------------------------------------
    df['BUY_TOUCH']      = (L <= EMA).astype(float)   # low ≤ EMA → touching
    df['SELL_TOUCH']     = (H >= EMA).astype(float)   # high ≥ EMA → touching

    for col in ['BUY_TRIGGERED','BUY_SIGNAL','BUY_ENTRY','BUY_SL','BUY_TP',
                'SELL_TRIGGERED','SELL_SIGNAL','SELL_ENTRY','SELL_SL','SELL_TP',
                'BUY_TAKEN','SELL_TAKEN']:
        df[col] = np.nan

    # ----------------------------------------------------------
    #  Signal generation — bar-by-bar state machine
    # ----------------------------------------------------------
    buy_trig  = None   # index of current BUY triggered candle
    sell_trig = None   # index of current SELL triggered candle

    print("Generating signals …")
    for i in range(ATR_PERIOD, n):
        atr_v = ATR[i]

        # ========== BUY SIDE ==========
        if L[i] <= EMA[i]:          # candle touches EMA → RESET
            buy_trig = None

        elif buy_trig is None:      # first non-touch after touch → TRIGGERED CANDLE
            buy_trig = i
            df.at[i, 'BUY_TRIGGERED'] = 1

        else:                       # subsequent non-touch → check signal
            if H[i] > H[buy_trig]:  # breaks triggered candle's HIGH
                entry   = round(H[buy_trig] + TICK, 5)
                if SL_MODE == 'atr':
                    sl = round(entry - atr_v, 5)
                elif SL_MODE == 'triggered_candle':
                    sl = round(L[buy_trig], 5)
                else:               # signal_candle
                    sl = round(L[i], 5)

                sl_dist = max(entry - sl, TICK * 2)   # guard zero-dist
                tp      = round(entry + sl_dist * TP_RATIO, 5) if TP_MODE == 'ratio' else np.nan

                df.at[i, 'BUY_SIGNAL'] = 1
                df.at[i, 'BUY_ENTRY']  = entry
                df.at[i, 'BUY_SL']     = sl
                df.at[i, 'BUY_TP']     = tp

            # Roll: this candle is now the triggered candle for the next
            buy_trig = i
            df.at[i, 'BUY_TRIGGERED'] = 1

        # ========== SELL SIDE ==========
        if H[i] >= EMA[i]:          # candle touches EMA → RESET
            sell_trig = None

        elif sell_trig is None:     # first non-touch → TRIGGERED CANDLE
            sell_trig = i
            df.at[i, 'SELL_TRIGGERED'] = 1

        else:                       # subsequent non-touch → check signal
            if L[i] < L[sell_trig]: # breaks triggered candle's LOW
                entry   = round(L[sell_trig] - TICK, 5)
                if SL_MODE == 'atr':
                    sl = round(entry + atr_v, 5)
                elif SL_MODE == 'triggered_candle':
                    sl = round(H[sell_trig], 5)
                else:
                    sl = round(H[i], 5)

                sl_dist = max(sl - entry, TICK * 2)
                tp      = round(entry - sl_dist * TP_RATIO, 5) if TP_MODE == 'ratio' else np.nan

                df.at[i, 'SELL_SIGNAL'] = 1
                df.at[i, 'SELL_ENTRY']  = entry
                df.at[i, 'SELL_SL']     = sl
                df.at[i, 'SELL_TP']     = tp

            sell_trig = i
            df.at[i, 'SELL_TRIGGERED'] = 1

    # ----------------------------------------------------------
    #  Trade simulation — bar-by-bar
    # ----------------------------------------------------------
    print("Simulating trades …")
    open_trades = []
    all_trades  = []

    df['TRADE_DIR']      = ''          # string column — keep as object
    df['TRADE_RESULT']   = ''
    for col in ['TRADE_ENTRY','TRADE_INIT_SL','TRADE_TP',
                'TRADE_EXIT','TRADE_PNL_PIPS']:
        df[col] = np.nan

    for i in range(n):
        o, h, l = O[i], H[i], L[i]

        # --- 1. Update & close existing trades ---
        still_open = []
        for trade in open_trades:
            trade.update(i, o, h, l)
            if trade.status == 'CLOSED':
                all_trades.append(trade)
                # Write exit info to df (last writer wins if overlap)
                df.at[i, 'TRADE_DIR']      = trade.dir
                df.at[i, 'TRADE_ENTRY']    = trade.entry
                df.at[i, 'TRADE_INIT_SL']  = trade.initial_sl
                df.at[i, 'TRADE_TP']       = trade.tp_val if TP_MODE == 'ratio' else np.nan
                df.at[i, 'TRADE_EXIT']     = trade.exit_price
                df.at[i, 'TRADE_RESULT']   = trade.exit_reason
                df.at[i, 'TRADE_PNL_PIPS'] = trade.pnl_pips
            else:
                still_open.append(trade)
        open_trades = still_open

        # --- 2. Open new trade(s) if signal present ---
        can_open = ALLOW_OVERLAP or (len(open_trades) == 0)

        # BUY signal
        if df.at[i, 'BUY_SIGNAL'] == 1:
            if can_open:
                entry   = df.at[i, 'BUY_ENTRY']
                sl      = df.at[i, 'BUY_SL']
                sl_dist = entry - sl
                tp_val  = df.at[i, 'BUY_TP']
                t = Trade(i, 'BUY', entry, sl, sl_dist, TP_MODE, TP_RATIO, tp_val)
                open_trades.append(t)
                df.at[i, 'BUY_TAKEN'] = 1
                can_open = ALLOW_OVERLAP  # re-evaluate for same-bar SELL
            else:
                df.at[i, 'BUY_TAKEN'] = 0

        # SELL signal
        if df.at[i, 'SELL_SIGNAL'] == 1:
            can_open_sell = ALLOW_OVERLAP or (len(open_trades) == 0)
            if can_open_sell:
                entry   = df.at[i, 'SELL_ENTRY']
                sl      = df.at[i, 'SELL_SL']
                sl_dist = sl - entry
                tp_val  = df.at[i, 'SELL_TP']
                t = Trade(i, 'SELL', entry, sl, sl_dist, TP_MODE, TP_RATIO, tp_val)
                open_trades.append(t)
                df.at[i, 'SELL_TAKEN'] = 1
            else:
                df.at[i, 'SELL_TAKEN'] = 0

    # Mark still-open trades at end of data
    for trade in open_trades:
        trade.exit_price  = C[-1]
        trade.exit_idx    = n - 1
        trade.exit_reason = 'OPEN_EOD'
        trade.pnl_pips    = (
            round((C[-1] - trade.entry) / TICK) if trade.dir == 'BUY'
            else round((trade.entry - C[-1]) / TICK)
        )
        all_trades.append(trade)

    # ----------------------------------------------------------
    #  Build trade results DataFrame
    # ----------------------------------------------------------
    records = []
    for t in all_trades:
        records.append({
            'OPEN_TIME'  : df.at[t.open_idx,  'DATETIME'],
            'CLOSE_TIME' : df.at[t.exit_idx,  'DATETIME'],
            'DIRECTION'  : t.dir,
            'ENTRY'      : t.entry,
            'INIT_SL'    : t.initial_sl,
            'TP'         : t.tp_val if TP_MODE == 'ratio' else 'TRAILING',
            'EXIT_PRICE' : t.exit_price,
            'RESULT'     : t.exit_reason,
            'PNL_PIPS'   : t.pnl_pips,
            'SL_DIST_PIPS': round(t.sl_dist / TICK),
        })
    trades_df = pd.DataFrame(records)

    # ----------------------------------------------------------
    #  Summary
    # ----------------------------------------------------------
    closed = trades_df[trades_df['RESULT'].isin(['TP', 'SL'])] if len(trades_df) else pd.DataFrame()
    wins   = closed[closed['RESULT'] == 'TP'] if len(closed) else pd.DataFrame()
    losses = closed[closed['RESULT'] == 'SL'] if len(closed) else pd.DataFrame()
    eod    = trades_df[trades_df['RESULT'] == 'OPEN_EOD'] if len(trades_df) else pd.DataFrame()

    sep = '=' * 56
    print(f"\n{sep}")
    print(f"  10 EMA STRATEGY — BACKTEST RESULTS")
    print(f"{sep}")
    print(f"  Instrument   : USDJPY 1-Min  |  Tick: {TICK}")
    print(f"  SL Mode      : {SL_MODE.upper()}"
          + (f"  (period={ATR_PERIOD})" if SL_MODE == 'atr' else ""))
    print(f"  TP Mode      : {TP_MODE.upper()}"
          + (f"  @ {TP_RATIO}R" if TP_MODE == 'ratio' else "  (grid = SL dist)"))
    print(f"  Overlap      : {'ON — multiple trades allowed' if ALLOW_OVERLAP else 'OFF — sequential only'}")
    print(sep)
    print(f"  BUY  Signals       : {int(df['BUY_SIGNAL'].sum()):>6}")
    print(f"  SELL Signals       : {int(df['SELL_SIGNAL'].sum()):>6}")
    print(f"  Total Signals      : {int(df['BUY_SIGNAL'].sum()) + int(df['SELL_SIGNAL'].sum()):>6}")
    print(sep)
    print(f"  Trades Taken       : {len(all_trades):>6}")
    print(f"  Closed (TP + SL)   : {len(closed):>6}")
    print(f"  Wins  (TP)         : {len(wins):>6}")
    print(f"  Losses (SL)        : {len(losses):>6}")
    print(f"  Open at EOD        : {len(eod):>6}")

    if len(closed) > 0:
        wr = len(wins) / len(closed) * 100
        total_pnl = closed['PNL_PIPS'].sum()
        print(sep)
        print(f"  Win Rate           : {wr:>5.1f}%")
        print(f"  Total PnL (pips)   : {total_pnl:>+7.0f}")
        if len(wins):
            print(f"  Avg Win  (pips)    : {wins['PNL_PIPS'].mean():>+7.1f}")
        if len(losses):
            print(f"  Avg Loss (pips)    : {losses['PNL_PIPS'].mean():>+7.1f}")
        if len(wins) and len(losses):
            rr = abs(wins['PNL_PIPS'].mean() / losses['PNL_PIPS'].mean())
            print(f"  Actual Avg R:R     : {rr:>7.2f}")
        print(f"  Max Win  (pips)    : {wins['PNL_PIPS'].max():>+7.0f}" if len(wins) else "")
        print(f"  Max Loss (pips)    : {losses['PNL_PIPS'].min():>+7.0f}" if len(losses) else "")
        # Equity curve high / low
        pnl_series = closed['PNL_PIPS'].cumsum()
        print(f"  Peak Equity (pips) : {pnl_series.max():>+7.0f}")
        print(f"  Max Drawdown(pips) : {(pnl_series - pnl_series.cummax()).min():>+7.0f}")
    print(sep + "\n")

    return df, trades_df


# -----------------------------------------------------------
#  Run & Export
# -----------------------------------------------------------
import os
os.makedirs('/mnt/user-data/outputs', exist_ok=True)

df_out, trades_df = run_backtest()

# Clean up internal index columns before export
drop_cols = [c for c in ['TR'] if c in df_out.columns]
df_out.drop(columns=drop_cols, inplace=True)

df_out.to_csv(OUTPUT_FILE, index=False)
trades_df.to_csv(TRADES_FILE, index=False)

print(f"Annotated data saved → {OUTPUT_FILE}")
print(f"  Columns: {list(df_out.columns)}\n")
print(f"Trade log saved      → {TRADES_FILE}")
print(f"  Rows: {len(trades_df)} trades\n")
