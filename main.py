# === KEEP-ALIVE SERVER ===
from flask import Flask
from threading import Thread
import os
import time
import pandas as pd
from datetime import datetime, date
import smtplib
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# === SETTINGS ===
USE_LIVE = False
BASE_URL = 'https://api.alpaca.markets' if USE_LIVE else 'https://paper-api.alpaca.markets'
API_KEY = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')

EMAIL_SENDER = os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.getenv('EMAIL_RECIPIENT')

api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
MAX_TRADE_DOLLARS = 100
traded_today = {}
last_summary_sent = None

# === STRATEGY ===
def get_data(symbol):
    try:
        print(f"[DATA] Fetching bars for {symbol}")
        barset = api.get_bars(symbol, tradeapi.TimeFrame.Minute, limit=100)
        df = barset.df

        if df.empty:
            print(f"[ERROR] No data received for {symbol}")
            return None

        if isinstance(df.index, pd.MultiIndex) and 'symbol' in df.index.names:
            df = df[df.index.get_level_values('symbol') == symbol]

        df = df.reset_index()
        df['EMA_9'] = df['close'].ewm(span=9).mean()
        df['EMA_21'] = df['close'].ewm(span=21).mean()
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))

        return df
    except Exception as e:
        print(f"[ERROR] Failed to get data for {symbol}: {e}")
        return None

def signal(df):
    try:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        return (
            prev['EMA_9'] < prev['EMA_21'] and
            latest['EMA_9'] > latest['EMA_21'] and
            prev['RSI'] < 30 and latest['RSI'] > 30
        )
    except Exception as e:
        print(f"[ERROR] Signal logic failed: {e}")
        return False

# === TRADING ===
def place_order(symbol, max_dollars):
    try:
        price = api.get_latest_trade(symbol).price
        qty = int(max_dollars / price)
        if qty < 1:
            print(f"[TRADE] {symbol} is too expensive for ${max_dollars}")
            return

        take_profit = round(price * 1.05, 2)
        stop_loss = round(price * 0.97, 2)

        api.submit_order(
            symbol=symbol,
            qty=qty,
            side='buy',
            type='market',
            time_in_force='gtc',
            order_class='bracket',
            take_profit={'limit_price': take_profit},
            stop_loss={'stop_price': stop_loss}
        )

        print(f"[TRADE] Placed BUY for {qty} of {symbol} at ${price:.2f}")
        send_email(f"Trade Executed: {symbol}", f"Bought {qty} of {symbol} at ${price:.2f}")
    except Exception as e:
        print(f"[ERROR] Order failed for {symbol}: {e}")

def get_open_positions():
    try:
        positions = api.list_positions()
        if positions:
            print("[POSITION] Current open positions:")
            for p in positions:
                print(f" - {p.symbol}: {p.qty} at ${p.avg_entry_price}")
        else:
            print("[POSITION] No open positions.")
    except Exception as e:
        print(f"[ERROR] Fetching positions failed: {e}")

# === EMAIL ===
def send_email(subject, body):
    try:
        if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECIPIENT:
            print("[EMAIL] Missing environment variables. Skipping email.")
            return

        msg = MIMEText(body)
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg['Subject'] = subject

        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

        print("[EMAIL] Sent notification.")
    except Exception as e:
        print(f"[ERROR] Email failed: {e}")

# === DAILY SUMMARY ===
def send_daily_summary():
    try:
        orders = api.list_orders(status='filled', limit=100)
        today = date.today().isoformat()
        summary_lines = [
            f"{o.symbol} | {o.side.upper()} | Qty: {o.qty} | Price: {o.filled_avg_price} | Time: {o.filled_at}"
            for o in orders if o.filled_at.date().isoformat() == today
        ]
        if not summary_lines:
            print("[SUMMARY] No trades today.")
            return

        body = "\n".join(summary_lines)
        send_email("Daily Trading Summary", f"Today's Trades:\n\n{body}")
        print("[SUMMARY] Daily summary sent.")
    except Exception as e:
        print(f"[ERROR] Daily summary failed: {e}")

# === MAIN LOOP ===
def run_bot():
    global last_summary_sent
    print("[BOT] Starting RSI/EMA bot...")
    send_daily_summary()

    while True:
        print(f"[BOT LOOP] Tick at {datetime.now().isoformat()}")
        now = datetime.now()
        today = date.today()

        for symbol in SYMBOLS:
            if traded_today.get(symbol) == today:
                print(f"[BOT] Already traded {symbol} today. Skipping.")
                continue

            print(f"[BOT] Checking {symbol}...")
            df = get_data(symbol)
            if df is not None and signal(df):
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = today
            else:
                print(f"[BOT] No signal for {symbol}.")

        get_open_positions()

        if now.hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        print("[BOT] Sleeping 5 minutes...\n")
        time.sleep(300)

# === START ===
if __name__ == '__main__':
    keep_alive()
    bot_thread = Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()