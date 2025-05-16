import os
import time
import logging
from datetime import datetime, date
from threading import Thread
from flask import Flask
import smtplib
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi
import pandas as pd

# === CONFIGURATION ===
MAX_TRADE_DOLLARS = 100
SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
RSI_PERIOD = 14
EMA_PERIOD = 9
RSI_THRESHOLD = 30

# === ENV VARS ===
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = "https://paper-api.alpaca.markets"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# === INITIALISE API ===
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

# === LOGGING SETUP ===
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

# === TRACKERS ===
traded_today = {}
last_summary_sent = None

# === FLASK SERVER ===
app = Flask(__name__)

@app.route('/')
def index():
    return "Alpaca RSI/EMA Bot is active."

# === FUNCTIONS ===

def get_data(symbol):
    try:
        barset = api.get_bars(symbol, '5Min', limit=100).df
        if barset.empty or 'close' not in barset.columns:
            logging.warning(f"[DATA] Invalid data for {symbol}")
            return None
        return barset
    except Exception as e:
        logging.error(f"[DATA ERROR] {symbol}: {e}")
        return None

def signal(df):
    try:
        df['EMA'] = df['close'].ewm(span=EMA_PERIOD).mean()
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=RSI_PERIOD).mean()
        avg_loss = loss.rolling(window=RSI_PERIOD).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        latest = df.iloc[-1]
        return latest['RSI'] < RSI_THRESHOLD and latest['close'] > latest['EMA']
    except Exception as e:
        logging.error(f"[SIGNAL ERROR] {e}")
        return False

def place_order(symbol, dollars):
    try:
        price = api.get_last_trade(symbol).price
        qty = int(dollars / price)
        if qty > 0:
            api.submit_order(symbol=symbol, qty=qty, side='buy', type='market', time_in_force='day')
            logging.info(f"[ORDER] Placed BUY order for {qty} shares of {symbol} at ${price}")
        else:
            logging.warning(f"[ORDER] Calculated quantity is 0 for {symbol} at price ${price}")
    except Exception as e:
        logging.error(f"[ORDER ERROR] {symbol}: {e}")

def get_open_positions():
    try:
        positions = api.list_positions()
        for pos in positions:
            logging.info(f"[POSITION] {pos.symbol}: {pos.qty} shares at ${pos.avg_entry_price}")
    except Exception as e:
        logging.error(f"[POSITION ERROR] {e}")

def send_daily_summary():
    try:
        positions = api.list_positions()
        message = "\n".join([f"{p.symbol}: {p.qty} shares @ ${p.avg_entry_price}" for p in positions]) or "No open positions."
        msg = MIMEText(message)
        msg['Subject'] = 'Daily Alpaca Bot Summary'
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())

        logging.info("[EMAIL] Summary sent.")
    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

# === MAIN BOT LOOP ===
def run_bot():
    global last_summary_sent
    logging.info("[BOT] Starting RSI/EMA bot loop")
    send_daily_summary()
    while True:
        logging.info(f"[BOT LOOP] Tick at {datetime.now().isoformat()}")
        today = date.today()
        for symbol in SYMBOLS:
            if traded_today.get(symbol) == today:
                logging.info(f"[BOT] {symbol} already traded today. Skipping.")
                continue

            logging.info(f"[BOT] Checking {symbol}")
            df = get_data(symbol)
            if df is not None and signal(df):
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = today
            else:
                logging.info(f"[BOT] No signal for {symbol}.")

        get_open_positions()

        if datetime.now().hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        logging.info("[BOT] Sleeping 5 minutes...")
        time.sleep(300)

# === START ===
def keep_alive():
    app.run(host="0.0.0.0", port=8080)

if __name__ == '__main__':
    server_thread = Thread(target=keep_alive)
    server_thread.start()

    bot_thread = Thread(target=run_bot)
    bot_thread.start()