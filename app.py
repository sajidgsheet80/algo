from flask import Flask, render_template, request, redirect, url_for
from fyers_apiv3 import fyersModel
import threading
import webbrowser
import pandas as pd
import os
# ---- Fyers Credentials ----
client_id = "UBKM03VNIB-100"
secret_key = "VCPXAFC291"
redirect_uri = "http://127.0.0.1:5000/callback"
grant_type = "authorization_code"
response_type = "code"
state = "sample"

app = Flask(__name__)

# ---- Globals ----
appSession = fyersModel.SessionModel(
    client_id=client_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type=response_type,
    grant_type=grant_type,
    state=state
)

fyers = None
access_token = None

# Trading data
strike_prices = []
atm_strike = None
ce_signals = []
pe_signals = []
atm_ce_plus20 = None   # User input
atm_pe_plus20 = None   # User input
option_chain_df = pd.DataFrame()
expiry_date = "-"
st_prefix = "NSE:NIFTY25923"   # Default value for ST

@app.route("/", methods=["GET", "POST"])
def index():
    global st_prefix, atm_ce_plus20, atm_pe_plus20
    if request.method == "POST":
        # Capture ST prefix
        st_prefix = request.form.get("st_prefix", st_prefix).strip()

        # Capture CE +20 threshold
        ce_input = request.form.get("atm_ce_plus20")
        if ce_input:
            try:
                atm_ce_plus20 = float(ce_input)
            except ValueError:
                atm_ce_plus20 = None

        # Capture PE +20 threshold
        pe_input = request.form.get("atm_pe_plus20")
        if pe_input:
            try:
                atm_pe_plus20 = float(pe_input)
            except ValueError:
                atm_pe_plus20 = None

    return render_template("index.html",
                           atm_strike=atm_strike,
                           atm_ce_plus20=atm_ce_plus20,
                           atm_pe_plus20=atm_pe_plus20,
                           ce_signals=ce_signals,
                           pe_signals=pe_signals,
                           option_chain=option_chain_df.to_dict(orient='records'),
                           expiry_date=expiry_date,
                           st_prefix=st_prefix)

@app.route("/login")
def login():
    login_url = appSession.generate_authcode()
    threading.Thread(target=lambda: webbrowser.open_new(login_url)).start()  # Open in new window
    return redirect(login_url)

@app.route("/callback")
def callback():
    global fyers, access_token
    auth_code = request.args.get("auth_code")
    if not auth_code:
        return "Auth failed"

    appSession.set_token(auth_code)
    token_response = appSession.generate_token()
    access_token = token_response.get("access_token")
    fyers = fyersModel.FyersModel(
        client_id=client_id,
        token=access_token,
        is_async=False,
        log_path=""
    )
    return redirect(url_for("index"))

@app.route("/fetch")
def fetch_option_chain():
    global fyers, strike_prices, atm_strike, option_chain_df
    global atm_ce_plus20, atm_pe_plus20, expiry_date

    if fyers is None:
        return "Not authenticated"

    try:
        data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 10, "timestamp": ""}
        response = fyers.optionchain(data=data)
        if "data" not in response or "optionsChain" not in response["data"]:
            return f"Error: {response}"

        options_data = response["data"]["optionsChain"]
        df = pd.DataFrame(options_data)
        expiry_date = "-"
        for option in options_data:
            if "CE" in option and "expiryDate" in option["CE"]:
                expiry_date = option["CE"]["expiryDate"]; break
            elif "PE" in option and "expiryDate" in option["PE"]:
                expiry_date = option["PE"]["expiryDate"]; break

        df_pivot = df.pivot_table(index="strike_price", columns="option_type", values="ltp", aggfunc="first").reset_index()
        df_pivot = df_pivot.rename(columns={"CE": "CE_LTP", "PE": "PE_LTP"})

        if not strike_prices:
            strike_prices.extend(df_pivot["strike_price"].tolist())

        nifty_spot = response["data"].get("underlyingValue", strike_prices[len(strike_prices) // 2])
        atm_strike = min(strike_prices, key=lambda x: abs(x - nifty_spot))
        option_chain_df = df_pivot

        atm_row = df_pivot[df_pivot["strike_price"] == atm_strike]
        if not atm_row.empty:
            ce_ltp = atm_row["CE_LTP"].values[0]
            pe_ltp = atm_row["PE_LTP"].values[0]

            # Signal logic using user-defined thresholds
            if atm_ce_plus20 and ce_ltp > atm_ce_plus20 and not ce_signals:
                ce_signals.append({"strike": atm_strike, "price": ce_ltp})
                place_order(f"{st_prefix}{atm_strike}CE", ce_ltp, side=1)

            if atm_pe_plus20 and pe_ltp > atm_pe_plus20 and not pe_signals:
                pe_signals.append({"strike": atm_strike, "price": pe_ltp})
                place_order(f"{st_prefix}{atm_strike}PE", pe_ltp, side=1)

        return redirect(url_for("index"))

    except Exception as e:
        return str(e)

@app.route("/buy_ce")
def buy_ce():
    global atm_strike, ce_signals
    if fyers is None or atm_strike is None:
        return "Not ready"
    try:
        atm_row = option_chain_df[option_chain_df["strike_price"] == atm_strike]
        if not atm_row.empty:
            ce_ltp = atm_row["CE_LTP"].values[0]
            ce_signals.append({"strike": atm_strike, "price": ce_ltp})
            place_order(f"{st_prefix}{atm_strike}CE", ce_ltp, side=1)
    except Exception as e:
        return str(e)
    return redirect(url_for("index"))

@app.route("/buy_pe")
def buy_pe():
    global atm_strike, pe_signals
    if fyers is None or atm_strike is None:
        return "Not ready"
    try:
        atm_row = option_chain_df[option_chain_df["strike_price"] == atm_strike]
        if not atm_row.empty:
            pe_ltp = atm_row["PE_LTP"].values[0]
            pe_signals.append({"strike": atm_strike, "price": pe_ltp})
            place_order(f"{st_prefix}{atm_strike}PE", pe_ltp, side=1)
    except Exception as e:
        return str(e)
    return redirect(url_for("index"))

@app.route("/exit")
def exit_orders():
    global ce_signals, pe_signals
    if fyers is None:
        return "Not authenticated"

    for signal in ce_signals:
        place_order(f"{st_prefix}{signal['strike']}CE", signal["price"], side=2)
    for signal in pe_signals:
        place_order(f"{st_prefix}{signal['strike']}PE", signal["price"], side=2)

    ce_signals.clear()
    pe_signals.clear()
    return redirect(url_for("index"))

@app.route("/reset")
def reset_constants():
    global atm_ce_plus20, atm_pe_plus20, ce_signals, pe_signals
    atm_ce_plus20 = None
    atm_pe_plus20 = None
    ce_signals.clear()
    pe_signals.clear()
    return redirect(url_for("index"))

def place_order(symbol, price, side):
    try:
        if fyers is None:
            return
        data = {
            "symbol": symbol,
            "qty": 75,
            "type": 1,
            "side": side,
            "productType": "INTRADAY",
            "limitPrice": price,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag": "signalorder"
        }
        response = fyers.place_order(data=data)
        print("Order placed:", response)
    except Exception as e:
        print("Order error:", e)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
