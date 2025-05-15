# === KEEP-ALIVE SERVER ===
from flask import Flask
from threading import Thread
import os
import time
from datetime import datetime, date
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi
app = Flask(__name__)

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
@app.route('/')
def home():
    return "Bot is alive!"

# === SETTINGS ===
USE_LIVE = False  # Set to True for live trading
BASE_URL = 'https://paper-api.alpaca.markets' if not USE_LIVE else 'https://api.alpaca.markets'
API_KEY = os.environ.get('ALPACA_API_KEY')
API_SECRET = os.environ.get('ALPACA_SECRET_KEY')

api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
MAX_TRADE_DOLLARS = 5
traded_today = {}
last_summary_sent = None

# === STRATEGY ===
def get_data(symbol):
    barset = api.get_bars(symbol, tradeapi.TimeFrame.Minute, limit=100)
    df = barset.df

    # Check if it's a MultiIndex and filter by symbol if needed
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

def signal(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    return (
        prev['EMA_9'] < prev['EMA_21']
        and latest['EMA_9'] > latest['EMA_21']
        and prev['RSI'] < 30 and latest['RSI'] > 30
    )

# === PLACE ORDER ===
def place_order(symbol, max_dollars):
    try:
        price = api.get_last_trade(symbol).price
        qty = int(max_dollars / price)
        if qty < 1:
            print(f"Skipped {symbol}: too expensive for ${max_dollars}")
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

        print(f"Bracket order placed: BUY {qty} of {symbol} at ${price:.2f}")
        print(f"  --> Take Profit at ${take_profit}")
        print(f"  --> Stop Loss at ${stop_loss}")

    except Exception as e:
        print(f"Order failed for {symbol}:", e)

# === OPEN POSITIONS ===
def get_open_positions():
    try:
        positions = api.list_positions()
        if positions:
            print("Open positions:")
            for p in positions:
                print(f"{p.symbol}: {p.qty} @ ${p.avg_entry_price}")
        else:
            print("No open positions.")
    except Exception as e:
        print("Error checking positions:", e)

# === DAILY SUMMARY ===
def send_daily_summary():
    try:
        orders = api.list_orders(status='filled', limit=100)
        today = date.today().isoformat()
        summary_lines = [
            f"{o.symbol}: {o.side.upper()} {o.qty} @ ${o.filled_avg_price} on {o.filled_at.date().isoformat()}"
            for o in orders if o.filled_at.date().isoformat() == today
        ]
        if not summary_lines:
            print("No trades today to summarize.")
            return

        summary = "\n".join(summary_lines)
        send_email("Daily Trading Summary", f"Today's Trades:\n\n{summary}")
        print("Daily summary email sent.")
    except Exception as e:
        print("Failed to send summary:", e)

def send_email(subject, body):
    sender = os.environ.get('EMAIL_SENDER')
    recipient = os.environ.get('EMAIL_RECIPIENT')
    password = os.environ.get('EMAIL_PASSWORD')

    msg = MIMEText(body)
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = subject

    with smtplib.SMTP('smtp.office365.com', 587) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

# === MAIN LOOP ===
def run_bot():
    global last_summary_sent
    print("Running Alpaca RSI/EMA bot on paper...")
    send_daily_summary()

    while True:
        now = datetime.now()
        today = date.today()

        for symbol in SYMBOLS:
            if traded_today.get(symbol) == today:
                print(f"Already traded {symbol} today. Skipping...")
                continue

            print(f"\nChecking {symbol}...")
            df = get_data(symbol)
            if signal(df):
                print(f"Buy signal detected for {symbol}!")
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = today
            else:
                print(f"No signal for {symbol}.")

        get_open_positions()

        if now.hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        print("\nSleeping 5 minutes...\n")
        time.sleep(300)

# === RUN ===
keep_alive()
run_bot()