import os
import time
import logging
from datetime import datetime
from flask import Flask
from threading import Thread
import krakenex

# === CONFIGURATION ===
SYMBOLS = ['XXBTZUSD', 'XETHZUSD']  # Kraken symbols for BTC/USD and ETH/USD
CHECK_INTERVAL = 300  # in seconds

# === ENV VARS ===
API_KEY = os.getenv("KRAKEN_API_KEY")
API_SECRET = os.getenv("KRAKEN_API_SECRET")

# === LOGGING SETUP ===
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

# === FLASK SERVER ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Kraken trading bot is running."

# === INITIALISE API ===
kraken = krakenex.API()
kraken.key = API_KEY
kraken.secret = API_SECRET

# === ENHANCED VERBOSE LOGGING ===
def log_verbose_price_info(symbol, price_data):
    try:
        ask = price_data['a'][0]
        bid = price_data['b'][0]
        last = price_data['c'][0]
        logging.info(f"[PRICE] {symbol}: Ask = {ask}, Bid = {bid}, Last = {last}")
    except Exception as e:
        logging.warning(f"[PRICE LOGGING ERROR] {symbol}: {e}")

# === BOT LOOP ===
def run_bot():
    logging.info("[BOT] Kraken bot started.")
    while True:
        for symbol in SYMBOLS:
            try:
                logging.info(f"[BOT] Checking {symbol}")
                response = kraken.query_public('Ticker', {'pair': symbol})
                if 'error' in response and response['error']:
                    logging.error(f"[API ERROR] {symbol}: {response['error']}")
                    continue
                price_data = response['result'][symbol]
                log_verbose_price_info(symbol, price_data)
                # Insert trading logic here if needed
            except Exception as e:
                logging.error(f"[BOT ERROR] {symbol}: {e}")
        logging.info("[BOT] Sleeping 5 minutes...")
        time.sleep(CHECK_INTERVAL)

# === HEALTH CHECK ENDPOINT ===
@app.route("/health")
def health():
    return "OK"

# === STARTUP ===
def start():
    server = Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 8080})
    server.start()

    bot = Thread(target=run_bot)
    bot.start()

if __name__ == "__main__":
    start()