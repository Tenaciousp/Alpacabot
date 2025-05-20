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
bot_started = False

# === FLASK SERVER ===
app = Flask(__name__)

@app.route('/')
def index():
    global bot_started
    if not bot_started:
        logging.info("[FLASK] First HTTP ping — launching trading bot...")
        bot_thread = Thread(target=run_bot_loop, daemon=True)
        bot_thread.start()
        bot_started = True
    return "Bot running."

@app.route('/health')
def health_check():
    return "OK"

def log_trade(action, symbol, qty, price):
    time_str = datetime.now().isoformat()
    with open(TRADE_LOG_FILE, "a") as f:
        f.write(f"{time_str},{action},{symbol},{qty},{price}\n")
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open("Alpaca Trade Log").sheet1
        sheet.append_row([time_str, action, symbol, qty, price])
        logging.info(f"[GOOGLE SHEETS] Logged {action} {symbol} to Google Sheet.")
    except Exception as e:
        logging.error(f"[SHEETS ERROR] {e}")

def get_data(symbol):
    try:
        barset = api.get_bars(symbol, '5Min', limit=100).df
        if barset.empty or 'close' not in barset.columns:
            logging.warning(f"[DATA] Invalid or missing 'close' data for {symbol}")
            return None
        return barset
    except Exception as e:
        logging.error(f"[DATA ERROR] {symbol}: {e}")
        return None

def calculate_signals(df):
    try:
        df['EMA'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=RSI_PERIOD, min_periods=RSI_PERIOD).mean()
        avg_loss = loss.rolling(window=RSI_PERIOD, min_periods=RSI_PERIOD).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        return df
    except Exception as e:
        logging.error(f"[SIGNAL ERROR] {e}")
        return df

def place_order(symbol, qty, side):
    global trade_count_today
    try:
        if trade_count_today >= MAX_TOTAL_TRADES_PER_DAY:
            logging.info("[LIMIT] Max daily trades reached.")
            return
        api.submit_order(symbol=symbol, qty=qty, side=side, type='market', time_in_force='day')
        price = api.get_last_trade(symbol).price
        log_trade(side, symbol, qty, price)
        send_trade_email(symbol, qty, side)
        trade_count_today += 1
        logging.info(f"[ORDER] {side.upper()} {qty} shares of {symbol} at ${price:.2f}")
    except Exception as e:
        logging.error(f"[ORDER ERROR] {symbol}: {e}")

def get_open_positions_dict():
    try:
        return {p.symbol: p for p in api.list_positions()}
    except Exception as e:
        logging.error(f"[POSITION ERROR] {e}")
        return {}

def send_trade_email(symbol, qty, side):
    try:
        subject = f"Trade Alert: {side.upper()} {qty} {symbol}"
        price = api.get_last_trade(symbol).price
        message = f"{side.upper()} {qty} shares of {symbol} at ${price:.2f} on {datetime.now().isoformat()}."
        msg = MIMEText(message)
        msg['Subject'] = subject
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        logging.info(f"[EMAIL] Trade alert sent for {symbol}.")
    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

def send_daily_summary():
    global trade_count_today
    try:
        positions = api.list_positions()
        total_unreal = sum([float(p.unrealized_pl) for p in positions]) if positions else 0
        lines = [f"{p.symbol}: {p.qty} shares @ ${p.avg_entry_price} | Unrealized: ${p.unrealized_pl}" for p in positions]

        message = (
            "Open positions:\n" + "\n".join(lines) +
            f"\n\nTotal Unrealized P&L: ${total_unreal:.2f}\nTrades Executed Today: {trade_count_today}"
        ) if positions else "No open positions."

        account = api.get_account()
        message += f"\nAccount Equity: ${account.equity}"

        msg = MIMEText(message)
        msg['Subject'] = 'Daily Alpaca Bot Summary'
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        logging.info("[EMAIL] Daily summary sent.")
        trade_count_today = 0
    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

def run_bot():
    global last_summary_sent
    logging.info("[BOT] Starting RSI/EMA bot loop")
    send_daily_summary()
    while True:
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

            time.sleep(1)

        if datetime.now().hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        logging.info("[BOT] Sleeping 5 minutes...")
        time.sleep(300)

def run_bot_loop():
    while True:
        try:
            run_bot()
        except Exception as e:
            logging.error(f"[BOT CRASH] Bot loop crashed: {e}")
            logging.info("[BOT] Restarting in 60 seconds...")
            time.sleep(60)

# === ENTRY POINT ===
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8080)