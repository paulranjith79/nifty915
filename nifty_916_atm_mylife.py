from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from dotenv import load_dotenv
import pyotp
import os
import json
from datetime import datetime, date, time, timedelta
import logging
import csv
import time as t
import sys
import re

load_dotenv()
# =====================================================
# SETTINGS
# =====================================================

INSTRUMENT_FILE = os.getenv("INSTRUMENT_FILE")#"NIFTY_28JUL2026.json"

# Extract date from filename
match = re.search(r'(\d{1,2}[A-Z]{3}\d{4})', INSTRUMENT_FILE)

if not match:
    print("❌ Expiry date not found in filename")
    sys.exit()

expiry_str = match.group(1)

# Convert to date
expiry_date = datetime.strptime(expiry_str, "%d%b%Y").date()

# Get today's date
today = datetime.now().date()

# Compare dates
if today <= expiry_date:
    print("✅ File expiry valid:", expiry_date)
else:
    print("❌ File expired:(Please run python dwn_instrument_list.py)", expiry_date)
    sys.exit()

# Check if file exists
if not os.path.exists(INSTRUMENT_FILE):
    print(f"❌ Instrument file not found:(Please run python dwn_instrument_list.py) {INSTRUMENT_FILE}")
    sys.exit()
else:
    print(f"✅ Instrument file found: {INSTRUMENT_FILE}")      

TARGET_POINTS = 50
PREMIUM_LIMIT = 5000


#START_STRIKE = 25650
#START_STRIKE = int(input("Enter the ATM Strike Price: "))
START_STRIKE = int(os.getenv("START_STRIKE"))

STEP  = 50
STEPS = 40

FIRST_CANDLE_HOUR = 9
FIRST_CANDLE_MINUTE = 15

SEARCH_TOKEN_START_TIME = time(FIRST_CANDLE_HOUR, FIRST_CANDLE_MINUTE, 50)
STRATEGY_START_TIME     = time(FIRST_CANDLE_HOUR, FIRST_CANDLE_MINUTE+1, 1)



EXCHANGE_TYPE = 2

SPOT_EXCHANGE = "NSE"
SPOT_TOKEN = "99926000"

# =====================================================
# GLOBAL VARIABLES
# =====================================================
ENTRY_TRIGGER = .5 #6
TARGET_TRIGGER = 1.5 #28
signal_generated = False
signal_stage = 0
# 0 = Waiting for first signal
# 1 = First signal generated
# 2 = Second signal generated (finished)

first_signal_side = None

ce_token = None
ce_strike = None
pe_token = None
pe_strike = None

selected_token = None
selected_strike = None
selected_side = None

entry_price = None
log_file = None
log_writer = None

# =====================================================
# LOAD INSTRUMENT FILE
# =====================================================

with open(INSTRUMENT_FILE, "r") as f:
    instruments = json.load(f)

strike_map = {}
for inst in instruments:
    strike_map.setdefault(inst["strike"], []).append(inst)

# =====================================================
# LOGIN
# =====================================================


#load_dotenv(".env.angel4")

API_KEY = os.getenv("ANGEL_API_KEY")
CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE")
PIN = os.getenv("ANGEL_PIN")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")

smartApi = SmartConnect(API_KEY)
totp = pyotp.TOTP(TOTP_SECRET).now()
session = smartApi.generateSession(CLIENT_CODE, PIN, totp)

authToken = session["data"]["jwtToken"]
feedToken = smartApi.getfeedToken()

print("✅ LOGIN SUCCESS")

# =====================================================
# WAIT FOR TOKEN SEARCH TIME
# =====================================================

while datetime.now().time() < SEARCH_TOKEN_START_TIME:
    t.sleep(0.5)

print("🔎 Searching CE and PE tokens...")

# =====================================================
# TOKEN SEARCH
# =====================================================

def find_token(side):
    for i in range(STEPS):

        strike = START_STRIKE + (i * STEP) if side == "CE" \
                 else START_STRIKE - (i * STEP)

        if strike in strike_map:
            for inst in strike_map[strike]:
                if inst["symbol"].endswith(side):

                    ltp = float(
                        smartApi.ltpData("NFO", inst["symbol"], inst["token"])
                        ["data"]["ltp"]
                    )

                    if ltp <= PREMIUM_LIMIT:
                        print(f"✅ {side} Found: {inst['symbol']} @ {ltp}")
                        return inst["token"], strike

    return None, None

ce_token, ce_strike = find_token("CE")
pe_token, pe_strike = find_token("PE")

print("✅ Token search completed")

# =====================================================
# WAIT FOR FIRST CANDLE COMPLETION
# =====================================================

now = datetime.now() #date(2021,2,19) #datetime.now()

start_dt = datetime(
    now.year, now.month, now.day,
    FIRST_CANDLE_HOUR,
    FIRST_CANDLE_MINUTE
)

end_dt = start_dt + timedelta(minutes=1)
wait_time = end_dt + timedelta(seconds=3)

print("⏳ Waiting for first candle completion...")

while datetime.now() < wait_time:
    t.sleep(0.5)

print("📥 Fetching first candle...")

fromdate = start_dt.strftime("%Y-%m-%d %H:%M")
todate   = end_dt.strftime("%Y-%m-%d %H:%M")

while True:

    params = {
        "exchange": SPOT_EXCHANGE,
        "symboltoken": SPOT_TOKEN,
        "interval": "ONE_MINUTE",
        "fromdate": fromdate,
        "todate": todate
    }

    response = smartApi.getCandleData(params)

    if response.get("data"):

        candle = response["data"][0]
        first_high  = float(candle[2])
        first_low   = float(candle[3])
        first_close = float(candle[4])

        print("\n📊 First Candle")
        print("High :", first_high)
        print("Low  :", first_low)
        print("Close:", first_close)

        break
    else:
        print("Retrying candle fetch...")
        t.sleep(1)

# =====================================================
# DIRECTION LOGIC
# =====================================================

if (first_high - first_close) > (first_close - first_low):
    selected_side = "PE"
else:
    selected_side = "CE"

print(f"📌 Direction Selected: {selected_side}")

# =====================================================
# WAIT FOR STRATEGY START
# =====================================================

while datetime.now().time() < STRATEGY_START_TIME:
    t.sleep(0.5)

print("🚀 Strategy Start Time Reached")

if selected_side == "CE":
    selected_token = ce_token
    selected_strike = ce_strike
else:
    selected_token = pe_token
    selected_strike = pe_strike

print(f"👉 Final Selected: {selected_side} Strike {selected_strike}")

# =====================================================
# WEBSOCKET
# =====================================================

logging.getLogger("smartWebSocketV2").setLevel(logging.CRITICAL)
sws = SmartWebSocketV2(authToken, API_KEY, CLIENT_CODE, feedToken)

def subscribe():
    sws.subscribe(
        "strategy",
        1,
        [{"exchangeType": EXCHANGE_TYPE, "tokens": [selected_token]}]
    )

def exit_program(reason):
    global log_file
    if log_file:
        log_file.close()
    try:
        sws.ws.close()
    except:
        pass
    print(f"\n🛑 EXITED | {reason}")
    os._exit(0)

def on_open(ws):
    print("📡 WebSocket Connected")
    subscribe()

def on_data(ws, message):
    global entry_price, log_file, log_writer, signal_generated
    global signal_stage, first_signal_side

    ltp_raw = message.get("last_traded_price")
    if not ltp_raw:
        return

    ltp = ltp_raw / 100

    if entry_price is None:
        entry_price = ltp
        print(f"\n➡️ BUY {selected_side} @ {entry_price}")

        filename = f"{date.today()}_{selected_strike}_{selected_side}.tsv"
        log_file = open(filename, "w", newline="")
        log_writer = csv.writer(log_file, delimiter="\t")

        log_writer.writerow(["Date","Time","LTP","PnL"])
        log_file.flush()
        return

    pnl = round(ltp - entry_price, 2)

    action=""
    # ---------------- FIRST SIGNAL ----------------
    if signal_stage == 0:

        # Selected side
        if pnl >= ENTRY_TRIGGER:

            print(f"\n🟢 BUY 1 {selected_side}")
            action=f"BUY 1 {selected_side}"

            first_signal_side = selected_side
            signal_stage = 1

        # Opposite side
        elif pnl <= -ENTRY_TRIGGER:

            opposite_side = "PE" if selected_side == "CE" else "CE"

            print(f"\n🔴 BUY 1 {opposite_side}")
            action=f"BUY 1 {opposite_side}"

            first_signal_side = opposite_side
            signal_stage = 1


    # ---------------- AFTER FIRST SIGNAL ----------------
    elif signal_stage == 1:

        # First signal was BUY selected side
        if first_signal_side == selected_side:

            # Target achieved
            if pnl >= TARGET_TRIGGER:
                print("\n🎯 BUY 1 TARGET HIT")
                action=f"BUY 1 TARGET HIT"
                #signal_stage = 2
                signal_stage = 10

            # First signal failed -> Reverse signal
            elif pnl <= -ENTRY_TRIGGER:

                opposite_side = "PE" if selected_side == "CE" else "CE"

                print(f"\n🔁 Buy 1 Failed, Now BUY 2 {opposite_side}")
                action=f"Buy 1 Failed, Now BUY 2 {opposite_side}"

                signal_stage = 2


        # First signal was BUY opposite side
        else:

            # Target achieved
            if pnl <= -TARGET_TRIGGER:
                print("\n🎯 Buy 1 TARGET HIT")
                action=f"Buy 1 TARGET HIT"
                #signal_stage = 2
                signal_stage = 10

            # First signal failed -> Reverse signal
            elif pnl >= ENTRY_TRIGGER:

                print(f"\n🔁 Buy 1 failed, Now BUY 2 {selected_side}")
                action=f"Buy 1 failed, Now BUY 2 {selected_side}"

                signal_stage = 2


    # ---------------- SECOND SIGNAL ----------------
    elif signal_stage == 2:

        # Second signal is BUY selected side
        if first_signal_side != selected_side:

            if pnl >= TARGET_TRIGGER:
                print("\n🎯 Buy 2 TARGET HIT")
                action=f"Buy 2 TARGET HIT"
                #signal_stage = 3
                signal_stage = 10

            elif pnl <= -ENTRY_TRIGGER:
                print("\n❌ Buy 2 FAILED")
                action=f"Buy 2 FAILED"
                signal_stage = 3

        # Second signal is BUY opposite side
        else:

            if pnl <= -TARGET_TRIGGER:
                print("\n🎯 Buy 2 TARGET HIT")
                action=f"Buy 2 TARGET HIT"
                #signal_stage = 3
                signal_stage = 10

            elif pnl >= ENTRY_TRIGGER:
                print("\n❌ Buy 2 FAILED")
                action=f"Buy 2 FAILED"
                signal_stage = 3
    elif signal_stage == 3:
        pass 

    print(f"📊 {selected_strike} {selected_side} | LTP {ltp} | P/L {pnl}")
    

    

    log_writer.writerow([
        date.today(),
        datetime.now().strftime("%H:%M:%S"),
        ltp,
        pnl,
        action
    ])
    log_file.flush()

    # ---------------- FIRST SIGNAL ----------------
       

    if pnl >= TARGET_POINTS:
        exit_program("TARGET HIT 🎯")

sws.on_open  = on_open
sws.on_data  = on_data
sws.on_error = lambda ws,e: print("Error:",e)
sws.on_close = lambda ws: print("Closed")

sws.connect()
