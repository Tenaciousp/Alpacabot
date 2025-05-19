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
RSI_BUY = 30
RSI_SELL = 70
HARD_STOP_LOSS_PCT = 0.03  # 3% stop-loss

# === ENV VARS ===
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = "https://paper-api.alpaca.markets"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

TRADE_LOG_FILE = "trade_log.csv"

# === INITIALISE API ===
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

# === LOGGING SETUP ===
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)

traded_today = {}
sold_today = {}
last_summary_sent = None

# === FLASK SERVER (for keep-alive pings) ===
app = Flask(__name__)

@app.route('/')
def index():
    return "Alpaca RSI/EMA Bot is active."

# === TRADE LOGGING ===
def log_trade(action, symbol, qty, price):
    time_str = datetime.now().isoformat()
    with open(TRADE_LOG_FILE, "a") as f:
        f.write(f"{time_str},{action},{symbol},{qty},{price}\n")
    logging.info(f"[TRADE LOG] {action} {qty} {symbol} @ {price:.2f}")

# === UTILITY FUNCTIONS ===

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
    try:
        api.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type='market',
            time_in_force='day'
        )
        logging.info(f"[ORDER] Placed {side.upper()} order for {qty} shares of {symbol}")
        log_trade(side, symbol, qty, api.get_last_trade(symbol).price)
        send_trade_email(symbol, qty, side)
    except Exception as e:
        logging.error(f"[ORDER ERROR] {symbol}: {e}")

def get_open_positions_dict():
    try:
        positions = api.list_positions()
        return {p.symbol: p for p in positions}
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

        with smtplib.SMTP('smtp.office365.com', 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        logging.info(f"[EMAIL] Trade alert sent for {symbol}.")
    except Exception as e:
        logging.error(f"[EMAIL ERROR] {e}")

def send_daily_summary():
    try:
        positions = api.list_positions()
        total_unreal = sum([float(p.unrealized_pl) for p in positions]) if positions else 0

        lines = [
            f"{p.symbol}: {p.qty} shares @ ${p.avg_entry_price} | Unrealized: ${p.unrealized_pl}"
            for p in positions
        ]

        message = (
            "Open positions:\n" + "\n".join(lines) +
            f"\n\nTotal Unrealized P&L: ${total_unreal:.2f}\n"
        ) if positions else "No open positions."

        try:
            account = api.get_account()
            message += f"\nAccount Equity: ${account.equity}"
        except Exception as e:
            logging.warning(f"[ACCOUNT] Unable to fetch account equity: {e}")

        msg = MIMEText(message)
        msg['Subject'] = 'Daily Alpaca Bot Summary'
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO

        with smtplib.SMTP('smtp.office365.com', 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        logging.info("[EMAIL] Daily summary sent.")
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
        open_positions = get_open_positions_dict()

        for symbol in SYMBOLS:
            df = get_data(symbol)
            if df is not None:
                df = calculate_signals(df)
                latest = df.iloc[-1]
                qty = None

                # Buy signal
                if traded_today.get(symbol) != today:
                    if (latest['RSI'] < RSI_BUY) and (latest['close'] > latest['EMA']):
                        price = latest['close']
                        qty = int(MAX_TRADE_DOLLARS / price)
                        if qty > 0:
                            place_order(symbol, qty, 'buy')
                            traded_today[symbol] = today
                        else:
                            logging.warning(f"[ORDER] Not enough capital for {symbol}. Price: {price}")

                # Sell signal
                if symbol in open_positions and sold_today.get(symbol) != today:
                    position = open_positions[symbol]
                    qty = int(position.qty)
                    entry_price = float(position.avg_entry_price)
                    stop_loss_price = entry_price * (1 - HARD_STOP_LOSS_PCT)
                    if (latest['RSI'] > RSI_SELL) or (latest['close'] < stop_loss_price):
                        place_order(symbol, qty, 'sell')
                        sold_today[symbol] = today

            time.sleep(1)  # Rate limit guard

        if datetime.now().hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        logging.info("[BOT] Sleeping 5 minutes...")
        time.sleep(300)

def keep_alive():
    app.run(host="0.0.0.0", port=8080)

if __name__ == '__main__':
    server_thread = Thread(target=keep_alive, daemon=True)
    server_thread.start()

    bot_thread = Thread(target=run_bot, daemon=False)
    bot_thread.start()