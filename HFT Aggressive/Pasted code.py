import MetaTrader5 as mt5
import time
from datetime import datetime, timedelta

# ============================================================
# 1. INITIALIZE MT5
# ============================================================

if not mt5.initialize():
    print("MT5 init failed")
    quit()

ACCOUNT  = 334675134
PASSWORD = "Soham@987*#"
SERVER   = "XMGlobal-MT5 9"

if not mt5.login(ACCOUNT, PASSWORD, SERVER):
    print("Login failed:", mt5.last_error())
    mt5.shutdown()
    quit()

print("Connected to MT5")

# ============================================================
# 2. SYMBOL SETUP
# ============================================================

SYMBOL = "GOLD.i#"   # ⚠️ confirm exact symbol in Market Watch

info = mt5.symbol_info(SYMBOL)
if info is None:
    print("Symbol not found")
    mt5.shutdown()
    quit()

if not info.visible:
    mt5.symbol_select(SYMBOL, True)

print("Warming up tick stream...")
for _ in range(20):
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick and tick.ask > 0 and tick.bid > 0:
        print("Tick stream ready")
        break
    time.sleep(0.1)

# ============================================================
# 3. PARAMETERS
# ============================================================

LOT = 0.01
MAGIC = 444444

STOP_DISTANCE  = 0.20    # breakout distance
SCALE_DISTANCE = 0.20     # ladder spacing
TRAIL_DISTANCE = 0.10     # global trailing SL
TRAIL_STEP     = 0.02     # minimum step to move SL
MAX_POSITIONS  = 2        # total positions incl first

MODIFY_DELAY = 0.10
MAX_RUNTIME_MINUTES = 15

# ============================================================
# 4. STATE VARIABLES
# ============================================================

direction = None
global_sl = None
trade_finished = False
ladder_placed = False

# ============================================================
# 5. HELPER FUNCTIONS
# ============================================================

def get_price():
    t = mt5.symbol_info_tick(SYMBOL)
    return t.ask, t.bid

def get_positions():
    return mt5.positions_get(symbol=SYMBOL) or []

def get_orders():
    return mt5.orders_get(symbol=SYMBOL) or []

def place_pending(order_type, price):
    mt5.order_send({
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": SYMBOL,
        "volume": LOT,
        "type": order_type,
        "price": price,
        "magic": MAGIC,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    })

def modify_order(ticket, price):
    mt5.order_send({
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": ticket,
        "price": price
    })

def remove_all_orders():
    for o in get_orders():
        mt5.order_send({
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": o.ticket
        })

def apply_global_sl(sl):
    for p in get_positions():
        mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP,
            "position": p.ticket,
            "sl": sl
        })

# ============================================================
# 6. MAIN LOOP
# ============================================================

start_time = datetime.now()

while not trade_finished and datetime.now() - start_time < timedelta(minutes=MAX_RUNTIME_MINUTES):

    ask, bid = get_price()
    positions = get_positions()
    orders = get_orders()

    print(f"[{datetime.now().time()}] Price={ask} Dir={direction} Pos={len(positions)} Ord={len(orders)}")

    # --------------------------------------------------------
    # A. ENTRY ENGINE (UNCHANGED)
    # --------------------------------------------------------
    if not positions and direction is None:

        buy_price  = ask + STOP_DISTANCE
        sell_price = bid - STOP_DISTANCE

        if not orders:
            place_pending(mt5.ORDER_TYPE_BUY_STOP, buy_price)
            place_pending(mt5.ORDER_TYPE_SELL_STOP, sell_price)
        else:
            for o in orders:
                if o.type == mt5.ORDER_TYPE_BUY_STOP:
                    modify_order(o.ticket, buy_price)
                elif o.type == mt5.ORDER_TYPE_SELL_STOP:
                    modify_order(o.ticket, sell_price)

    # --------------------------------------------------------
    # B. FIRST FILL → FIX DIRECTION & PLACE LADDER
    # --------------------------------------------------------
    elif positions and direction is None:

        remove_all_orders()

        first_pos = positions[0]
        direction = "BUY" if first_pos.type == 0 else "SELL"

        if direction == "BUY":
            global_sl = bid - TRAIL_DISTANCE
            base_price = first_pos.price_open
        else:
            global_sl = ask + TRAIL_DISTANCE
            base_price = first_pos.price_open

        apply_global_sl(global_sl)

        # ---- PLACE ALL SCALE-IN ORDERS AT ONCE ----
        for i in range(1, MAX_POSITIONS):
            if direction == "BUY":
                price = base_price + i * SCALE_DISTANCE
                place_pending(mt5.ORDER_TYPE_BUY_STOP, price)
            else:
                price = base_price - i * SCALE_DISTANCE
                place_pending(mt5.ORDER_TYPE_SELL_STOP, price)

        ladder_placed = True
        print("Scale-in ladder placed")

    # --------------------------------------------------------
    # C. RUNNING TRADE → GLOBAL TRAILING SL ONLY
    # --------------------------------------------------------
    elif positions and direction:

        if direction == "BUY":
            new_sl = bid - TRAIL_DISTANCE
            if new_sl >= global_sl + TRAIL_STEP:
                global_sl = new_sl
                apply_global_sl(global_sl)
        else:
            new_sl = ask + TRAIL_DISTANCE
            if new_sl <= global_sl - TRAIL_STEP:
                global_sl = new_sl
                apply_global_sl(global_sl)

    # --------------------------------------------------------
    # D. SL HIT → STOP EVERYTHING
    # --------------------------------------------------------
    if direction and not get_positions():
        print("Global SL hit — trade completed")
        remove_all_orders()
        trade_finished = True
        break

    time.sleep(MODIFY_DELAY)

# ============================================================
# 7. SHUTDOWN
# ============================================================

print("EA stopped. Manual restart required.")
mt5.shutdown()
