import MetaTrader5 as mt5
import sys
import time


def ensure_connection_and_symbol(symbol=None) -> bool:
    """
    Ensures the MT5 terminal is initialized and optionally selects a symbol in Market Watch.
    """
    if mt5.terminal_info() is None:
        if not mt5.initialize():
            print("MT5 initialization failed. Ensure terminal is running.")
            return False
            
    if symbol is not None:
        if not mt5.symbol_select(symbol, True):
            print(f"Failed to select symbol '{symbol}'")
            return False
            
    return True

def connect_mt5_application(account_id: int, password: str, server: str) -> bool:
    """
    Establish a connection to the MetaTrader 5 terminal.
    """
    # Ensure the MT5 package is initialized
    # Sometimes it's necessary to specify the path to terminal64.exe in initialize() if it's not found automatically.
    if not mt5.initialize():
        print("initialize() failed, error code =", mt5.last_error())
        return False
        
    # Attempt to log in with the provided credentials
    authorized = mt5.login(login=account_id, password=password, server=server)
    
    if authorized:
        print(f"Successfully connected to account #{account_id} on {server}")
        return True
    else:
        print(f"Failed to connect to account #{account_id}, error code =", mt5.last_error())
        mt5.shutdown()
        return False

def connect_mt5():
    # --- Configuration ---
    # Replace these placeholder values with your actual MT5 credentials
    MT5_ACCOUNT_ID = 12345678          # Must be an integer
    MT5_PASSWORD = "YourPasswordHere"  # String
    MT5_SERVER = "YourBroker-Server"   # String

    # 1. Connect to MT5
    connected = connect_mt5_application(MT5_ACCOUNT_ID, MT5_PASSWORD, MT5_SERVER)

    if connected:
        try:
            # 2. Execute your operations here
            print("\nConnection is active. Ready to execute operations.")
            
            # Example: Fetching account info to verify connection
            account_info = mt5.account_info()
            if account_info is not None:
                print("\n--- Account Info ---")
                print(f"Balance : {account_info.balance}")
                print(f"Equity  : {account_info.equity}")
                print(f"Margin  : {account_info.margin}")
            else:
                print("Failed to retrieve account info.")
                
            # Add your custom algo logic, trading, and symbol operations here
            
        finally:
            # 3. Always ensure you shutdown the connection when done
            mt5.shutdown()
            print("MT5 connection closed.")

def send_order(symbol, lot_size, order_type, sl, tp):
    if not ensure_connection_and_symbol(symbol):
        return False
        
    # Get current tick for immediate market price (CMP)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"Failed to get tick data for '{symbol}'. Check if market is open.")
        return False
        
    price = tick.ask if order_type == "BUY" else tick.bid
    
    # Create a trading request for immediate execution (Market Order)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot_size),
        "type": mt5.ORDER_TYPE_BUY if order_type == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": float(sl),
        "tp": float(tp),
        "deviation": 10,
        "magic": 123456,
        "comment": "Algo_Trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK
    }
    
    # Send the request
    result = mt5.order_send(request)
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Order send failed, error code = {result.retcode}")
        
        # Fallback to IOC if FOK is not supported by broker
        if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            print("Retrying with ORDER_FILLING_IOC...")
            request["type_filling"] = mt5.ORDER_FILLING_IOC
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                 print(f"Order send failed again, error code = {result.retcode}")
                 return False
        else:
            return False
            
    print(f"Order sent successfully: #{result.order}")
    return True

def close_all_positions():
    """
    Closes all open positions across all symbols.
    """
    if not ensure_connection_and_symbol():
        return False
            
    # Retrieve all open positions
    positions = mt5.positions_get()
    if positions is None:
        print(f"Failed to retrieve positions, error code = {mt5.last_error()}")
        return False
        
    if len(positions) == 0:
        print("No open positions to close.")
        return True
        
    print(f"Found {len(positions)} open position(s). Attempting to close...")
    
    all_closed = True
    for pos in positions:
        symbol = pos.symbol
        ticket = pos.ticket
        volume = pos.volume
        pos_type = pos.type
        
        if not ensure_connection_and_symbol(symbol):
            print(f"Cannot close position #{ticket} due to selection failure.")
            all_closed = False
            continue
            
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"Failed to get tick data for '{symbol}'. Cannot close position #{ticket}.")
            all_closed = False
            continue
            
        # Determine opposite order type and closing price
        if pos_type == mt5.ORDER_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        elif pos_type == mt5.ORDER_TYPE_SELL:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            continue
            
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 10,
            "magic": 123456,
            "comment": "Close_All",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK
        }
        
        result = mt5.order_send(request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Failed to close position #{ticket}, error code = {result.retcode}")
            
            # Fallback to IOC if FOK is not supported
            if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
                print("Retrying with ORDER_FILLING_IOC...")
                request["type_filling"] = mt5.ORDER_FILLING_IOC
                result = mt5.order_send(request)
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                     print(f"Retry failed to close position #{ticket}, error code = {result.retcode}")
                     all_closed = False
                else:
                     print(f"Position #{ticket} closed successfully via IOC retry")
            else:
                all_closed = False
        else:
            print(f"Position #{ticket} on {symbol} closed successfully")
            
    return all_closed


SYMBOL = "XAUUSDm"
SIZE = 0.01
ENTRY_PRICE = 4000
ZONE_RANGE = 15
ZONE_UPPER_BAND = ENTRY_PRICE + (ZONE_RANGE/2)
ZONE_LOWER_BAND = ENTRY_PRICE - (ZONE_RANGE/2)

if not ensure_connection_and_symbol(SYMBOL):
    print("Pre-execution checks failed. Exiting.")
    sys.exit()

print(f"Starting execution loop for {SYMBOL}. Monitoring zone crosses...")
print(f"Upper Band: {ZONE_UPPER_BAND} | Lower Band: {ZONE_LOWER_BAND}")

# Initialize prev_cmp to track crosses
tick = mt5.symbol_info_tick(SYMBOL)
if tick is not None:
    prev_cmp = (tick.ask + tick.bid) / 2
else:
    prev_cmp = ENTRY_PRICE

while True:
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print("Failed to get tick data, retrying...")
        time.sleep(1)
        continue
        
    # Calculate Current Market Price (Mid Price)
    current_cmp = (tick.ask + tick.bid) / 2
    
    # Check if there is already an open position for this symbol
    positions = mt5.positions_get(symbol=SYMBOL)
    has_open_position = positions is not None and len(positions) > 0
    
    # 1. Buy if price crosses UPPER band from inside the zone (below to above)
    if prev_cmp <= ZONE_UPPER_BAND and current_cmp > ZONE_UPPER_BAND:
        if not has_open_position:
            print(f"[{current_cmp}] Crossed UPPER BAND (from inside zone)! Executing BUY...")
            send_order(SYMBOL, SIZE, "BUY", ZONE_LOWER_BAND+0.01, 0.0)
        else:
            print(f"[{current_cmp}] Crossed UPPER BAND, but a position is already open. Skipping BUY.")
            
    # 2. Sell if price crosses LOWER band from inside the zone (above to below)
    elif prev_cmp >= ZONE_LOWER_BAND and current_cmp < ZONE_LOWER_BAND:
        if not has_open_position:
            print(f"[{current_cmp}] Crossed LOWER BAND (from inside zone)! Executing SELL...")
            send_order(SYMBOL, SIZE, "SELL", ZONE_UPPER_BAND-0.01, 0.0)
        else:
            print(f"[{current_cmp}] Crossed LOWER BAND, but a position is already open. Skipping SELL.")
        
    # Update prev_cmp for the next tick comparison
    prev_cmp = current_cmp
    
    # Small sleep to prevent CPU overload while remaining highly sensitive to ticks
    time.sleep(0.1)