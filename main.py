# === KEEP-ALIVE SERVER ===
from flask import Flask
from threading import Thread
import os, time, pandas as pd, smtplib
from datetime import datetime, date
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# === SETTINGS ===
USE_LIVE = False  # Set True to go live
BASE_URL = 'https://api.alpaca.markets' if USE_LIVE else 'https://paper-api.alpaca.markets'
API_KEY = os.environ.get('ALPACA_API_KEY')
API_SECRET = os.environ.get('ALPACA_SECRET_KEY')
api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
MAX_TRADE_DOLLARS = 100  # Use small amount for paper testing
traded_today = {}
last_summary_sent = None

# === STRATEGY ===
def get_data(symbol):
    try:
        barset = api.get_barset(symbol, 'minute', limit=100)
        df = barset[symbol]
        if not df:
            print(f"[ERROR] No data for {symbol}")
            return None

        df = pd.DataFrame([bar._raw for bar in df])
        df.set_index('t', inplace=True)
        df['close'] = df['c']

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
        print(f"[ERROR] get_data for {symbol}: {e}")
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
        print(f"[ERROR] signal logic: {e}")
        return False

# === ORDER ===
def place_order(symbol, max_dollars):
    try:
        price = api.get_latest_trade(symbol).price
        qty = int(max_dollars / price)
        if qty < 1:
            print(f"[BOT] {symbol} too expensive for ${max_dollars}")
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
        print(f"[BOT] Buy {qty} of {symbol} at ${price:.2f} | TP: {take_profit}, SL: {stop_loss}")
    except Exception as e:
        print(f"[ERROR] order failed for {symbol}: {e}")

# === POSITIONS ===
def get_open_positions():
    try:
        positions = api.list_positions()
        if positions:
            print("[BOT] Open positions:")
            for p in positions:
                print(f"  {p.symbol}: {p.qty} @ {p.avg_entry_price}")
        else:
            print("[BOT] No open positions.")
    except Exception as e:
        print(f"[ERROR] checking positions: {e}")

# === DAILY SUMMARY ===
def send_email(subject, body):
    try:
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
        print("[BOT] Daily email summary sent.")
    except Exception as e:
        print(f"[ERROR] sending email: {e}")

def send_daily_summary():
    try:
        orders = api.list_orders(status='filled', limit=100)
        today = date.today().isoformat()
        lines = [
            f"{o.symbol} {o.side.upper()} {o.qty} @ {o.filled_avg_price} on {o.filled_at.date().isoformat()}"
            for o in orders if o.filled_at.date().isoformat() == today
        ]
        if not lines:
            print("[BOT] No trades today.")
            return
        summary = "\n".join(lines)
        send_email("Daily Trading Summary", f"Today’s Trades:\n\n{summary}")
    except Exception as e:
        print(f"[ERROR] summary: {e}")

# === MAIN LOOP ===
def run_bot():
    global last_summary_sent
    print("[BOT] Starting RSI/EMA bot...")
    send_daily_summary()

    while True:
        print(f"[LOOP] Tick at {datetime.now().isoformat()}")
        now = datetime.now()
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

        if now.hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        print("[BOT] Sleeping 5 minutes...\n")
        time.sleep(300)

# === START ===
keep_alive()
bot_thread = Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()