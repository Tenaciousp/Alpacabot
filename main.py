import os
import time
from datetime import datetime, date
from threading import Thread
from flask import Flask
import pandas as pd
import alpaca_trade_api as tradeapi

# === CONFIG ===
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets"
MAX_TRADE_DOLLARS = 100  # Paper trading cap per trade
SYMBOLS = ["AAPL", "TSLA", "MSFT"]  # Add more as needed

# === SETUP ===
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version="v2")
app = Flask(__name__)
traded_today = {}
last_summary_sent = None

# === KEEP ALIVE ===
@app.route('/')
def home():
    return "Alpacabot is running."

def keep_alive():
    app.run(host='0.0.0.0', port=8080)

# === FETCHING DATA ===
def get_data(symbol):
    try:
        bars = api.get_bars(symbol, timeframe="1Min", limit=100).df
        if bars.empty:
            print(f"[DATA] No data returned for {symbol}", flush=True)
            return None
        bars['EMA_9'] = bars['close'].ewm(span=9).mean()
        bars['RSI'] = compute_rsi(bars['close'], 14)
        return bars
    except Exception as e:
        print(f"[ERROR] Failed to get data for {symbol}: {e}", flush=True)
        return None

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# === SIGNAL LOGIC ===
def signal(df):
    try:
        latest = df.iloc[-1]
        if latest['RSI'] < 30 and latest['close'] > latest['EMA_9']:
            return True
        return False
    except Exception as e:
        print(f"[ERROR] Signal check failed: {e}", flush=True)
        return False

# === ORDER LOGIC ===
def place_order(symbol, max_dollars):
    try:
        latest_price = api.get_last_trade(symbol).price
        qty = int(max_dollars / latest_price)
        if qty == 0:
            print(f"[ORDER] Price too high to buy {symbol} with ${max_dollars}", flush=True)
            return
        api.submit_order(
            symbol=symbol,
            qty=qty,
            side='buy',
            type='market',
            time_in_force='day',
        )
        print(f"[ORDER] Buy {qty} shares of {symbol} at ~${latest_price}", flush=True)
    except Exception as e:
        print(f"[ERROR] Order failed for {symbol}: {e}", flush=True)

def get_open_positions():
    try:
        positions = api.list_positions()
        for p in positions:
            print(f"[POSITION] {p.symbol} - Qty: {p.qty}, Entry: {p.avg_entry_price}, Current: {p.current_price}", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to get positions: {e}", flush=True)

# === DAILY SUMMARY (for logging only) ===
def send_daily_summary():
    try:
        print("[SUMMARY] Daily summary sent (placeholder)", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to send summary: {e}", flush=True)

# === MAIN BOT LOOP ===
def run_bot():
    global last_summary_sent
    print("[BOT] Starting RSI/EMA bot loop...", flush=True)
    send_daily_summary()

    while True:
        print(f"[BOT LOOP] Tick at {datetime.now().isoformat()}", flush=True)

        now = datetime.now()
        today = date.today()

        for symbol in SYMBOLS:
            if traded_today.get(symbol) == today:
                print(f"[BOT] Already traded {symbol} today. Skipping.", flush=True)
                continue

            print(f"[BOT] Checking {symbol}...", flush=True)
            df = get_data(symbol)
            if df is not None and signal(df):
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = today
            else:
                print(f"[BOT] No signal for {symbol}.", flush=True)

        get_open_positions()

        if now.hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        print("[BOT] Sleeping 5 minutes...\n", flush=True)
        time.sleep(300)

# === RUN ===
if __name__ == '__main__':
    keep_alive()

    bot_thread = Thread(target=run_bot)
    bot_thread.daemon = False  # Required for long-running operation
    bot_thread.start()