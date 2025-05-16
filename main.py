from flask import Flask
from threading import Thread
import os, time, pandas as pd
from datetime import datetime, date
import smtplib
from email.mime.text import MIMEText
import alpaca_trade_api as tradeapi

app = Flask(__name__)

# === Flask Keep Alive ===
@app.route('/')
def home():
    return "Bot is running"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.start()

# === Alpaca Setup ===
USE_LIVE = False
BASE_URL = 'https://paper-api.alpaca.markets' if not USE_LIVE else 'https://api.alpaca.markets'
API_KEY = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")

api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version='v2')

SYMBOLS = ['AAPL', 'TSLA', 'MSFT']
MAX_TRADE_DOLLARS = 100
traded_today = {}
last_summary_sent = None

# === Utility Functions ===
def get_data(symbol):
    try:
        bars = api.get_bars(symbol, tradeapi.TimeFrame.Minute, limit=100).df
        if bars.empty:
            print(f"[WARN] No data returned for {symbol}")
            return None
        if 'symbol' in bars.index.names:
            bars = bars[bars.index.get_level_values('symbol') == symbol].copy()
        bars.reset_index(inplace=True)
        bars['EMA_9'] = bars['close'].ewm(span=9).mean()
        bars['EMA_21'] = bars['close'].ewm(span=21).mean()
        delta = bars['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        bars['RSI'] = 100 - (100 / (1 + rs))
        return bars
    except Exception as e:
        print(f"[ERROR] Failed to fetch or process data for {symbol}: {e}")
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
        print(f"[ERROR] Signal check failed: {e}")
        return False

def place_order(symbol, max_dollars):
    try:
        price = api.get_last_trade(symbol).price
        qty = int(max_dollars / price)
        if qty < 1:
            print(f"[SKIP] {symbol} price too high for ${max_dollars}")
            return
        print(f"[TRADE] Placing buy order for {qty} shares of {symbol} at ${price:.2f}")
        api.submit_order(
            symbol=symbol,
            qty=qty,
            side='buy',
            type='market',
            time_in_force='gtc',
            order_class='bracket',
            take_profit={'limit_price': round(price * 1.05, 2)},
            stop_loss={'stop_price': round(price * 0.97, 2)}
        )
    except Exception as e:
        print(f"[ERROR] Order placement failed for {symbol}: {e}")

def get_open_positions():
    try:
        positions = api.list_positions()
        if positions:
            print("[POSITIONS] Open positions:")
            for p in positions:
                print(f" - {p.symbol}: {p.qty} @ ${p.avg_entry_price}")
        else:
            print("[POSITIONS] None")
    except Exception as e:
        print(f"[ERROR] Fetching positions failed: {e}")

def send_daily_summary():
    try:
        today = date.today().isoformat()
        orders = api.list_orders(status='filled', limit=100)
        summary_lines = [
            f"{o.symbol} {o.side.upper()} {o.qty} @ {o.filled_avg_price} on {o.filled_at.date().isoformat()}"
            for o in orders if o.filled_at and o.filled_at.date().isoformat() == today
        ]
        if summary_lines:
            summary = "\n".join(summary_lines)
            send_email("Daily Trading Summary", summary)
            print("[EMAIL] Daily summary sent")
        else:
            print("[EMAIL] No trades today to summarize")
    except Exception as e:
        print(f"[ERROR] Sending summary failed: {e}")

def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg['Subject'] = subject
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
    except Exception as e:
        print(f"[ERROR] Email send failed: {e}")

# === Bot Logic ===
def run_bot():
    global last_summary_sent
    print("[BOOT] Starting RSI/EMA bot loop")
    send_daily_summary()

    while True:
        print(f"[LOOP] Tick: {datetime.now().isoformat()}")
        now = datetime.now()
        today = date.today()

        for symbol in SYMBOLS:
            if traded_today.get(symbol) == today:
                print(f"[SKIP] {symbol} already traded today")
                continue

            print(f"[CHECK] Analyzing {symbol}...")
            df = get_data(symbol)
            if df is not None and signal(df):
                place_order(symbol, MAX_TRADE_DOLLARS)
                traded_today[symbol] = today
            else:
                print(f"[INFO] No signal for {symbol}")

        get_open_positions()

        if now.hour == 0 and last_summary_sent != today:
            send_daily_summary()
            last_summary_sent = today

        print("[SLEEP] Pausing for 5 minutes...\n")
        time.sleep(300)

# === Start ===
if __name__ == "__main__":
    keep_alive()
    Thread(target=run_bot).start()