import os
import time
import logging
from datetime import datetime, date
from threading import Thread
from flask import Flask
import smtplib
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi

# === CONFIGURATION ===
MAX_TRADE_DOLLARS = 20
SYMBOLS = ['AAPL', 'TSLA', 'MSFT']

# === ENVIRONMENT VARIABLES ===
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://api.alpaca.markets")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# === INIT ===
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

traded_today = {}
last_summary_sent = None

@app.route('/')
def index():
    return "Alpaca free-tier trading bot is live."

# === CORE FUNCTIONS ===

def get_latest_price(symbol):
    try:
        quote = api.get_latest_quote(symbol)
        return quote.ask_price
    except Exception as e:
        logging.warning(f"[QUOTE ERROR] {symbol}: {e}")
        return None

def should_trade_now(symbol):
    # Simulated strategy: trade every 15 minutes if not yet traded today
    now = datetime.now()
    return now.minute % 15 == 0

def place_order(symbol, dollars):
    try:
        price = get_latest_price(symbol)
        if not price or price <= 0:
            logging.warning(f"[ORDER] Skipping {symbol}, invalid price.")
            return
        qty = int(dollars / price)
        if qty < 1:
            logging.info(f"[ORDER] Skipping {symbol}, not enough funds to buy minimum qty.")
            return
        api.submit_order(symbol=symbol, qty=qty, side='buy', type='market', time_in_force='day')
        logging.info(f"[ORDER] Buy {qty} x {symbol} at ${price:.2f}")
    except Exception as e:
        logging.error(f"[ORDER ERROR] {symbol}: {e}")

def send_summary():
    try:
        positions = api.list_positions()
        message = "\n".join([f"{p.symbol}: {p.qty} shares @ ${p.avg_entry_price}" for p in positions]) or "No open positions."
        msg = MIMEText(message)
        msg['Subject'] = "Daily Alpaca Bot Summary"
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        logging.info("[EMAIL] Summary sent.")
    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

# === BOT LOOP ===

def run_bot():
    global last_summary_sent
    logging.info("[BOT] Starting main loop")
    while True:
        now = datetime.now()
        for symbol in SYMBOLS:
            if traded_today.get(symbol) == date.today():
                logging.info(f"[BOT] {symbol} already traded today.")
                continue
            if should_trade_now(symbol):
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = date.today()
            else:
                logging.info(f"[BOT] No signal for {symbol}.")
        if now.hour == 0 and last_summary_sent != date.today():
            send_summary()
            last_summary_sent = date.today()
        logging.info("[BOT] Sleeping 5 minutes...")
        time.sleep(300)

# === SERVER KEEPALIVE ===

def keep_alive():
    app.run(host="0.0.0.0", port=8080)

# === ENTRYPOINT ===
if __name__ == '__main__':
    Thread(target=keep_alive).start()
    Thread(target=run_bot).start()