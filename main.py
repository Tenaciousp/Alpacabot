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

# === CONFIG ===
MAX_TRADE_DOLLARS = 20
SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
RSI_THRESHOLD = 30  # Still used for placeholder logic

# === ENV VARS ===
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# === INIT ===
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

traded_today = {}
last_summary_sent = None

app = Flask(__name__)

@app.route('/')
def index():
    return "Alpaca Bot (Real-Time Fallback) is running."

@app.route('/health')
def health():
    return "OK"

# === DATA FETCH: Using get_latest_trade() ===
def get_latest_price(symbol):
    try:
        trade = api.get_latest_trade(symbol)
        logging.info(f"[DATA] {symbol} latest price: ${trade.price:.2f}")
        return trade.price
    except Exception as e:
        logging.error(f"[DATA ERROR] {symbol}: {e}")
        return None

# === FAKE SIGNAL: fallback logic just checks if price dropped below RSI_THRESHOLD for example's sake ===
def simple_signal(price):
    try:
        return price < RSI_THRESHOLD  # crude placeholder for demo/testing
    except:
        return False

def place_order(symbol, dollars):
    try:
        price = api.get_latest_trade(symbol).price
        qty = int(dollars / price)
        if qty > 0:
            api.submit_order(symbol=symbol, qty=qty, side='buy', type='market', time_in_force='day')
            logging.info(f"[ORDER] Placed BUY order for {qty} shares of {symbol} at ~${price:.2f}")
        else:
            logging.warning(f"[ORDER] Skipped {symbol} — ${dollars} too low to buy at ${price:.2f}")
    except Exception as e:
        logging.error(f"[ORDER ERROR] {symbol}: {e}")

def get_open_positions():
    try:
        positions = api.list_positions()
        for p in positions:
            logging.info(f"[POSITION] {p.symbol}: {p.qty} @ ${p.avg_entry_price}")
    except Exception as e:
        logging.warning(f"[POSITION ERROR] Could not fetch: {e}")

def send_daily_summary():
    try:
        positions = api.list_positions()
        message = "\n".join([f"{p.symbol}: {p.qty} @ ${p.avg_entry_price}" for p in positions]) or "No open positions."
        msg = MIMEText(message)
        msg['Subject'] = 'Alpaca Bot Daily Summary'
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())

        logging.info("[EMAIL] Daily summary sent.")
    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

# === MAIN LOOP ===
def run_bot():
    global last_summary_sent
    logging.info("[BOT] Starting Alpaca Bot (Realtime Fallback Mode)")
    while True:
        logging.info(f"[TICK] {datetime.now().isoformat()}")
        today = date.today()

        for symbol in SYMBOLS:
            if traded_today.get(symbol) == today:
                logging.info(f"[SKIP] {symbol} already traded today.")
                continue

            price = get_latest_price(symbol)
            if price and simple_signal(price):
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = today
            else:
                logging.info(f"[BOT] No signal for {symbol}.")

        get_open_positions()

        if datetime.now().hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        logging.info("[SLEEP] 5 minutes...")
        time.sleep(300)

def keep_alive():
    app.run(host="0.0.0.0", port=8080)

if __name__ == '__main__':
    Thread(target=keep_alive).start()
    Thread(target=run_bot).start()