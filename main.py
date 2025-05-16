from flask import Flask
from threading import Thread
import os
import time
import pandas as pd
from datetime import datetime, date
import smtplib
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi

# === Flask keep-alive server ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# === Alpaca config ===
USE_LIVE = False
BASE_URL = 'https://api.alpaca.markets' if USE_LIVE else 'https://paper-api.alpaca.markets'
API_KEY = os.environ.get('ALPACA_API_KEY')
API_SECRET = os.environ.get('ALPACA_SECRET_KEY')
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
MAX_TRADE_DOLLARS = 100
traded_today = {}
last_summary_sent = None

# === Strategy ===
def get_data(symbol):
    try:
        barset = api.get_bars(symbol, tradeapi.TimeFrame.Minute, limit=100)
        df = barset.df

        if isinstance(df.index, pd.MultiIndex) and 'symbol' in df.index.names:
            df = df[df.index.get_level_values('symbol') == symbol]

        df = df.reset_index()

        if 'close' not in df.columns:
            print(f"[ERROR] 'close' not found in data for {symbol}")
            return None

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
        print(f"[ERROR] Failed to fetch/process data for {symbol}: {e}")
        return None

def signal(df):
    try:
        if df is None or len(df) < 2:
            return False
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        return (
            prev['EMA_9'] < prev['EMA_21']
            and latest['EMA_9'] > latest['EMA_21']
            and prev['RSI'] < 30 and latest['RSI'] > 30
        )
    except Exception as e:
        print(f"[ERROR] Signal generation failed: {e}")
        return False

# === Order Execution ===
def place_order(symbol, max_dollars):
    try:
        price = api.get_latest_trade(symbol).price
        qty = int(max_dollars / price)
        if qty < 1:
            print(f"[BOT] Skipped {symbol}: too expensive.")
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
        print(f"[BOT] Order placed: BUY {qty} of {symbol} at ${price:.2f}")
    except Exception as e:
        print(f"[ERROR] Order failed for {symbol}: {e}")

# === Open Positions Logging ===
def get_open_positions():
    try:
        positions = api.list_positions()
        if positions:
            print("[BOT] Open positions:")
            for p in positions:
                print(f" - {p.symbol}: {p.qty} @ {p.avg_entry_price}")
        else:
            print("[BOT] No open positions.")
    except Exception as e:
        print(f"[ERROR] Checking positions: {e}")

# === Daily Summary ===
def send_email(subject, body):
    sender = os.environ.get('EMAIL_SENDER')
    recipient = os.environ.get('EMAIL_RECIPIENT')
    password = os.environ.get('EMAIL_PASSWORD')

    msg = MIMEText(body)
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = subject

    try:
        with smtplib.SMTP('smtp.office365.com', 587) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        print("[BOT] Summary email sent.")
    except Exception as e:
        print(f"[ERROR] Failed to send summary email: {e}")

def send_daily_summary():
    try:
        orders = api.list_orders(status='filled', limit=100)
        today = date.today().isoformat()
        lines = [f"{o.symbol} {o.side.upper()} {o.qty} @ {o.filled_avg_price} on {o.filled_at.date().isoformat()}"
                 for o in orders if o.filled_at and o.filled_at.date().isoformat() == today]
        if not lines:
            print("[BOT] No trades to summarise.")
            return
        body = "Today's Trades:\n\n" + "\n".join(lines)
        send_email("Daily Trading Summary", body)
    except Exception as e:
        print(f"[ERROR] Sending summary: {e}")

# === Main Bot Loop ===
def run_bot():
    global last_summary_sent
    print("[BOT] Starting RSI/EMA bot...")
    send_daily_summary()

    while True:
        print(f"[BOT LOOP] Tick at {datetime.now().isoformat()}")
        today = date.today()
        for symbol in SYMBOLS:
            if traded_today.get(symbol) == today:
                print(f"[BOT] Already traded {symbol}. Skipping.")
                continue

            print(f"[BOT] Checking {symbol}...")
            df = get_data(symbol)
            if df is not None and signal(df):
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = today
            else:
                print(f"[BOT] No signal for {symbol}.")

        get_open_positions()

        if datetime.now().hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        print("[BOT] Sleeping 5 minutes...\n")
        time.sleep(300)

# === Startup ===
if __name__ == "__main__":
    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    bot_thread = Thread(target=run_bot)
    bot_thread.start()