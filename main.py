# (unchanged imports)
import os
import time
import logging
import json
from datetime import datetime, date
from threading import Thread
from flask import Flask
import smtplib
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

# === CONFIGURATION ===
MAX_TRADE_DOLLARS = 50
SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
RSI_PERIOD = 14
EMA_PERIOD = 9
RSI_BUY = 35
RSI_SELL = 70
HARD_STOP_LOSS_PCT = 0.03
MAX_TOTAL_TRADES_PER_DAY = 2

# === ENV VARS ===
PAPER = os.getenv("PAPER", "true").lower() == "true"
BASE_URL = "https://paper-api.alpaca.markets" if PAPER else "https://api.alpaca.markets"
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")
TRADE_LOG_FILE = "trade_log.csv"
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# === INITIALISE API ===
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

# === LOGGING SETUP ===
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

# === GLOBALS ===
traded_today = {}
sold_today = {}
trade_count_today = 0
last_summary_sent = None

# === FLASK SERVER ===
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot running."

@app.route('/health')
def health_check():
    return "OK"

# === BOT LOGIC FUNCTIONS (unchanged — not repeated for brevity) ===
# Includes: log_trade, get_data, calculate_signals, place_order,
# get_open_positions_dict, send_trade_email, send_daily_summary

def run_bot():
    global last_summary_sent
    logging.info("[BOT] Starting RSI/EMA bot loop")
    send_daily_summary()
    while True:
        try:
            logging.info(f"[BOT LOOP] Tick at {datetime.now().isoformat()}")
            today = date.today()
            open_positions = get_open_positions_dict()

            for symbol in SYMBOLS:
                logging.info(f"[BOT] Checking {symbol}...")
                if traded_today.get(symbol) == today:
                    logging.info(f"[SKIP] {symbol} already traded today.")
                    continue

                df = get_data(symbol)
                if df is not None:
                    df = calculate_signals(df)
                    latest = df.iloc[-1]

                    if (latest['RSI'] < RSI_BUY) and (latest['close'] > latest['EMA']):
                        price = latest['close']
                        qty = int(MAX_TRADE_DOLLARS / price)
                        if qty > 0:
                            place_order(symbol, qty, 'buy')
                            traded_today[symbol] = today
                        else:
                            logging.warning(f"[ORDER] {symbol}: price too high for ${MAX_TRADE_DOLLARS}")

                    if symbol in open_positions and sold_today.get(symbol) != today:
                        position = open_positions[symbol]
                        qty = int(position.qty)
                        entry_price = float(position.avg_entry_price)
                        stop_loss_price = entry_price * (1 - HARD_STOP_LOSS_PCT)
                        if (latest['RSI'] > RSI_SELL) or (latest['close'] < stop_loss_price):
                            place_order(symbol, qty, 'sell')
                            sold_today[symbol] = today

            if datetime.now().hour == 0 and last_summary_sent != today:
                send_daily_summary()
                last_summary_sent = today

            logging.info("[BOT] Sleeping 5 minutes...")
            time.sleep(300)

        except Exception as e:
            logging.error(f"[BOT LOOP ERROR] {e}")
            logging.info("[BOT] Sleeping 60 seconds before retry...")
            time.sleep(60)

def run_bot_loop():
    logging.info("[BOOT] Bot thread is now running...")
    run_bot()

# === ENTRY POINT (non-daemon thread to ensure bot stays alive) ===
if __name__ == '__main__':
    logging.info("[BOOT] Launching Flask and trading bot...")
    bot_thread = Thread(target=run_bot_loop)
    bot_thread.start()
    app.run(host="0.0.0.0", port=8080)