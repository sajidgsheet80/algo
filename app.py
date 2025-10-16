from fyers_apiv3 import fyersModel
from flask import Flask, redirect, request, render_template_string, session, flash, make_response
import webbrowser
import pandas as pd
import os
import math
import traceback
import json
from datetime import datetime, timedelta, date
from collections import deque
import pytz  # Added for timezone handling
import hashlib
import secrets

# ---- Timezone Function ----
def get_mumbai_time():
    """Get current time in Mumbai (IST) timezone"""
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist)

# ---- Option Pricing Functions ----
def calculate_option_fair_value(spot_price, strike_price, option_type, days_to_expiry=7, volatility=0.2, risk_free_rate=0.06):
    """
    Calculate fair value of an option using a simplified Black-Scholes model
    For educational purposes only
    """
    try:
        # Convert days to years
        t = max(days_to_expiry / 365.0, 0.01)  # Minimum 1 day to avoid division by zero
        
        # Calculate d1 and d2 parameters
        if strike_price > 0:
            d1 = (math.log(spot_price / strike_price) + (risk_free_rate + 0.5 * volatility ** 2) * t) / (volatility * math.sqrt(t))
            d2 = d1 - volatility * math.sqrt(t)
        else:
            return 0
        
        # Calculate fair value based on option type
        if option_type == "CE":
            # Call option fair value
            fair_value = spot_price * norm_cdf(d1) - strike_price * math.exp(-risk_free_rate * t) * norm_cdf(d2)
        else:
            # Put option fair value
            fair_value = strike_price * math.exp(-risk_free_rate * t) * norm_cdf(-d2) - spot_price * norm_cdf(-d1)
        
        return max(fair_value, 0)  # Options can't have negative value
    except:
        return 0

def norm_cdf(x):
    """Standard normal CDF function"""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

def calculate_profit_probability(spot_price, strike_price, option_type, days_to_expiry=7, volatility=0.2):
    """
    Calculate probability of the option being profitable at expiry
    For educational purposes only
    """
    try:
        # Convert days to years
        t = max(days_to_expiry / 365.0, 0.01)  # Minimum 1 day to avoid division by zero
        
        # Calculate the probability
        if option_type == "CE":
            # For call options, probability that spot > strike at expiry
            if strike_price > 0 and spot_price > 0:
                d = (math.log(spot_price / strike_price) - 0.5 * volatility ** 2 * t) / (volatility * math.sqrt(t))
                return 1 - norm_cdf(d)
        else:
            # For put options, probability that spot < strike at expiry
            if strike_price > 0 and spot_price > 0:
                d = (math.log(spot_price / strike_price) - 0.5 * volatility ** 2 * t) / (volatility * math.sqrt(t))
                return norm_cdf(d)
        
        return 0.5  # Default 50% if calculation fails
    except:
        return 0.5  # Default 50% if calculation fails

def calculate_risk_reward(spot_price, strike_price, option_type, ltp, days_to_expiry=7):
    """
    Calculate risk/reward ratio for the option
    For educational purposes only
    """
    try:
        if option_type == "CE":
            # For call options
            intrinsic_value = max(spot_price - strike_price, 0)
            potential_profit = intrinsic_value - ltp
            risk = ltp  # Maximum loss is the premium paid
        else:
            # For put options
            intrinsic_value = max(strike_price - spot_price, 0)
            potential_profit = intrinsic_value - ltp
            risk = ltp  # Maximum loss is the premium paid
        
        if risk > 0:
            return potential_profit / risk
        return 0
    except:
        return 0

def get_best_options(df, spot_price, option_type="PE", limit=5):
    """
    Get the best ATM/ITM options based on fair value discount
    """
    try:
        # Filter options by type
        filtered_df = df[df["option_type"] == option_type].copy()
        
        # Calculate fair value, discount, profit probability, and risk/reward
        filtered_df["fair_value"] = filtered_df.apply(
            lambda row: calculate_option_fair_value(
                spot_price, row["strike_price"], option_type, days_to_expiry=7
            ), axis=1
        )
        
        filtered_df["discount"] = ((filtered_df["fair_value"] - filtered_df["ltp"]) / filtered_df["fair_value"] * 100)
        
        filtered_df["profit_probability"] = filtered_df.apply(
            lambda row: calculate_profit_probability(
                spot_price, row["strike_price"], option_type, days_to_expiry=7
            ), axis=1
        )
        
        filtered_df["risk_reward"] = filtered_df.apply(
            lambda row: calculate_risk_reward(
                spot_price, row["strike_price"], option_type, row["ltp"], days_to_expiry=7
            ), axis=1
        )
        
        # Filter for ATM and ITM options
        if option_type == "PE":
            # For PE options, ITM means strike > spot
            atm_itm_df = filtered_df[filtered_df["strike_price"] >= spot_price - 100]
        else:
            # For CE options, ITM means strike < spot
            atm_itm_df = filtered_df[filtered_df["strike_price"] <= spot_price + 100]
        
        # Sort by discount (highest first) and take top options
        best_options = atm_itm_df.sort_values("discount", ascending=False).head(limit)
        
        return best_options
    except Exception as e:
        print(f"Error getting best options: {e}")
        return pd.DataFrame()

# ---- Gamma Exposure Functions ----
def calculate_gamma_exposure(spot_price, strike_price, option_type, volume, volume_change, oi, oi_change):
    """
    Calculate a gamma exposure score based on multiple factors
    Higher score indicates higher potential for gamma blast
    """
    try:
        # Distance from ATM (closer = higher gamma)
        distance_from_atm = abs(spot_price - strike_price) / spot_price
        proximity_score = max(0, 1 - distance_from_atm) * 30  # Max 30 points
        
        # Volume change factor (higher change = higher gamma exposure)
        if volume > 0 and volume_change is not None:
            volume_change_pct = abs(volume_change) / volume
            volume_score = min(volume_change_pct * 100, 30)  # Max 30 points
        else:
            volume_score = 0
            
        # OI change factor (higher change = higher gamma exposure)
        if oi > 0 and oi_change is not None:
            oi_change_pct = abs(oi_change) / oi
            oi_score = min(oi_change_pct * 100, 30)  # Max 30 points
        else:
            oi_score = 0
            
        # Option type factor (ATM options have higher gamma)
        if option_type == "CE":
            if strike_price <= spot_price:
                type_score = 10  # ITM CE
            else:
                type_score = 5   # OTM CE
        else:  # PE
            if strike_price >= spot_price:
                type_score = 10  # ITM PE
            else:
                type_score = 5   # OTM PE
                
        # Total gamma exposure score
        gamma_score = proximity_score + volume_score + oi_score + type_score
        
        return gamma_score
    except:
        return 0

def get_best_gamma_options(df, spot_price, limit=5):
    """
    Get the best options with high gamma exposure
    """
    try:
        # Calculate gamma exposure score for each option
        df["gamma_score"] = df.apply(
            lambda row: calculate_gamma_exposure(
                spot_price, 
                row["strike_price"], 
                row["option_type"],
                row.get("volume", 0),
                row.get("vol_change", 0),
                row.get("oi", 0),
                row.get("oi_change", 0)
            ), axis=1
        )
        
        # Sort by gamma score and take top options
        best_options = df.sort_values("gamma_score", ascending=False).head(limit)
        
        return best_options
    except Exception as e:
        print(f"Error getting best gamma options: {e}")
        return pd.DataFrame()

# ---- Read Fyers Credentials from File ----
def read_fyers_credentials():
    """Read Fyers credentials from cred.txt file"""
    try:
        with open('cred.txt', 'r') as file:
            lines = file.readlines()
            credentials = {}
            for line in lines:
                if '=' in line:
                    key, value = line.strip().split('=', 1)
                    credentials[key.strip()] = value.strip()

            return {
                'client_id': credentials.get('client_id', ''),
                'secret_key': credentials.get('secret_key', ''),
                'redirect_uri': credentials.get('redirect_uri', '')
            }
    except Exception as e:
        print(f"Error reading credentials: {e}")
        # Return default values if file doesn't exist or error occurs
        return {
            'client_id': "VMS68P9EK0-100",
            'secret_key': "ZJ0CFWZEL1",
            'redirect_uri': "http://localhost:5000/callback"
        }

# ---- User Data Management ----
USER_DATA_DIR = "user_data"
if not os.path.exists(USER_DATA_DIR):
    os.makedirs(USER_DATA_DIR)

# User-specific scalping positions
user_scalping_positions = {}  # {username: {date: [positions]}}

def get_user_positions_file(username, trading_date=None):
    """Get the file path for a user's positions on a specific date"""
    if trading_date is None:
        trading_date = date.today().strftime("%Y-%m-%d")
    return os.path.join(USER_DATA_DIR, f"{username}_{trading_date}.json")

def save_user_positions(username):
    """Save a user's positions to disk"""
    if username not in user_scalping_positions:
        return False
    
    try:
        today = date.today().strftime("%Y-%m-%d")
        file_path = get_user_positions_file(username, today)
        
        with open(file_path, 'w') as f:
            json.dump(user_scalping_positions[username], f)
        
        return True
    except Exception as e:
        print(f"Error saving positions for {username}: {e}")
        return False

def load_user_positions(username):
    """Load a user's positions from disk"""
    try:
        today = date.today().strftime("%Y-%m-%d")
        file_path = get_user_positions_file(username, today)
        
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                user_scalping_positions[username] = json.load(f)
            return True
        return False
    except Exception as e:
        print(f"Error loading positions for {username}: {e}")
        return False

def get_user_position_history(username):
    """Get a list of dates for which the user has saved positions"""
    try:
        user_files = [f for f in os.listdir(USER_DATA_DIR) if f.startswith(f"{username}_")]
        dates = [f.replace(f"{username}_", "").replace(".json", "") for f in user_files]
        return sorted(dates, reverse=True)  # Most recent first
    except Exception as e:
        print(f"Error getting position history for {username}: {e}")
        return []

def load_user_positions_by_date(username, trading_date):
    """Load a user's positions from a specific date"""
    try:
        file_path = get_user_positions_file(username, trading_date)
        
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"Error loading positions for {username} on {trading_date}: {e}")
        return {}

# ---- Remember Me Token Management ----
REMEMBER_ME_TOKENS_FILE = os.path.join(USER_DATA_DIR, "remember_me_tokens.json")

def load_remember_me_tokens():
    """Load remember me tokens from file"""
    try:
        if os.path.exists(REMEMBER_ME_TOKENS_FILE):
            with open(REMEMBER_ME_TOKENS_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"Error loading remember me tokens: {e}")
        return {}

def save_remember_me_tokens(tokens):
    """Save remember me tokens to file"""
    try:
        with open(REMEMBER_ME_TOKENS_FILE, 'w') as f:
            json.dump(tokens, f)
        return True
    except Exception as e:
        print(f"Error saving remember me tokens: {e}")
        return False

def generate_remember_me_token(username):
    """Generate a secure remember me token for a user"""
    try:
        # Load existing tokens
        tokens = load_remember_me_tokens()
        
        # Generate a secure random token
        token = secrets.token_urlsafe(32)
        
        # Store token with username and expiry (30 days from now)
        expiry = (datetime.now() + timedelta(days=30)).timestamp()
        tokens[token] = {
            'username': username,
            'expiry': expiry
        }
        
        # Save updated tokens
        save_remember_me_tokens(tokens)
        
        return token
    except Exception as e:
        print(f"Error generating remember me token: {e}")
        return None

def validate_remember_me_token(token):
    """Validate a remember me token and return the associated username"""
    try:
        # Load tokens
        tokens = load_remember_me_tokens()
        
        # Check if token exists and is not expired
        if token in tokens:
            token_data = tokens[token]
            if token_data['expiry'] > datetime.now().timestamp():
                return token_data['username']
            else:
                # Token expired, remove it
                del tokens[token]
                save_remember_me_tokens(tokens)
        
        return None
    except Exception as e:
        print(f"Error validating remember me token: {e}")
        return None

def remove_remember_me_token(token):
    """Remove a remember me token"""
    try:
        tokens = load_remember_me_tokens()
        if token in tokens:
            del tokens[token]
            save_remember_me_tokens(tokens)
        return True
    except Exception as e:
        print(f"Error removing remember me token: {e}")
        return False

# ---- Credentials ----
fyers_creds = read_fyers_credentials()
client_id = fyers_creds['client_id']
secret_key = fyers_creds['secret_key']
redirect_uri = fyers_creds['redirect_uri']

# ---- Session ----
appSession = fyersModel.SessionModel(
    client_id=client_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type="code",
    grant_type="authorization_code",
    state="sample"
)

# ---- Flask ----
app = Flask(__name__)
app.secret_key = "sajid_secret"
fyers = None
fyers_token_expiry = None

# ---- Symbol Mapping ----
symbols_map = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCAPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX"
}

display_cols = ["ask", "bid", "ltp", "ltpch", "option_type", "strike_price",
                "oi", "oich", "oichp", "prev_oi", "volume"]

previous_data = {}  # Store previous rows for diff

# ---- User Management ----
# In a production environment, you would use a proper database
# For this example, we'll use a simple in-memory dictionary
users = {
    "admin": {
        "password": hashlib.sha256("admin123".encode()).hexdigest(),
        "role": "admin",
        "name": "Administrator",
        "mobile": "+919876543210"  # Added mobile number for admin
    }
}

# Track logged-in users
logged_in_users = {}  # {username: login_time}

# ---- Historical Data Storage ----
# Structure: {index_name: {strike_type_key: deque([(timestamp, volume, oi), ...])}}
historical_data = {}
TRACKING_INTERVALS = [1, 2, 5, 10]  # Minutes to track

def format_to_crore(value):
    """Format a number to crore (10 million) units"""
    if pd.isna(value) or value == 0:
        return "0.00"
    return f"{value/10000000:.2f} Cr"

def get_strike_key(strike, option_type):
    """Generate unique key for strike-option combination"""
    return f"{strike}_{option_type}"

def update_historical_data(index_name, strike, option_type, volume, oi):
    """Store historical volume and OI data"""
    if index_name not in historical_data:
        historical_data[index_name] = {}

    key = get_strike_key(strike, option_type)
    if key not in historical_data[index_name]:
        historical_data[index_name][key] = deque(maxlen=600)  # Keep 10 minutes at 1sec intervals

    # Use Mumbai time instead of local time
    timestamp = get_mumbai_time().timestamp()
    historical_data[index_name][key].append((timestamp, volume, oi))

def get_change_data(index_name, strike, option_type, minutes):
    """Calculate volume and OI change over specified minutes"""
    if index_name not in historical_data:
        return None, None

    key = get_strike_key(strike, option_type)
    if key not in historical_data[index_name]:
        return None, None

    data_queue = historical_data[index_name][key]
    if len(data_queue) < 2:
        return None, None

    # Use Mumbai time instead of local time
    current_time = get_mumbai_time().timestamp()
    target_time = current_time - (minutes * 60)

    # Get most recent data
    current_timestamp, current_volume, current_oi = data_queue[-1]

    # Find data point closest to target time
    old_data = None
    for timestamp, volume, oi in data_queue:
        if timestamp >= target_time:
            old_data = (timestamp, volume, oi)
            break

    if old_data is None:
        # Use oldest available data if not enough history
        old_data = data_queue[0]

    old_timestamp, old_volume, old_oi = old_data

    volume_change = current_volume - old_volume
    oi_change = current_oi - old_oi

    return volume_change, oi_change

def validate_fyers_token():
    """Check if Fyers token is valid and refresh if needed"""
    global fyers, fyers_token_expiry

    if fyers is None:
        return False

    # Check if token is expired
    if fyers_token_expiry and datetime.now() > fyers_token_expiry:
        try:
            # Try to refresh the token
            token_response = appSession.generate_token()
            access_token = token_response.get("access_token")
            if access_token:
                fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, is_async=False)
                fyers_token_expiry = datetime.now() + timedelta(hours=23)  # Set new expiry
                return True
            else:
                fyers = None
                return False
        except:
            fyers = None
            return False

    return True

def is_logged_in():
    """Check if user is logged in"""
    return 'username' in session

def is_admin():
    """Check if current user is admin"""
    return is_logged_in() and users.get(session.get('username'), {}).get('role') == 'admin'

def check_remember_me():
    """Check for valid remember me cookie and log in user if valid"""
    remember_me_token = request.cookies.get('remember_me_token')
    if remember_me_token and not is_logged_in():
        username = validate_remember_me_token(remember_me_token)
        if username and username in users:
            # Log in the user
            session['username'] = username
            session['name'] = users[username]["name"]
            session['role'] = users[username]["role"]
            
            # Track login time
            logged_in_users[username] = get_mumbai_time()
            
            # Load user's positions
            load_user_positions(username)
            
            return True
    return False

@app.before_request
def before_request():
    """Check for remember me cookie before each request"""
    if not is_logged_in() and request.endpoint and request.endpoint != 'static':
        check_remember_me()

@app.route("/")
def home():
    if not is_logged_in():
        return """<center>
        <h1 style="fond-size:80;color:green">Sajid Shaikh Algo Software : +91 9834370368</h1></center>
        <a href="/login">🔑 Login</a>
        <hr>
        <p>Use the dropdown on pages to switch indices. Auto-refresh every second.</p>
        """

    admin_links = ""
    if is_admin():
        admin_links = f"""
        <a href="/users" target="_blank">👥 Manage Users</a> |
        <a href="/logged_in_users" target="_blank">🔍 Logged In Users ({len(logged_in_users)})</a> |
        """

    return f"""<center>

    <h1 style="color:red">For Eductaion Purpose Only</h1>
    <h1 style="color:green">Sajid Shaikh Scalping Trading App : +91 9834370368</h1></center>
    {admin_links}
    <a href="/chain?index=NIFTY50" target="_blank">📊 View Option Chain</a> |
    <a href="/scalping?index=NIFTY50" target="_blank">⚡ Scalping Dashboard</a> |
    <a href="/logout">🔓 Logout</a>
    <hr>

    <pre >









Stock market education and practice are essential for anyone looking to invest wisely and build financial security.
Understanding how markets work, analyzing stocks, and recognizing risks helps investors make informed decisions.
Without proper knowledge, one may fall into common traps or emotional investing.
Practicing with simulations or small investments allows individuals to apply concepts, test strategies, and build confidence without risking large sums.
Education empowers investors to stay updated with market trends, economic indicators, and company performance.
In a dynamic and complex financial world, continuous learning and hands-on experience are key to long-term success in the stock market.
</pre>

    """

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        remember_me = request.form.get("remember_me") == "on"

        if username in users:
            hashed_password = hashlib.sha256(password.encode()).hexdigest()
            if users[username]["password"] == hashed_password:
                session['username'] = username
                session['name'] = users[username]["name"]
                session['role'] = users[username]["role"]

                # Track login time
                logged_in_users[username] = get_mumbai_time()
                
                # Load user's positions
                load_user_positions(username)

                # Handle remember me
                response = make_response(redirect("/"))
                if remember_me:
                    token = generate_remember_me_token(username)
                    if token:
                        # Set cookie that expires in 30 days
                        response.set_cookie('remember_me_token', token, max_age=30*24*60*60, secure=True, httponly=True)
                else:
                    # Clear any existing remember me cookie
                    response.set_cookie('remember_me_token', '', expires=0)

                # If admin, check if Fyers is configured
                if users[username]["role"] == "admin":
                    if fyers is None:
                        flash("Please configure Fyers API first", "warning")
                        return redirect("/fyers_setup")

                return response

        flash("Invalid username or password", "error")
        return redirect("/login")

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <title>Login - Sajid Shaikh Algo Software</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }
            .container { max-width: 400px; margin: 50px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { text-align:center; color:#1a73e8; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
            .checkbox-group { display: flex; align-items: center; margin-bottom: 15px; }
            .checkbox-group input { width: auto; margin-right: 8px; }
            button { width: 100%; padding: 10px; background: #1a73e8; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
            button:hover { background: #1558b8; }
            .register-link { text-align: center; margin-top: 15px; }
            .alert { padding: 10px; margin-bottom: 15px; border-radius: 4px; }
            .alert-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
            .alert-warning { background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Sajid Shaikh Algo Software</h1>
            <h2>Login</h2>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            <form method="post">
                <div class="form-group">
                    <label for="username">Username:</label>
                    <input type="text" id="username" name="username" required>
                </div>
                <div class="form-group">
                    <label for="password">Password:</label>
                    <input type="password" id="password" name="password" required>
                </div>
                <div class="checkbox-group">
                    <input type="checkbox" id="remember_me" name="remember_me">
                    <label for="remember_me">Remember me for 30 days</label>
                </div>
                <button type="submit">Login</button>
            </form>
            <div class="register-link">
                <p>Don't have an account? <a href="/register">Register here</a></p>
            </div>
        </div>
    </body>
    </html>
    """)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        name = request.form.get("name")
        mobile = request.form.get("mobile")

        if not username or not password or not name or not mobile:
            flash("All fields are required", "error")
            return redirect("/register")

        if username in users:
            flash("Username already exists", "error")
            return redirect("/register")

        # Validate mobile number format (basic validation)
        if not mobile.startswith('+'):
            flash("Mobile number must include country code (e.g., +91)", "error")
            return redirect("/register")

        # Create new user with regular role
        users[username] = {
            "password": hashlib.sha256(password.encode()).hexdigest(),
            "role": "user",
            "name": name,
            "mobile": mobile  # Added mobile number
        }

        flash("Registration successful. Please login.", "success")
        return redirect("/login")

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <title>Register - Sajid Shaikh Algo Software</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }
            .container { max-width: 400px; margin: 50px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { text-align:center; color:#1a73e8; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
            button { width: 100%; padding: 10px; background: #1a73e8; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
            button:hover { background: #1558b8; }
            .login-link { text-align: center; margin-top: 15px; }
            .alert { padding: 10px; margin-bottom: 15px; border-radius: 4px; }
            .alert-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
            .alert-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
            .help-text { font-size: 12px; color: #666; margin-top: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Sajid Shaikh Algo Software</h1>
            <h2>Register</h2>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            <form method="post">
                <div class="form-group">
                    <label for="name">Full Name:</label>
                    <input type="text" id="name" name="name" required>
                </div>
                <div class="form-group">
                    <label for="username">Username:</label>
                    <input type="text" id="username" name="username" required>
                </div>
                <div class="form-group">
                    <label for="password">Password:</label>
                    <input type="password" id="password" name="password" required>
                </div>
                <div class="form-group">
                    <label for="mobile">Mobile Number:</label>
                    <input type="text" id="mobile" name="mobile" placeholder="+91XXXXXXXXXX" required>
                    <div class="help-text">Please include country code (e.g., +91)</div>
                </div>
                <button type="submit">Register</button>
            </form>
            <div class="login-link">
                <p>Already have an account? <a href="/login">Login here</a></p>
            </div>
        </div>
    </body>
    </html>
    """)

@app.route("/logout")
def logout():
    username = session.get('username')
    if username:
        # Save user positions before logout
        save_user_positions(username)
        
        if username in logged_in_users:
            del logged_in_users[username]

    # Clear session
    session.clear()
    
    # Clear remember me cookie if exists
    remember_me_token = request.cookies.get('remember_me_token')
    if remember_me_token:
        remove_remember_me_token(remember_me_token)
    
    response = make_response(redirect("/login"))
    response.set_cookie('remember_me_token', '', expires=0)
    
    return response

@app.route("/logged_in_users")
def logged_in_users_page():
    if not is_admin():
        flash("Access denied. Admin privileges required.", "error")
        return redirect("/")

    users_html = ""
    for username, login_time in logged_in_users.items():
        user_info = users.get(username, {})
        name = user_info.get("name", "Unknown")
        mobile = user_info.get("mobile", "Not provided")
        role = user_info.get("role", "user")

        # Format login time
        formatted_time = login_time.strftime("%Y-%m-%d %H:%M:%S")

        users_html += f"""
        <tr>
            <td>{username}</td>
            <td>{name}</td>
            <td>{mobile}</td>
            <td>{role}</td>
            <td>{formatted_time}</td>
        </tr>
        """

    if not users_html:
        users_html = "<tr><td colspan='5'>No users are currently logged in.</td></tr>"

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <title>Logged In Users - Sajid Shaikh Algo Software</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }
            .container { max-width: 1000px; margin: 20px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { text-align:center; color:#1a73e8; }
            .user-info { float: right; padding: 10px; background: #e3f2fd; border-radius: 4px; }
            .logout { float: right; margin-left: 10px; }
            .back-link { margin-bottom: 20px; }
            .users-table { width:100%; border-collapse: collapse; margin-top: 20px; }
            .users-table th { background:#1a73e8; color:#fff; padding: 10px; text-align: left; }
            .users-table td { border:1px solid #ddd; padding: 10px; }
            .users-table tr:nth-child(even) { background:#f7f7f7; }
            .alert { padding: 10px; margin-bottom: 15px; border-radius: 4px; }
            .alert-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
            .stats { display: flex; justify-content: space-between; margin-bottom: 20px; }
            .stat-card { background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center; flex: 1; margin: 0 10px; }
            .stat-value { font-size: 24px; font-weight: bold; color: #1a73e8; }
            .stat-label { color: #666; font-size: 14px; }
        </style>
    </head>
    <body>
        <div class="user-info">
            Welcome, {{ session.get('name', session.get('username')) }}
            <a href="/logout" class="logout">Logout</a>
        </div>

        <div class="container">
            <h1>Logged In Users</h1>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value">{{ logged_in_users|length }}</div>
                    <div class="stat-label">Total Logged In Users</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{{ total_users }}</div>
                    <div class="stat-label">Total Registered Users</div>
                </div>
            </div>

            <div class="back-link">
                <a href="/">← Back to Dashboard</a>
            </div>

            <table class="users-table">
                <thead>
                    <tr>
                        <th>Username</th>
                        <th>Name</th>
                        <th>Mobile</th>
                        <th>Role</th>
                        <th>Login Time (IST)</th>
                    </tr>
                </thead>
                <tbody>
                    {{ users_html|safe }}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """, users_html=users_html, total_users=len(users))

@app.route("/users")
def manage_users():
    if not is_admin():
        flash("Access denied. Admin privileges required.", "error")
        return redirect("/")

    users_html = ""
    for username, user_info in users.items():
        name = user_info.get("name", "Unknown")
        mobile = user_info.get("mobile", "Not provided")
        role = user_info.get("role", "user")

        # Check if user is logged in
        is_logged_in_status = "Yes" if username in logged_in_users else "No"
        login_time = logged_in_users.get(username, None)
        login_time_str = login_time.strftime("%Y-%m-%d %H:%M:%S") if login_time else "N/A"

        users_html += f"""
        <tr>
            <td>{username}</td>
            <td>{name}</td>
            <td>{mobile}</td>
            <td>{role}</td>
            <td>{is_logged_in_status}</td>
            <td>{login_time_str}</td>
        </tr>
        """

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <title>Manage Users - Sajid Shaikh Algo Software</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }
            .container { max-width: 1000px; margin: 20px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { text-align:center; color:#1a73e8; }
            .user-info { float: right; padding: 10px; background: #e3f2fd; border-radius: 4px; }
            .logout { float: right; margin-left: 10px; }
            .back-link { margin-bottom: 20px; }
            .users-table { width:100%; border-collapse: collapse; margin-top: 20px; }
            .users-table th { background:#1a73e8; color:#fff; padding: 10px; text-align: left; }
            .users-table td { border:1px solid #ddd; padding: 10px; }
            .users-table tr:nth-child(even) { background:#f7f7f7; }
            .alert { padding: 10px; margin-bottom: 15px; border-radius: 4px; }
            .alert-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
            .stats { display: flex; justify-content: space-between; margin-bottom: 20px; }
            .stat-card { background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center; flex: 1; margin: 0 10px; }
            .stat-value { font-size: 24px; font-weight: bold; color: #1a73e8; }
            .stat-label { color: #666; font-size: 14px; }
        </style>
    </head>
    <body>
        <div class="user-info">
            Welcome, {{ session.get('name', session.get('username')) }}
            <a href="/logout" class="logout">Logout</a>
        </div>

        <div class="container">
            <h1>Manage Users</h1>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value">{{ logged_in_users|length }}</div>
                    <div class="stat-label">Currently Logged In</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{{ total_users }}</div>
                    <div class="stat-label">Total Registered Users</div>
                </div>
            </div>

            <div class="back-link">
                <a href="/">← Back to Dashboard</a>
            </div>

            <table class="users-table">
                <thead>
                    <tr>
                        <th>Username</th>
                        <th>Name</th>
                        <th>Mobile</th>
                        <th>Role</th>
                        <th>Logged In</th>
                        <th>Last Login</th>
                    </tr>
                </thead>
                <tbody>
                    {{ users_html|safe }}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """, users_html=users_html, total_users=len(users))

@app.route("/fyers_setup", methods=["GET", "POST"])
def fyers_setup():
    if not is_admin():
        flash("Access denied. Admin privileges required.", "error")
        return redirect("/login")

    if request.method == "POST":
        # Start Fyers authentication process
        login_url = appSession.generate_authcode()
        webbrowser.open(login_url, new=1)
        return redirect(login_url)

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <title>Fyers Setup - Sajid Shaikh Algo Software</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }
            .container { max-width: 600px; margin: 50px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { text-align:center; color:#1a73e8; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            button { width: 100%; padding: 10px; background: #1a73e8; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
            button:hover { background: #1558b8; }
            .alert { padding: 10px; margin-bottom: 15px; border-radius: 4px; }
            .alert-warning { background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
            .alert-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
            .credentials-info { background: #e3f2fd; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
            .credentials-info h3 { margin-top: 0; color: #1a73e8; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Sajid Shaikh Algo Software</h1>
            <h2>Fyers API Setup</h2>

            <div class="credentials-info">
                <h3>Current Credentials</h3>
                <p><strong>Client ID:</strong> {{ client_id }}</p>
                <p><strong>Secret Key:</strong> {{ secret_key[:5] }}...{{ secret_key[-5:] }}</p>
                <p><strong>Redirect URI:</strong> {{ redirect_uri }}</p>
                <p><small>Credentials are read from cred.txt file</small></p>
            </div>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            <p>Please authenticate with Fyers to enable option chain data.</p>
            <p>Click the button below to open the Fyers authentication page in your browser.</p>

            <form method="post">
                <button type="submit">Authenticate with Fyers</button>
            </form>
        </div>
    </body>
    </html>
    """, client_id=client_id, secret_key=secret_key, redirect_uri=redirect_uri)

@app.route("/callback")
def callback():
    global fyers, fyers_token_expiry
    auth_code = request.args.get("auth_code")
    if auth_code:
        try:
            appSession.set_token(auth_code)
            token_response = appSession.generate_token()
            access_token = token_response.get("access_token")
            if access_token:
                fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, is_async=False)
                fyers_token_expiry = datetime.now() + timedelta(hours=23)  # Set expiry time

                return render_template_string("""
                <!doctype html>
                <html>
                <head>
                    <title>Fyers Setup Complete</title>
                    <style>
                        body { font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }
                        .container { max-width: 600px; margin: 50px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; }
                        h1 { color:#1a73e8; }
                        .btn { display: inline-block; padding: 10px 20px; background: #1a73e8; color: white; text-decoration: none; border-radius: 4px; margin-top: 20px; }
                        .btn:hover { background: #1558b8; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>✅ Authentication Successful!</h1>
                        <p>Fyers API has been successfully configured.</p>
                        <p>You can now use the application.</p>
                        <a href="/" class="btn">Go to Dashboard</a>
                    </div>
                </body>
                </html>
                """)
            else:
                return "<h3>❌ Failed to get access token</h3>"
        except Exception as e:
            return f"<h3>Callback error: {str(e)}</h3>"
    return "❌ Authentication failed. Please retry."

@app.route("/save_positions", methods=["POST"])
def save_positions():
    if not is_logged_in():
        return json.dumps({"status": "error", "message": "Please login first"})
    
    username = session.get('username')
    success = save_user_positions(username)
    
    if success:
        return json.dumps({"status": "success", "message": "Positions saved successfully"})
    else:
        return json.dumps({"status": "error", "message": "Failed to save positions"})

@app.route("/load_positions", methods=["POST"])
def load_positions():
    if not is_logged_in():
        return json.dumps({"status": "error", "message": "Please login first"})
    
    username = session.get('username')
    trading_date = request.form.get('date', date.today().strftime("%Y-%m-%d"))
    
    if trading_date == date.today().strftime("%Y-%m-%d"):
        # Load today's positions
        success = load_user_positions(username)
        message = "Today's positions loaded" if success else "No positions found for today"
    else:
        # Load positions from a specific date
        positions = load_user_positions_by_date(username, trading_date)
        if positions:
            user_scalping_positions[username] = positions
            message = f"Positions from {trading_date} loaded"
        else:
            message = f"No positions found for {trading_date}"
    
    return json.dumps({"status": "success", "message": message})

@app.route("/position_history")
def position_history():
    if not is_logged_in():
        return redirect("/login")
    
    username = session.get('username')
    history = get_user_position_history(username)
    
    return render_template_string("""
    <!doctype html>
    <html>
    <head>
        <title>Position History - Sajid Shaikh Algo Software</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }
            .container { max-width: 800px; margin: 20px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { text-align:center; color:#1a73e8; }
            .user-info { float: right; padding: 10px; background: #e3f2fd; border-radius: 4px; }
            .logout { float: right; margin-left: 10px; }
            .back-link { margin-bottom: 20px; }
            .history-table { width:100%; border-collapse: collapse; margin-top: 20px; }
            .history-table th { background:#1a73e8; color:#fff; padding: 10px; text-align: left; }
            .history-table td { border:1px solid #ddd; padding: 10px; }
            .history-table tr:nth-child(even) { background:#f7f7f7; }
            .btn { padding: 8px 16px; margin: 4px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
            .btn-primary { background: #1a73e8; color: white; }
            .btn-primary:hover { background: #1558b8; }
            .alert { padding: 10px; margin-bottom: 15px; border-radius: 4px; }
            .alert-info { background: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }
        </style>
    </head>
    <body>
        <div class="user-info">
            Welcome, {{ session.get('name', session.get('username')) }}
            <a href="/logout" class="logout">Logout</a>
        </div>

        <div class="container">
            <h1>Position History</h1>

            <div class="alert alert-info">
                Select a date to load positions from that trading day.
            </div>

            <div class="back-link">
                <a href="/scalping">← Back to Scalping Dashboard</a>
            </div>

            {% if history %}
            <table class="history-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {% for date in history %}
                    <tr>
                        <td>{{ date }}</td>
                        <td>
                            <button class="btn btn-primary" onclick="loadPositions('{{ date }}')">Load Positions</button>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p>No position history found.</p>
            {% endif %}
        </div>

        <script>
            function loadPositions(date) {
                fetch('/load_positions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: 'date=' + date
                })
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        alert(data.message);
                        window.location.href = '/scalping';
                    } else {
                        alert('Error: ' + data.message);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Error loading positions');
                });
            }
        </script>
    </body>
    </html>
    """, history=history)

@app.route("/scalping")
def scalping_dashboard():
    if not is_logged_in():
        return redirect("/login")

    if not validate_fyers_token():
        if is_admin():
            flash("Fyers token expired. Please re-authenticate.", "warning")
            return redirect("/fyers_setup")
        else:
            flash("Fyers API is not configured. Please contact the administrator.", "error")
            return redirect("/")

    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))

    # Get user info for display
    user_name = session.get('name', session.get('username'))
    user_mobile = users.get(session.get('username'), {}).get('mobile', 'Not provided') if is_admin() else None

    html = f"""
    <!doctype html>
    <html>
    <head>
        <title>{index_name} Scalping Dashboard</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }}
            h2 {{ text-align:center; color:#1a73e8; }}
            .container {{ max-width: 1800px; margin: 0 auto; }}
            .dropdown {{ margin:12px 0; text-align:center; background: white; padding: 15px; border-radius: 8px; }}
            .user-info {{ float: right; padding: 10px; background: #e3f2fd; border-radius: 4px; }}
            .logout {{ float: right; margin-left: 10px; }}

            .strategy-section {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .strategy-buttons {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 15px 0; }}
            .strategy-btn {{ padding: 12px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 14px; transition: all 0.3s; }}
            .strategy-btn:hover {{ transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.2); }}
            .btn-iron-condor {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
            .btn-straddle {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; }}
            .btn-strangle {{ background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); color: white; }}
            .btn-butterfly {{ background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); color: black; }}
            .btn-bull-call {{ background: linear-gradient(135deg, #fa709a 0%, #fee140 100%); color: black; }}
            .btn-bear-put {{ background: linear-gradient(135deg, #30cfd0 0%, #330867 100%); color: white; }}
            .btn-calendar {{ background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%); color: black; }}
            .btn-ratio {{ background: linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%); color: black; }}

            .positions-section {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .positions-table {{ width:100%; border-collapse: collapse; font-size:13px; }}
            .positions-table th {{ background:#1a73e8; color:#fff; padding: 10px; text-align: center; }}
            .positions-table td {{ border:1px solid #ddd; padding:8px; text-align:center; }}
            .positions-table tr:nth-child(even) {{ background:#f7f7f7; }}

            .profit {{ color: #0f9d58; font-weight: bold; }}
            .loss {{ color: #db4437; font-weight: bold; }}
            .neutral {{ color: #666; }}

            .btn {{ padding: 8px 16px; margin: 4px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }}
            .btn-buy {{ background: #0f9d58; color: white; }}
            .btn-sell {{ background: #db4437; color: white; }}
            .btn-exit {{ background: #f4b400; color: white; }}
            .btn-clear {{ background: #666; color: white; }}
            .btn-save {{ background: #4285f4; color: white; }}
            .btn-load {{ background: #34a853; color: white; }}
            .btn-history {{ background: #fbbc05; color: black; }}

            .opportunities {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; overflow-x: auto; }}
            .opp-table {{ width:100%; border-collapse: collapse; font-size:12px; }}
            .opp-table th {{ background:#f4b400; color:#000; padding: 10px; text-align: center; }}
            .opp-table td {{ border:1px solid #ddd; padding:8px; text-align:center; }}

            .best-options {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; overflow-x: auto; }}
            .best-options-table {{ width:100%; border-collapse: collapse; font-size:12px; }}
            .best-options-table th {{ background:#4caf50; color:#fff; padding: 10px; text-align: center; }}
            .best-options-table td {{ border:1px solid #ddd; padding:8px; text-align:center; }}
            .best-options-table tr:nth-child(even) {{ background:#f7f7f7; }}
            .discount-positive {{ color: #4caf50; font-weight: bold; }}
            .discount-negative {{ color: #f44336; font-weight: bold; }}
            .probability-high {{ color: #4caf50; font-weight: bold; }}
            .probability-medium {{ color: #ff9800; font-weight: bold; }}
            .probability-low {{ color: #f44336; font-weight: bold; }}
            .risk-reward-high {{ color: #4caf50; font-weight: bold; }}
            .risk-reward-medium {{ color: #ff9800; font-weight: bold; }}
            .risk-reward-low {{ color: #f44336; font-weight: bold; }}

            .gamma-options {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; overflow-x: auto; }}
            .gamma-options-table {{ width:100%; border-collapse: collapse; font-size:12px; }}
            .gamma-options-table th {{ background:#9c27b0; color:#fff; padding: 10px; text-align: center; }}
            .gamma-options-table td {{ border:1px solid #ddd; padding:8px; text-align:center; }}
            .gamma-options-table tr:nth-child(even) {{ background:#f7f7f7; }}
            .gamma-score-high {{ color: #9c27b0; font-weight: bold; font-size: 14px; }}
            .gamma-score-medium {{ color: #673ab7; font-weight: bold; }}
            .gamma-score-low {{ color: #3f51b5; font-weight: bold; }}

            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
            .stat-card {{ background: white; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .stat-value {{ font-size: 24px; font-weight: bold; margin: 10px 0; }}
            .stat-label {{ color: #666; font-size: 14px; }}

            .interval-selector {{ display: inline-block; margin: 0 10px; }}
            .interval-selector label {{ font-weight: bold; margin-right: 5px; }}
            .interval-selector select {{ padding: 5px; border-radius: 4px; }}

            .data-controls {{ text-align: center; margin: 15px 0; }}

            /* Highlight styles for highest values */
            .highest-volume {{ background-color: #e3f2fd !important; font-weight: bold; color: #0d47a1; }}
            .highest-vol-change {{ background-color: #e8f5e9 !important; font-weight: bold; color: #1b5e20; }}
            .highest-oi {{ background-color: #fff3e0 !important; font-weight: bold; color: #e65100; }}
            .highest-oi-change {{ background-color: #fce4ec !important; font-weight: bold; color: #880e4f; }}

            .strategy-badge {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 11px; font-weight: bold; margin-left: 5px; }}
        </style>
    </head>
    <body>
        <div class="user-info">
            <div>Welcome, {user_name}</div>
            {'<div>Mobile: ' + user_mobile + '</div>' if user_mobile else ''}
            <a href="/logout" class="logout">Logout</a>
        </div>

        <div class="container">
        <center><h1 aligh=center style="color:green;fond-size:70">Sajid Shaikh | (+91) 9834370368</h1></center>
            <h2>⚡ {index_name} Scalping Dashboard</h2>

            <div class="dropdown">
                <form method="get" action="/scalping" id="mainForm">
                    <label for="index">Select Index: </label>
                    <select name="index" id="index" onchange="this.form.submit()">
                        <option value="NIFTY50" {"selected" if index_name=="NIFTY50" else ""}>NIFTY50</option>
                        <option value="BANKNIFTY" {"selected" if index_name=="BANKNIFTY" else ""}>BANKNIFTY</option>
                        <option value="FINNIFTY" {"selected" if index_name=="FINNIFTY" else ""}>FINNIFTY</option>
                        <option value="MIDCAPNIFTY" {"selected" if index_name=="MIDCAPNIFTY" else ""}>MIDCAPNIFTY</option>
                        <option value="SENSEX" {"selected" if index_name=="SENSEX" else ""}>SENSEX</option>
                    </select>

                    <div class="interval-selector">
                        <label for="vol_interval">Volume Δ Interval:</label>
                        <select name="vol_interval" id="vol_interval" onchange="this.form.submit()">
                            <option value="1" {"selected" if vol_interval==1 else ""}>1 min</option>
                            <option value="2" {"selected" if vol_interval==2 else ""}>2 min</option>
                            <option value="5" {"selected" if vol_interval==5 else ""}>5 min</option>
                            <option value="10" {"selected" if vol_interval==10 else ""}>10 min</option>
                        </select>
                    </div>

                    <div class="interval-selector">
                        <label for="oi_interval">OI Δ Interval:</label>
                        <select name="oi_interval" id="oi_interval" onchange="this.form.submit()">
                            <option value="1" {"selected" if oi_interval==1 else ""}>1 min</option>
                            <option value="2" {"selected" if oi_interval==2 else ""}>2 min</option>
                            <option value="5" {"selected" if oi_interval==5 else ""}>5 min</option>
                            <option value="10" {"selected" if oi_interval==10 else ""}>10 min</option>
                        </select>
                    </div>

                    <button type="button" class="btn btn-clear" onclick="clearAllPositions()">Clear All Positions</button>
                </form>
            </div>

            <div class="data-controls">
                <button class="btn btn-save" onclick="savePositions()">💾 Save Positions</button>
                <button class="btn btn-load" onclick="loadTodayPositions()">📂 Load Today's Positions</button>
                <a href="/position_history" class="btn btn-history">📅 Position History</a>
            </div>

            <div class="strategy-section">
                <h3>🎯 Quick Strategy Builder</h3>
                <div class="strategy-buttons">
                    <button class="strategy-btn btn-iron-condor" onclick="addStrategy('iron_condor')">
                        Iron Condor<br><small>Sell CE & PE, Buy Far OTM</small>
                    </button>
                    <button class="strategy-btn btn-straddle" onclick="addStrategy('straddle')">
                        Straddle<br><small>Buy ATM CE & PE</small>
                    </button>
                    <button class="strategy-btn btn-strangle" onclick="addStrategy('strangle')">
                        Strangle<br><small>Buy OTM CE & PE</small>
                    </button>
                    <button class="strategy-btn btn-butterfly" onclick="addStrategy('butterfly')">
                        Butterfly<br><small>Buy 1 ITM, Sell 2 ATM, Buy 1 OTM</small>
                    </button>
                    <button class="strategy-btn btn-bull-call" onclick="addStrategy('bull_call')">
                        Bull Call Spread<br><small>Buy ITM CE, Sell OTM CE</small>
                    </button>
                    <button class="strategy-btn btn-bear-put" onclick="addStrategy('bear_put')">
                        Bear Put Spread<br><small>Buy ITM PE, Sell OTM PE</small>
                    </button>
                    <button class="strategy-btn btn-calendar" onclick="addStrategy('calendar')">
                        Calendar Spread<br><small>Buy Far, Sell Near</small>
                    </button>
                    <button class="strategy-btn btn-ratio" onclick="addStrategy('ratio')">
                        Ratio Spread<br><small>Buy 1, Sell 2 OTM</small>
                    </button>
                </div>
            </div>

            <div class="stats" id="stats-section">
                <div class="stat-card">
                    <div class="stat-label">Active Positions</div>
                    <div class="stat-value" id="active-count">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total P&L</div>
                    <div class="stat-value" id="total-pnl">₹0.00</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Spot Price</div>
                    <div class="stat-value" id="spot-price">-</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Active Strategies</div>
                    <div class="stat-value" id="strategy-count">0</div>
                </div>
            </div>

            <div class="gamma-options">
                <h3>🚀 Best Gamma Blast Options (High Volatility Potential)</h3>
                <table class="gamma-options-table">
                    <thead>
                        <tr>
                            <th>Type</th>
                            <th>Strike</th>
                            <th>LTP</th>
                            <th>Volume (Cr)</th>
                            <th>Vol Δ</th>
                            <th>OI (Cr)</th>
                            <th>OI Δ</th>
                            <th>Gamma Score</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="gamma-options-body">
                        <tr><td colspan="9">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <div class="best-options">
                <h3>💰 Best ATM/ITM Options (Lower Risk, Higher Delta)</h3>
                <table class="best-options-table">
                    <thead>
                        <tr>
                            <th>Type</th>
                            <th>Strike</th>
                            <th>LTP</th>
                            <th>Fair Value</th>
                            <th>Discount</th>
                            <th>Profit Prob</th>
                            <th>Risk/Reward</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="best-options-body">
                        <tr><td colspan="8">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <div class="positions-section">
                <h3>📊 Active Positions (Qty: 75)</h3>
                <table class="positions-table">
                    <thead>
                        <tr>
                            <th>Strike</th>
                            <th>Type</th>
                            <th>Entry LTP</th>
                            <th>Current LTP</th>
                            <th>P&L per Lot</th>
                            <th>Entry Time (IST)</th>
                            <th>Strategy</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="positions-body">
                        <tr><td colspan="8">No active positions. Add from opportunities below.</td></tr>
                    </tbody>
                </table>
            </div>

            <div class="opportunities">
                <h3>🎯 Scalping Opportunities (ATM ±2 strikes)</h3>
                <table class="opp-table">
                    <thead>
                        <tr>
                            <th>Strike</th>
                            <th>Type</th>
                            <th>LTP</th>
                            <th>Volume (Cr)</th>
                            <th>Vol Δ ({vol_interval}m)</th>
                            <th>OI (Cr)</th>
                            <th>OI Δ ({oi_interval}m)</th>
                            <th>OI Change %</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="opportunities-body">
                        <tr><td colspan="9">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            const indexName = "{index_name}";
            const volInterval = {vol_interval};
            const oiInterval = {oi_interval};
            const LOT_SIZE = 75;

            function addPosition(strike, type, ltp) {{
                fetch(`/add_position?index=${{indexName}}&strike=${{strike}}&type=${{type}}&ltp=${{ltp}}`, {{
                    method: 'POST'
                }}).then(() => refreshData());
            }}

            function addStrategy(strategy) {{
                fetch(`/add_strategy?index=${{indexName}}&strategy=${{strategy}}`, {{
                    method: 'POST'
                }}).then(response => response.json()).then(data => {{
                    if (data.status === 'success') {{
                        alert(`${{strategy.toUpperCase()}} strategy added successfully!`);
                        refreshData();
                    }} else {{
                        alert(`Error: ${{data.message}}`);
                    }}
                }}).catch(err => {{
                    console.error('Error adding strategy:', err);
                    alert('Error adding strategy');
                }});
            }}

            function exitPosition(posId) {{
                fetch(`/exit_position?index=${{indexName}}&id=${{posId}}`, {{
                    method: 'POST'
                }}).then(() => refreshData());
            }}

            function clearAllPositions() {{
                if (confirm('Clear all positions for ' + indexName + '?')) {{
                    fetch(`/clear_positions?index=${{indexName}}`, {{
                        method: 'POST'
                    }}).then(() => refreshData());
                }}
            }}

            function savePositions() {{
                fetch('/save_positions', {{
                    method: 'POST'
                }}).then(response => response.json()).then(data => {{
                    if (data.status === 'success') {{
                        alert('Positions saved successfully!');
                    }} else {{
                        alert('Error: ' + data.message);
                    }}
                }}).catch(err => {{
                    console.error('Error saving positions:', err);
                    alert('Error saving positions');
                }});
            }}

            function loadTodayPositions() {{
                fetch('/load_positions', {{
                    method: 'POST'
                }}).then(response => response.json()).then(data => {{
                    if (data.status === 'success') {{
                        alert(data.message);
                        refreshData();
                    }} else {{
                        alert('Error: ' + data.message);
                    }}
                }}).catch(err => {{
                    console.error('Error loading positions:', err);
                    alert('Error loading positions');
                }});
            }}

            async function refreshData() {{
                try {{
                    const resp = await fetch(`/scalping_data?index=${{indexName}}&vol_interval=${{volInterval}}&oi_interval=${{oiInterval}}`);
                    const data = await resp.json();

                    if (data.error === 'token_expired') {{
                        window.location.href = '/fyers_setup';
                        return;
                    }}

                    document.getElementById('positions-body').innerHTML = data.positions;
                    document.getElementById('opportunities-body').innerHTML = data.opportunities;
                    document.getElementById('best-options-body').innerHTML = data.best_options;
                    document.getElementById('gamma-options-body').innerHTML = data.gamma_options;
                    document.getElementById('active-count').innerText = data.active_count;
                    document.getElementById('total-pnl').innerText = data.total_pnl;
                    document.getElementById('total-pnl').className = 'stat-value ' + (data.total_pnl_num >= 0 ? 'profit' : 'loss');
                    document.getElementById('spot-price').innerText = data.spot_price;
                    document.getElementById('strategy-count').innerText = data.strategy_count;
                }} catch (err) {{
                    console.error("Error refreshing data:", err);
                }}
            }}

            setInterval(refreshData, 1000);
            refreshData();
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route("/add_strategy", methods=["POST"])
def add_strategy():
    if not is_logged_in():
        return json.dumps({"status": "error", "message": "Please login first"})

    if not validate_fyers_token():
        return json.dumps({"status": "error", "message": "Fyers token expired. Please contact administrator."})

    index_name = request.args.get("index", "NIFTY50")
    strategy = request.args.get("strategy")
    username = session.get('username')

    try:
        # Get current option chain data
        symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")
        data = {"symbol": symbol, "strikecount": 50}
        response = fyers.optionchain(data=data)
        data_section = response.get("data", {}) if isinstance(response, dict) else {}
        options_data = data_section.get("optionsChain") or data_section.get("options_chain") or []

        if not options_data:
            return json.dumps({"status": "error", "message": "No option chain data available"})

        df = pd.json_normalize(options_data)
        if "strike_price" not in df.columns:
            possible_strike_cols = [c for c in df.columns if "strike" in c.lower()]
            if possible_strike_cols:
                df = df.rename(columns={possible_strike_cols[0]: "strike_price"})

        num_cols = ["strike_price", "ltp", "oi", "oich", "oichp", "volume"]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Get spot price
        spot_price = None
        for key in ("underlying_value", "underlyingValue", "underlying", "underlying_value_instrument"):
            if data_section.get(key) is not None:
                try:
                    spot_price = float(data_section.get(key))
                    break
                except Exception:
                    pass

        if spot_price is None:
            strikes_all = sorted(df["strike_price"].dropna().unique())
            spot_price = float(strikes_all[len(strikes_all)//2]) if strikes_all else 0

        # Find ATM strike
        strikes_all = sorted(df["strike_price"].dropna().unique())
        atm_strike = min(strikes_all, key=lambda s: abs(s - spot_price)) if strikes_all else 0
        atm_index = strikes_all.index(atm_strike) if atm_strike in strikes_all else 0

        # Initialize user data if not exists
        if username not in user_scalping_positions:
            user_scalping_positions[username] = {}
        
        today = date.today().strftime("%Y-%m-%d")
        if today not in user_scalping_positions[username]:
            user_scalping_positions[username][today] = []

        mumbai_time = get_mumbai_time()
        positions_to_add = []

        if strategy == "iron_condor":
            # Iron Condor: Sell ATM CE & PE, Buy Far OTM CE & PE
            # Find strikes
            ce_sell_strike = strikes_all[min(atm_index + 2, len(strikes_all)-1)]
            pe_sell_strike = strikes_all[max(atm_index - 2, 0)]
            ce_buy_strike = strikes_all[min(atm_index + 5, len(strikes_all)-1)]
            pe_buy_strike = strikes_all[max(atm_index - 5, 0)]

            # Get LTPs
            ce_sell_ltp = df[(df["strike_price"] == ce_sell_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == ce_sell_strike) & (df["option_type"] == "CE")].empty else 0
            pe_sell_ltp = df[(df["strike_price"] == pe_sell_strike) & (df["option_type"] == "PE")]["ltp"].values[0] if not df[(df["strike_price"] == pe_sell_strike) & (df["option_type"] == "PE")].empty else 0
            ce_buy_ltp = df[(df["strike_price"] == ce_buy_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == ce_buy_strike) & (df["option_type"] == "CE")].empty else 0
            pe_buy_ltp = df[(df["strike_price"] == pe_buy_strike) & (df["option_type"] == "PE")]["ltp"].values[0] if not df[(df["strike_price"] == pe_buy_strike) & (df["option_type"] == "PE")].empty else 0

            positions_to_add = [
                {"strike": ce_sell_strike, "type": "CE", "ltp": ce_sell_ltp, "action": "sell"},
                {"strike": pe_sell_strike, "type": "PE", "ltp": pe_sell_ltp, "action": "sell"},
                {"strike": ce_buy_strike, "type": "CE", "ltp": ce_buy_ltp, "action": "buy"},
                {"strike": pe_buy_strike, "type": "PE", "ltp": pe_buy_ltp, "action": "buy"}
            ]

        elif strategy == "straddle":
            # Straddle: Buy ATM CE & PE
            ce_ltp = df[(df["strike_price"] == atm_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == atm_strike) & (df["option_type"] == "CE")].empty else 0
            pe_ltp = df[(df["strike_price"] == atm_strike) & (df["option_type"] == "PE")]["ltp"].values[0] if not df[(df["strike_price"] == atm_strike) & (df["option_type"] == "PE")].empty else 0

            positions_to_add = [
                {"strike": atm_strike, "type": "CE", "ltp": ce_ltp, "action": "buy"},
                {"strike": atm_strike, "type": "PE", "ltp": pe_ltp, "action": "buy"}
            ]

        elif strategy == "strangle":
            # Strangle: Buy OTM CE & PE
            ce_strike = strikes_all[min(atm_index + 3, len(strikes_all)-1)]
            pe_strike = strikes_all[max(atm_index - 3, 0)]

            ce_ltp = df[(df["strike_price"] == ce_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == ce_strike) & (df["option_type"] == "CE")].empty else 0
            pe_ltp = df[(df["strike_price"] == pe_strike) & (df["option_type"] == "PE")]["ltp"].values[0] if not df[(df["strike_price"] == pe_strike) & (df["option_type"] == "PE")].empty else 0

            positions_to_add = [
                {"strike": ce_strike, "type": "CE", "ltp": ce_ltp, "action": "buy"},
                {"strike": pe_strike, "type": "PE", "ltp": pe_ltp, "action": "buy"}
            ]

        elif strategy == "butterfly":
            # Butterfly: Buy 1 ITM, Sell 2 ATM, Buy 1 OTM (for calls)
            it_strike = strikes_all[max(atm_index - 2, 0)]
            otm_strike = strikes_all[min(atm_index + 2, len(strikes_all)-1)]

            it_ltp = df[(df["strike_price"] == it_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == it_strike) & (df["option_type"] == "CE")].empty else 0
            atm_ltp = df[(df["strike_price"] == atm_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == atm_strike) & (df["option_type"] == "CE")].empty else 0
            otm_ltp = df[(df["strike_price"] == otm_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == otm_strike) & (df["option_type"] == "CE")].empty else 0

            positions_to_add = [
                {"strike": it_strike, "type": "CE", "ltp": it_ltp, "action": "buy"},
                {"strike": atm_strike, "type": "CE", "ltp": atm_ltp, "action": "sell"},
                {"strike": atm_strike, "type": "CE", "ltp": atm_ltp, "action": "sell"},
                {"strike": otm_strike, "type": "CE", "ltp": otm_ltp, "action": "buy"}
            ]

        elif strategy == "bull_call":
            # Bull Call Spread: Buy ITM CE, Sell OTM CE
            it_strike = strikes_all[max(atm_index - 2, 0)]
            otm_strike = strikes_all[min(atm_index + 2, len(strikes_all)-1)]

            it_ltp = df[(df["strike_price"] == it_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == it_strike) & (df["option_type"] == "CE")].empty else 0
            otm_ltp = df[(df["strike_price"] == otm_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == otm_strike) & (df["option_type"] == "CE")].empty else 0

            positions_to_add = [
                {"strike": it_strike, "type": "CE", "ltp": it_ltp, "action": "buy"},
                {"strike": otm_strike, "type": "CE", "ltp": otm_ltp, "action": "sell"}
            ]

        elif strategy == "bear_put":
            # Bear Put Spread: Buy ITM PE, Sell OTM PE
            it_strike = strikes_all[min(atm_index + 2, len(strikes_all)-1)]
            otm_strike = strikes_all[max(atm_index - 2, 0)]

            it_ltp = df[(df["strike_price"] == it_strike) & (df["option_type"] == "PE")]["ltp"].values[0] if not df[(df["strike_price"] == it_strike) & (df["option_type"] == "PE")].empty else 0
            otm_ltp = df[(df["strike_price"] == otm_strike) & (df["option_type"] == "PE")]["ltp"].values[0] if not df[(df["strike_price"] == otm_strike) & (df["option_type"] == "PE")].empty else 0

            positions_to_add = [
                {"strike": it_strike, "type": "PE", "ltp": it_ltp, "action": "buy"},
                {"strike": otm_strike, "type": "PE", "ltp": otm_ltp, "action": "sell"}
            ]

        elif strategy == "calendar":
            # Calendar Spread: Simplified - Buy Far OTM, Sell Near OTM
            near_strike = strikes_all[min(atm_index + 2, len(strikes_all)-1)]
            far_strike = strikes_all[min(atm_index + 4, len(strikes_all)-1)]

            near_ltp = df[(df["strike_price"] == near_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == near_strike) & (df["option_type"] == "CE")].empty else 0
            far_ltp = df[(df["strike_price"] == far_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == far_strike) & (df["option_type"] == "CE")].empty else 0

            positions_to_add = [
                {"strike": near_strike, "type": "CE", "ltp": near_ltp, "action": "sell"},
                {"strike": far_strike, "type": "CE", "ltp": far_ltp, "action": "buy"}
            ]

        elif strategy == "ratio":
            # Ratio Spread: Buy 1 ATM, Sell 2 OTM
            otm_strike = strikes_all[min(atm_index + 3, len(strikes_all)-1)]

            atm_ltp = df[(df["strike_price"] == atm_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == atm_strike) & (df["option_type"] == "CE")].empty else 0
            otm_ltp = df[(df["strike_price"] == otm_strike) & (df["option_type"] == "CE")]["ltp"].values[0] if not df[(df["strike_price"] == otm_strike) & (df["option_type"] == "CE")].empty else 0

            positions_to_add = [
                {"strike": atm_strike, "type": "CE", "ltp": atm_ltp, "action": "buy"},
                {"strike": otm_strike, "type": "CE", "ltp": otm_ltp, "action": "sell"},
                {"strike": otm_strike, "type": "CE", "ltp": otm_ltp, "action": "sell"}
            ]

        # Add all positions
        for pos in positions_to_add:
            pos_id = f"{pos['strike']}_{pos['type']}_{strategy}_{mumbai_time.timestamp()}"
            position = {
                "id": pos_id,
                "strike": pos["strike"],
                "type": pos["type"],
                "entry_ltp": pos["ltp"],
                "entry_time": mumbai_time.strftime("%H:%M:%S"),
                "lot_size": 75,
                "strategy": strategy.upper(),
                "action": pos["action"],
                "user": username
            }
            user_scalping_positions[username][today].append(position)

        return json.dumps({"status": "success", "message": f"{strategy.upper()} strategy added with {len(positions_to_add)} legs"})

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@app.route("/add_position", methods=["POST"])
def add_position():
    if not is_logged_in():
        return json.dumps({"status": "error", "message": "Please login first"})

    index_name = request.args.get("index", "NIFTY50")
    strike = float(request.args.get("strike"))
    option_type = request.args.get("type")
    ltp = float(request.args.get("ltp"))
    username = session.get('username')

    # Initialize user data if not exists
    if username not in user_scalping_positions:
        user_scalping_positions[username] = {}
    
    today = date.today().strftime("%Y-%m-%d")
    if today not in user_scalping_positions[username]:
        user_scalping_positions[username][today] = []

    # Use Mumbai time instead of local time
    mumbai_time = get_mumbai_time()
    pos_id = f"{strike}_{option_type}_{mumbai_time.timestamp()}"
    position = {
        "id": pos_id,
        "strike": strike,
        "type": option_type,
        "entry_ltp": ltp,
        "entry_time": mumbai_time.strftime("%H:%M:%S"),
        "lot_size": 75,
        "strategy": "MANUAL",
        "user": username
    }
    user_scalping_positions[username][today].append(position)

    return json.dumps({"status": "success"})

@app.route("/exit_position", methods=["POST"])
def exit_position():
    if not is_logged_in():
        return json.dumps({"status": "error", "message": "Please login first"})

    index_name = request.args.get("index", "NIFTY50")
    pos_id = request.args.get("id")
    username = session.get('username')

    if username in user_scalping_positions:
        today = date.today().strftime("%Y-%m-%d")
        if today in user_scalping_positions[username]:
            # Only allow users to exit their own positions or admin to exit any
            is_admin_user = is_admin()

            user_scalping_positions[username][today] = [
                p for p in user_scalping_positions[username][today]
                if p["id"] != pos_id or (p.get("user") != username and not is_admin_user)
            ]

    return json.dumps({"status": "success"})

@app.route("/clear_positions", methods=["POST"])
def clear_positions():
    if not is_logged_in():
        return json.dumps({"status": "error", "message": "Please login first"})

    index_name = request.args.get("index", "NIFTY50")
    username = session.get('username')

    # Only allow users to clear their own positions or admin to clear all
    is_admin_user = is_admin()

    if is_admin_user:
        # Admin can clear all positions for all users
        for user in user_scalping_positions:
            today = date.today().strftime("%Y-%m-%d")
            if today in user_scalping_positions[user]:
                user_scalping_positions[user][today] = []
    else:
        # Regular users can only clear their own positions
        if username in user_scalping_positions:
            today = date.today().strftime("%Y-%m-%d")
            if today in user_scalping_positions[username]:
                user_scalping_positions[username][today] = []

    return json.dumps({"status": "success"})

@app.route("/scalping_data")
def scalping_data():
    if not is_logged_in():
        return json.dumps({"error": "not_logged_in", "positions": "", "opportunities": "", "best_options": "", "gamma_options": "", "active_count": 0, "total_pnl": "₹0.00", "total_pnl_num": 0, "spot_price": "-", "strategy_count": 0})

    if not validate_fyers_token():
        return json.dumps({"error": "token_expired", "positions": "", "opportunities": "", "best_options": "", "gamma_options": "", "active_count": 0, "total_pnl": "₹0.00", "total_pnl_num": 0, "spot_price": "-", "strategy_count": 0})

    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))
    symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")
    username = session.get('username')

    try:
        data = {"symbol": symbol, "strikecount": 50}
        response = fyers.optionchain(data=data)
        data_section = response.get("data", {}) if isinstance(response, dict) else {}
        options_data = data_section.get("optionsChain") or data_section.get("options_chain") or []

        if not options_data:
            return json.dumps({"positions": "", "opportunities": "", "best_options": "", "gamma_options": "", "active_count": 0, "total_pnl": "₹0.00", "total_pnl_num": 0, "spot_price": "-", "strategy_count": 0})

        df = pd.json_normalize(options_data)
        if "strike_price" not in df.columns:
            possible_strike_cols = [c for c in df.columns if "strike" in c.lower()]
            if possible_strike_cols:
                df = df.rename(columns={possible_strike_cols[0]: "strike_price"})

        num_cols = ["strike_price", "ltp", "oi", "oich", "oichp", "volume"]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        spot_price = None
        for key in ("underlying_value", "underlyingValue", "underlying", "underlying_value_instrument"):
            if data_section.get(key) is not None:
                try:
                    spot_price = float(data_section.get(key))
                    break
                except Exception:
                    pass

        strikes_all = sorted(df["strike_price"].dropna().unique())
        if spot_price is None:
            spot_price = float(strikes_all[len(strikes_all)//2]) if strikes_all else 0

        atm_strike = min(strikes_all, key=lambda s: abs(s - spot_price)) if strikes_all else 0
        atm_index = strikes_all.index(atm_strike) if atm_strike in strikes_all else 0
        low = max(0, atm_index - 2)
        high = min(len(strikes_all), atm_index + 3)
        strikes_to_show = strikes_all[low:high] if strikes_all else []

        # Generate positions HTML
        positions_html = ""
        total_pnl = 0

        # Get user's positions for today
        today = date.today().strftime("%Y-%m-%d")
        active_positions = []
        
        if username in user_scalping_positions and today in user_scalping_positions[username]:
            active_positions = user_scalping_positions[username][today]

        strategies = set()

        for pos in active_positions:
            strike = pos["strike"]
            option_type = pos["type"]
            entry_ltp = pos["entry_ltp"]
            strategy = pos.get("strategy", "MANUAL")
            action = pos.get("action", "buy")
            strategies.add(strategy)

            current_row = df[(df["strike_price"] == strike) & (df["option_type"] == option_type)]
            current_ltp = current_row["ltp"].values[0] if not current_row.empty and "ltp" in current_row.columns else entry_ltp

            # Calculate P&L based on action
            if action == "sell":
                pnl = (entry_ltp - current_ltp) * pos["lot_size"]
            else:
                pnl = (current_ltp - entry_ltp) * pos["lot_size"]

            total_pnl += pnl

            pnl_class = "profit" if pnl >= 0 else "loss"
            pnl_symbol = "+" if pnl >= 0 else ""

            # Strategy badge color
            strategy_colors = {
                "IRON_CONDOR": "#667eea",
                "STRADDLE": "#f093fb",
                "STRANGLE": "#4facfe",
                "BUTTERFLY": "#43e97b",
                "BULL_CALL": "#fa709a",
                "BEAR_PUT": "#30cfd0",
                "CALENDAR": "#a8edea",
                "RATIO": "#ff9a9e",
                "MANUAL": "#666"
            }
            strategy_color = strategy_colors.get(strategy, "#666")

            positions_html += f"""
            <tr>
                <td><b>{strike}</b></td>
                <td>{option_type}</td>
                <td>₹{entry_ltp:.2f}</td>
                <td>₹{current_ltp:.2f}</td>
                <td class="{pnl_class}">{pnl_symbol}₹{pnl:.2f}</td>
                <td>{pos['entry_time']}</td>
                <td><span class="strategy-badge" style="background-color: {strategy_color}; color: white;">{strategy}</span></td>
                <td><button class="btn btn-exit" onclick="exitPosition('{pos['id']}')">Exit</button></td>
            </tr>
            """

        if not positions_html:
            positions_html = "<tr><td colspan='8'>No active positions. Add from opportunities below.</td></tr>"

        # Generate opportunities HTML with change tracking
        opportunities_html = ""
        opp_df = df[df["strike_price"].isin(strikes_to_show)]

        # Track highest values
        highest_volume = {"value": 0, "strike": None, "type": None}
        highest_vol_change = {"value": 0, "strike": None, "type": None}
        highest_oi = {"value": 0, "strike": None, "type": None}
        highest_oi_change = {"value": 0, "strike": None, "type": None}

        # First pass to collect all data and find highest values
        temp_data = []
        for _, row in opp_df.iterrows():
            strike = row.get("strike_price", 0)
            option_type = row.get("option_type", "")
            ltp = row.get("ltp", 0)
            volume = row.get("volume", 0)
            oi = row.get("oi", 0)
            oichp = row.get("oichp", 0)

            # Update historical data
            update_historical_data(index_name, strike, option_type, volume, oi)

            # Get volume and OI changes
            vol_change, _ = get_change_data(index_name, strike, option_type, vol_interval)
            _, oi_change = get_change_data(index_name, strike, option_type, oi_interval)

            # Store temp data
            temp_data.append({
                "strike": strike,
                "option_type": option_type,
                "ltp": ltp,
                "volume": volume,
                "vol_change": vol_change,
                "oi": oi,
                "oi_change": oi_change,
                "oichp": oichp
            })

            # Track highest values
            if volume > highest_volume["value"]:
                highest_volume = {"value": volume, "strike": strike, "type": option_type}

            if vol_change is not None and vol_change > highest_vol_change["value"]:
                highest_vol_change = {"value": vol_change, "strike": strike, "type": option_type}

            if oi > highest_oi["value"]:
                highest_oi = {"value": oi, "strike": strike, "type": option_type}

            if oi_change is not None and oi_change > highest_oi_change["value"]:
                highest_oi_change = {"value": oi_change, "strike": strike, "type": option_type}

        # Second pass to generate HTML with highlighting
        for data in temp_data:
            strike = data["strike"]
            option_type = data["option_type"]
            ltp = data["ltp"]
            volume = data["volume"]
            vol_change = data["vol_change"]
            oi = data["oi"]
            oi_change = data["oi_change"]
            oichp = data["oichp"]

            # Format values in crore
            volume_cr = format_to_crore(volume)
            oi_cr = format_to_crore(oi)

            # Format changes
            vol_change_str = f"{vol_change:+,.0f}" if vol_change is not None else "N/A"
            vol_change_class = "profit" if (vol_change or 0) > 0 else ("loss" if (vol_change or 0) < 0 else "neutral")

            # Check if this is the highest volume
            if strike == highest_volume["strike"] and option_type == highest_volume["type"]:
                volume_class = "highest-volume"
            else:
                volume_class = ""

            # Check if this is the highest volume change
            if strike == highest_vol_change["strike"] and option_type == highest_vol_change["type"]:
                vol_change_class = "highest-vol-change"
            elif (vol_change or 0) > 0:
                vol_change_class = "profit"
            elif (vol_change or 0) < 0:
                vol_change_class = "loss"
            else:
                vol_change_class = "neutral"

            oi_change_str = f"{oi_change:+,.0f}" if oi_change is not None else "N/A"

            # Check if this is the highest OI
            if strike == highest_oi["strike"] and option_type == highest_oi["type"]:
                oi_class = "highest-oi"
            else:
                oi_class = ""

            # Check if this is the highest OI change
            if strike == highest_oi_change["strike"] and option_type == highest_oi_change["type"]:
                oi_change_class = "highest-oi-change"
            elif (oi_change or 0) > 0:
                oi_change_class = "profit"
            elif (oi_change or 0) < 0:
                oi_change_class = "loss"
            else:
                oi_change_class = "neutral"

            opportunities_html += f"""
            <tr>
                <td><b>{strike}</b></td>
                <td>{option_type}</td>
                <td>₹{ltp:.2f}</td>
                <td class="{volume_class}">{volume_cr}</td>
                <td class="{vol_change_class}">{vol_change_str}</td>
                <td class="{oi_class}">{oi_cr}</td>
                <td class="{oi_change_class}">{oi_change_str}</td>
                <td>{oichp:.2f}%</td>
                <td>
                    <button class="btn btn-buy" onclick="addPosition({strike}, '{option_type}', {ltp})">Add Position</button>
                </td>
            </tr>
            """

        # Generate best options HTML
        best_options_html = ""
        try:
            # Get best PE options
            best_pe_options = get_best_options(df, spot_price, "PE", 5)
            
            # Get best CE options
            best_ce_options = get_best_options(df, spot_price, "CE", 5)
            
            # Combine and sort by discount
            best_options = pd.concat([best_pe_options, best_ce_options]).sort_values("discount", ascending=False)
            
            for _, row in best_options.iterrows():
                strike = row["strike_price"]
                option_type = row["option_type"]
                ltp = row["ltp"]
                fair_value = row["fair_value"]
                discount = row["discount"]
                profit_prob = row["profit_probability"] * 100  # Convert to percentage
                risk_reward = row["risk_reward"]
                
                # Format values
                ltp_str = f"₹{ltp:.2f}"
                fair_value_str = f"₹{fair_value:.2f}"
                discount_str = f"{discount:.1f}%"
                profit_prob_str = f"{profit_prob:.1f}%"
                risk_reward_str = f"{risk_reward:.2f}"
                
                # Determine CSS classes based on values
                discount_class = "discount-positive" if discount > 0 else "discount-negative"
                
                if profit_prob > 60:
                    prob_class = "probability-high"
                elif profit_prob > 40:
                    prob_class = "probability-medium"
                else:
                    prob_class = "probability-low"
                
                if risk_reward > 2:
                    rr_class = "risk-reward-high"
                elif risk_reward > 1:
                    rr_class = "risk-reward-medium"
                else:
                    rr_class = "risk-reward-low"
                
                best_options_html += f"""
                <tr>
                    <td>{option_type}</td>
                    <td>{strike}</td>
                    <td>{ltp_str}</td>
                    <td>{fair_value_str}</td>
                    <td class="{discount_class}">{discount_str}</td>
                    <td class="{prob_class}">{profit_prob_str}</td>
                    <td class="{rr_class}">{risk_reward_str}</td>
                    <td>
                        <button class="btn btn-buy" onclick="addPosition({strike}, '{option_type}', {ltp})">Buy</button>
                    </td>
                </tr>
                """
            
            if not best_options_html:
                best_options_html = "<tr><td colspan='8'>No suitable options found</td></tr>"
                
        except Exception as e:
            best_options_html = f"<tr><td colspan='8'>Error: {str(e)}</td></tr>"

        # Generate gamma options HTML
        gamma_options_html = ""
        try:
            # Get all options with volume and OI changes
            df_with_changes = df.copy()
            
            # Add volume and OI changes to dataframe
            for _, row in df_with_changes.iterrows():
                strike = row["strike_price"]
                option_type = row["option_type"]
                volume = row.get("volume", 0)
                oi = row.get("oi", 0)
                
                # Update historical data
                update_historical_data(index_name, strike, option_type, volume, oi)
                
                # Get volume and OI changes
                vol_change, oi_change = get_change_data(index_name, strike, option_type, vol_interval)
                
                df_with_changes.loc[_, "vol_change"] = vol_change
                df_with_changes.loc[_, "oi_change"] = oi_change
            
            # Get best gamma options
            best_gamma_options = get_best_gamma_options(df_with_changes, spot_price, 5)
            
            for _, row in best_gamma_options.iterrows():
                strike = row["strike_price"]
                option_type = row["option_type"]
                ltp = row["ltp"]
                volume = row.get("volume", 0)
                vol_change = row.get("vol_change", 0)
                oi = row.get("oi", 0)
                oi_change = row.get("oi_change", 0)
                gamma_score = row.get("gamma_score", 0)
                
                # Format values
                ltp_str = f"₹{ltp:.2f}"
                volume_str = format_to_crore(volume)
                oi_str = format_to_crore(oi)
                vol_change_str = f"{vol_change:+,.0f}" if vol_change is not None else "N/A"
                oi_change_str = f"{oi_change:+,.0f}" if oi_change is not None else "N/A"
                
                # Determine CSS class for gamma score
                if gamma_score > 60:
                    gamma_class = "gamma-score-high"
                elif gamma_score > 40:
                    gamma_class = "gamma-score-medium"
                else:
                    gamma_class = "gamma-score-low"
                
                gamma_options_html += f"""
                <tr>
                    <td>{option_type}</td>
                    <td>{strike}</td>
                    <td>{ltp_str}</td>
                    <td>{volume_str}</td>
                    <td>{vol_change_str}</td>
                    <td>{oi_str}</td>
                    <td>{oi_change_str}</td>
                    <td class="{gamma_class}">{gamma_score:.1f}</td>
                    <td>
                        <button class="btn btn-buy" onclick="addPosition({strike}, '{option_type}', {ltp})">Buy</button>
                    </td>
                </tr>
                """
            
            if not gamma_options_html:
                gamma_options_html = "<tr><td colspan='9'>No gamma options found</td></tr>"
                
        except Exception as e:
            gamma_options_html = f"<tr><td colspan='9'>Error: {str(e)}</td></tr>"

        total_pnl_str = f"₹{total_pnl:,.2f}" if total_pnl >= 0 else f"-₹{abs(total_pnl):,.2f}"

        return json.dumps({
            "positions": positions_html,
            "opportunities": opportunities_html,
            "best_options": best_options_html,
            "gamma_options": gamma_options_html,
            "active_count": len(active_positions),
            "total_pnl": total_pnl_str,
            "total_pnl_num": total_pnl,
            "spot_price": f"₹{spot_price:,.2f}",
            "strategy_count": len(strategies)
        })

    except Exception as e:
        return json.dumps({
            "positions": f"<tr><td colspan='8'>Error: {str(e)}</td></tr>",
            "opportunities": f"<tr><td colspan='9'>Error: {str(e)}</td></tr>",
            "best_options": f"<tr><td colspan='8'>Error: {str(e)}</td></tr>",
            "gamma_options": f"<tr><td colspan='9'>Error: {str(e)}</td></tr>",
            "active_count": 0,
            "total_pnl": "₹0.00",
            "total_pnl_num": 0,
            "spot_price": "-",
            "strategy_count": 0
        })

@app.route("/chain")
def fetch_option_chain():
    if not is_logged_in():
        return redirect("/login")

    if not validate_fyers_token():
        if is_admin():
            flash("Fyers token expired. Please re-authenticate.", "warning")
            return redirect("/fyers_setup")
        else:
            flash("Fyers API is not configured. Please contact the administrator.", "error")
            return redirect("/")

    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))
    symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")

    # Get user info for display
    user_name = session.get('name', session.get('username'))
    user_mobile = users.get(session.get('username'), {}).get('mobile', 'Not provided') if is_admin() else None

    try:
        table_html, spot_price, analysis_html, ce_headers, pe_headers = generate_full_table(index_name, symbol, vol_interval, oi_interval)
    except Exception as e:
        table_html = f"<p>Error fetching option chain: {str(e)}</p>"
        spot_price = ""
        analysis_html = ""

    html = f"""
    <!doctype html>
    <html>
    <head>
        <title>{index_name} Option Chain (ATM ±3)</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 16px; }}
            h2 {{ text-align:center; color:#1a73e8; }}
            table {{ width:100%; border-collapse: collapse; font-size:12px; }}
            th, td {{ border:1px solid #ddd; padding:6px; text-align:center; }}
            th {{ background:#1a73e8; color:#fff; }}
            tr:nth-child(even) {{ background:#f7f7f7; }}
            .dropdown {{ margin:12px 0; text-align:center; }}
            #analysis {{ background:#eef; padding:10px; border-radius:5px; margin-top:15px; }}
            .profit {{ color: #0f9d58; font-weight: bold; }}
            .loss {{ color: #db4437; font-weight: bold; }}
            .neutral {{ color: #666; }}
            .interval-selector {{ display: inline-block; margin: 0 10px; }}
            .interval-selector label {{ font-weight: bold; margin-right: 5px; }}
            .interval-selector select {{ padding: 5px; border-radius: 4px; }}
            .user-info {{ float: right; padding: 10px; background: #e3f2fd; border-radius: 4px; }}
            .logout {{ float: right; margin-left: 10px; }}
        </style>
    </head>
    <body>
        <div class="user-info">
            <div>Welcome, {user_name}</div>
            {'<div>Mobile: ' + user_mobile + '</div>' if user_mobile else ''}
            <a href="/logout" class="logout">Logout</a>
        </div>

        <h2 id="spot-title">{index_name} Option Chain (ATM ±3) — Spot: {spot_price}</h2>

        <div class="dropdown">
            <form method="get" action="/chain">
                <label for="index">Select Index: </label>
                <select name="index" id="index" onchange="this.form.submit()">
                    <option value="NIFTY50" {"selected" if index_name=="NIFTY50" else ""}>NIFTY50</option>
                    <option value="BANKNIFTY" {"selected" if index_name=="BANKNIFTY" else ""}>BANKNIFTY</option>
                    <option value="FINNIFTY" {"selected" if index_name=="FINNIFTY" else ""}>FINNIFTY</option>
                    <option value="MIDCAPNIFTY" {"selected" if index_name=="MIDCAPNIFTY" else ""}>MIDCAPNIFTY</option>
                    <option value="SENSEX" {"selected" if index_name=="SENSEX" else ""}>SENSEX</option>
                </select>

                <div class="interval-selector">
                    <label for="vol_interval">Volume Δ:</label>
                    <select name="vol_interval" id="vol_interval" onchange="this.form.submit()">
                        <option value="1" {"selected" if vol_interval==1 else ""}>1 min</option>
                        <option value="2" {"selected" if vol_interval==2 else ""}>2 min</option>
                        <option value="5" {"selected" if vol_interval==5 else ""}>5 min</option>
                        <option value="10" {"selected" if vol_interval==10 else ""}>10 min</option>
                    </select>
                </div>

                <div class="interval-selector">
                    <label for="oi_interval">OI Δ:</label>
                    <select name="oi_interval" id="oi_interval" onchange="this.form.submit()">
                        <option value="1" {"selected" if oi_interval==1 else ""}>1 min</option>
                        <option value="2" {"selected" if oi_interval==2 else ""}>2 min</option>
                        <option value="5" {"selected" if oi_interval==5 else ""}>5 min</option>
                        <option value="10" {"selected" if oi_interval==10 else ""}>10 min</option>
                    </select>
                </div>
            </form>
        </div>

        <table id="option-chain-table">
            <thead><tr>{ce_headers}<th>STRIKE</th>{pe_headers}</tr></thead>
            <tbody>{table_html}</tbody>
        </table>

        <div id="analysis">{analysis_html}</div>

        <script>
            const indexName = "{index_name}";
            const volInterval = {vol_interval};
            const oiInterval = {oi_interval};

            async function refreshTableRows() {{
                try {{
                    const resp = await fetch(`/chain_rows_diff?index=${{indexName}}&vol_interval=${{volInterval}}&oi_interval=${{oiInterval}}`);
                    const result = await resp.json();

                    if (result.error === 'token_expired') {{
                        window.location.href = '/fyers_setup';
                        return;
                    }}

                    if (result.rows) {{
                        document.querySelector("#option-chain-table tbody").innerHTML = result.rows;
                        document.querySelector("#spot-title").innerHTML = `${{indexName}} Option Chain (ATM ±3) — Spot: ${{result.spot}}`;
                        document.querySelector("#analysis").innerHTML = result.analysis;
                    }}
                }} catch (err) {{
                    console.error("Error refreshing rows:", err);
                }}
            }}
            setInterval(refreshTableRows, 1000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route("/chain_rows_diff")
def chain_rows_diff():
    global previous_data

    if not is_logged_in():
        return json.dumps({"error": "not_logged_in", "rows": "", "spot": "", "analysis": ""})

    if not validate_fyers_token():
        return json.dumps({"error": "token_expired", "rows": "", "spot": "", "analysis": ""})

    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))
    symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")

    rows_html, spot_price, analysis_html, _, _ = generate_rows(index_name, symbol, vol_interval, oi_interval)

    current_data = {"rows": rows_html, "analysis": analysis_html}

    diff_rows = ""
    if previous_data.get(index_name) != current_data["rows"]:
        diff_rows = current_data["rows"]
        previous_data[index_name] = current_data["rows"]

    return json.dumps({"rows": diff_rows, "spot": spot_price, "analysis": analysis_html})

def generate_full_table(index_name, symbol, vol_interval, oi_interval):
    rows_html, spot_price, analysis_html, ce_headers, pe_headers = generate_rows(index_name, symbol, vol_interval, oi_interval)
    return rows_html, spot_price, analysis_html, ce_headers, pe_headers

def generate_rows(index_name, symbol, vol_interval, oi_interval):
    global fyers
    data = {"symbol": symbol, "strikecount": 50}
    response = fyers.optionchain(data=data)
    data_section = response.get("data", {}) if isinstance(response, dict) else {}
    options_data = data_section.get("optionsChain") or data_section.get("options_chain") or []

    if not options_data:
        return "", "", "<p>No option chain data available.</p>", "", ""

    df = pd.json_normalize(options_data)
    if "strike_price" not in df.columns:
        possible_strike_cols = [c for c in df.columns if "strike" in c.lower()]
        if possible_strike_cols:
            df = df.rename(columns={possible_strike_cols[0]: "strike_price"})

    num_cols = ["strike_price", "ask", "bid", "ltp", "oi", "oich", "oichp", "prev_oi", "volume", "ltpch"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    spot_price = None
    for key in ("underlying_value", "underlyingValue", "underlying", "underlying_value_instrument"):
        if data_section.get(key) is not None:
            try:
                spot_price = float(data_section.get(key))
                break
            except Exception:
                pass

    strikes_all = sorted(df["strike_price"].dropna().unique())
    if spot_price is None:
        spot_price = float(strikes_all[len(strikes_all)//2]) if strikes_all else 0

    atm_strike = min(strikes_all, key=lambda s: abs(s - spot_price)) if strikes_all else 0
    atm_index = strikes_all.index(atm_strike) if atm_strike in strikes_all else 0
    low = max(0, atm_index - 3)
    high = min(len(strikes_all), atm_index + 4)
    strikes_to_show = strikes_all[low:high] if strikes_all else []

    df = df[df["strike_price"].isin(strikes_to_show)]
    ce_df = df[df["option_type"] == "CE"].set_index("strike_price", drop=False) if "option_type" in df.columns else pd.DataFrame()
    pe_df = df[df["option_type"] == "PE"].set_index("strike_price", drop=False) if "option_type" in df.columns else pd.DataFrame()

    lr_cols = [c for c in ["ask", "bid", "ltp", "ltpch", "volume", "vol_change", "oi", "oi_change", "oich", "oichp", "prev_oi"] if c in df.columns or c in ["vol_change", "oi_change"]]

    ce_itm_df = ce_df[ce_df["strike_price"] < spot_price] if not ce_df.empty else pd.DataFrame()
    pe_itm_df = pe_df[pe_df["strike_price"] > spot_price] if not pe_df.empty else pd.DataFrame()

    rows_html = ""
    for strike in strikes_to_show:
        ce_cells = ""
        pe_cells = ""

        for c in lr_cols:
            if c == "vol_change":
                # CE Volume Change
                if not ce_df.empty and strike in ce_df.index:
                    ce_volume = ce_df.loc[strike, "volume"] if "volume" in ce_df.columns else 0
                    update_historical_data(index_name, strike, "CE", ce_volume, ce_df.loc[strike, "oi"] if "oi" in ce_df.columns else 0)
                    vol_change, _ = get_change_data(index_name, strike, "CE", vol_interval)
                    if vol_change is not None:
                        vol_class = "profit" if vol_change > 0 else ("loss" if vol_change < 0 else "neutral")
                        ce_cells += f"<td class='{vol_class}'>{vol_change:+,.0f}</td>"
                    else:
                        ce_cells += "<td>-</td>"
                else:
                    ce_cells += "<td>-</td>"

                # PE Volume Change
                if not pe_df.empty and strike in pe_df.index:
                    pe_volume = pe_df.loc[strike, "volume"] if "volume" in pe_df.columns else 0
                    update_historical_data(index_name, strike, "PE", pe_volume, pe_df.loc[strike, "oi"] if "oi" in pe_df.columns else 0)
                    vol_change, _ = get_change_data(index_name, strike, "PE", vol_interval)
                    if vol_change is not None:
                        vol_class = "profit" if vol_change > 0 else ("loss" if vol_change < 0 else "neutral")
                        pe_cells += f"<td class='{vol_class}'>{vol_change:+,.0f}</td>"
                    else:
                        pe_cells += "<td>-</td>"
                else:
                    pe_cells += "<td>-</td>"

            elif c == "oi_change":
                # CE OI Change
                if not ce_df.empty and strike in ce_df.index:
                    _, oi_change = get_change_data(index_name, strike, "CE", oi_interval)
                    if oi_change is not None:
                        oi_class = "profit" if oi_change > 0 else ("loss" if oi_change < 0 else "neutral")
                        ce_cells += f"<td class='{oi_class}'>{oi_change:+,.0f}</td>"
                    else:
                        ce_cells += "<td>-</td>"
                else:
                    ce_cells += "<td>-</td>"

                # PE OI Change
                if not pe_df.empty and strike in pe_df.index:
                    _, oi_change = get_change_data(index_name, strike, "PE", oi_interval)
                    if oi_change is not None:
                        oi_class = "profit" if oi_change > 0 else ("loss" if oi_change < 0 else "neutral")
                        pe_cells += f"<td class='{oi_class}'>{oi_change:+,.0f}</td>"
                    else:
                        pe_cells += "<td>-</td>"
                else:
                    pe_cells += "<td>-</td>"
            else:
                ce_val = ce_df.loc[strike, c] if (not ce_df.empty and strike in ce_df.index and c in ce_df.columns) else ""
                pe_val = pe_df.loc[strike, c] if (not pe_df.empty and strike in pe_df.index and c in pe_df.columns) else ""

                # Format volume and OI in crore
                if c == "volume" and ce_val != "":
                    ce_val = format_to_crore(ce_val)
                if c == "volume" and pe_val != "":
                    pe_val = format_to_crore(pe_val)
                if c == "oi" and ce_val != "":
                    ce_val = format_to_crore(ce_val)
                if c == "oi" and pe_val != "":
                    pe_val = format_to_crore(pe_val)

                ce_cells += f"<td>{ce_val}</td>"
                pe_cells += f"<td>{pe_val}</td>"

        row_style = "style='background-color: #ffeb3b; font-weight: bold;'" if strike == atm_strike else ""
        rows_html += f"<tr {row_style}>{ce_cells}<td><b>{strike}</b></td>{pe_cells}</tr>"

    # Calculate totals (excluding vol_change and oi_change from sum)
    sum_cols = [c for c in lr_cols if c not in ["vol_change", "oi_change"]]
    ce_totals = ce_df[sum_cols].sum(numeric_only=True) if not ce_df.empty else pd.Series(0, index=sum_cols)
    pe_totals = pe_df[sum_cols].sum(numeric_only=True) if not pe_df.empty else pd.Series(0, index=sum_cols)
    ce_itm_totals = ce_itm_df[sum_cols].sum(numeric_only=True) if not ce_itm_df.empty else pd.Series(0, index=sum_cols)
    pe_itm_totals = pe_itm_df[sum_cols].sum(numeric_only=True) if not pe_itm_df.empty else pd.Series(0, index=sum_cols)

    ce_headers, pe_headers = generate_headers(vol_interval, oi_interval)

    # CE Totals
    ce_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            ce_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                ce_totals_cells += f"<td><b>{format_to_crore(ce_totals[c])}</b></td>"
            else:
                ce_totals_cells += f"<td><b>{ce_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #c8e6c9; font-weight: bold;'>{ce_totals_cells}<td>CE TOTAL</td>{'<td>-</td>' * len(lr_cols)}</tr>"

    # PE Totals
    pe_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            pe_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                pe_totals_cells += f"<td><b>{format_to_crore(pe_totals[c])}</b></td>"
            else:
                pe_totals_cells += f"<td><b>{pe_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #c8e6c9; font-weight: bold;'>{'<td>-</td>' * len(lr_cols)}<td>PE TOTAL</td>{pe_totals_cells}</tr>"

    # CE ITM Totals
    ce_itm_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            ce_itm_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                ce_itm_totals_cells += f"<td><b>{format_to_crore(ce_itm_totals[c])}</b></td>"
            else:
                ce_itm_totals_cells += f"<td><b>{ce_itm_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #b3e5fc; font-weight: bold;'>{ce_itm_totals_cells}<td>CE ITM TOTAL</td>{'<td>-</td>' * len(lr_cols)}</tr>"

    # PE ITM Totals
    pe_itm_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            pe_itm_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                pe_itm_totals_cells += f"<td><b>{format_to_crore(pe_itm_totals[c])}</b></td>"
            else:
                pe_itm_totals_cells += f"<td><b>{pe_itm_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #b3e5fc; font-weight: bold;'>{'<td>-</td>' * len(lr_cols)}<td>PE ITM TOTAL</td>{pe_itm_totals_cells}</tr>"

    # All Totals
    all_totals = ce_totals.add(pe_totals, fill_value=0)
    all_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            all_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                all_totals_cells += f"<td><b>{format_to_crore(all_totals[c])}</b></td>"
            else:
                all_totals_cells += f"<td><b>{all_totals[c]:,.2f}</b></td>"
    rows_html += f"<tr style='background-color: #ffd699; font-weight: bold;'>{all_totals_cells}<td>ALL TOTAL</td>{all_totals_cells}</tr>"

    analysis_html = generate_market_insights(ce_df, pe_df, spot_price)

    return rows_html, spot_price, analysis_html, ce_headers, pe_headers

def generate_headers(vol_interval=1, oi_interval=1):
    cols = ["ASK", "BID", "LTP", "LTPCH", "VOLUME (Cr)", f"VOL Δ({vol_interval}m)", "OI (Cr)", f"OI Δ({oi_interval}m)", "OICH", "OICHP", "PREV_OI"]
    ce_headers = "".join([f"<th>{c}</th>" for c in cols])
    pe_headers = "".join([f"<th>{c}</th>" for c in cols])
    return ce_headers, pe_headers

def generate_market_insights(ce_df, pe_df, spot_price):
    try:
        total_ce_oi = ce_df["oi"].sum() if not ce_df.empty else 0
        total_pe_oi = pe_df["oi"].sum() if not pe_df.empty else 0
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else None

        strongest_support = pe_df.loc[pe_df["oi"].idxmax(), "strike_price"] if not pe_df.empty else None
        strongest_resistance = ce_df.loc[ce_df["oi"].idxmax(), "strike_price"] if not ce_df.empty else None

        ce_vol = ce_df["volume"].sum() if not ce_df.empty else 0
        pe_vol = pe_df["volume"].sum() if not pe_df.empty else 0
        
        # Calculate average LTPCH for CE and PE
        ce_ltpch_avg = ce_df["ltpch"].mean() if not ce_df.empty and "ltpch" in ce_df.columns else 0
        pe_ltpch_avg = pe_df["ltpch"].mean() if not pe_df.empty and "ltpch" in pe_df.columns else 0
        
        # Determine market direction based on LTPCH comparison
        if ce_ltpch_avg > pe_ltpch_avg:
            ltpch_trend = "Market Up 📈"
        elif ce_ltpch_avg < pe_ltpch_avg:
            ltpch_trend = "Market Down 📉"
        else:
            ltpch_trend = "Sideways ⚖️"
        
        # Special case: if both are negative
        if ce_ltpch_avg < 0 and pe_ltpch_avg < 0:
            ltpch_trend = "Sideways ⚖️"
        
        # Calculate average OI change percentage for CE and PE
        ce_oichp_avg = ce_df["oichp"].mean() if not ce_df.empty and "oichp" in ce_df.columns else 0
        pe_oichp_avg = pe_df["oichp"].mean() if not pe_df.empty and "oichp" in pe_df.columns else 0
        
        # Determine market direction based on OI change percentage comparison
        if ce_oichp_avg < pe_oichp_avg:
            oichp_trend = "Market Up 📈"
        else:
            oichp_trend = "Market Down 📉"
        
        # Determine market direction based on volume comparison
        if ce_vol > pe_vol:
            volume_trend = "Market Up 📈"
        else:
            volume_trend = "Market Down 📉"

        trend_bias = ""
        if pcr is not None:
            if pcr > 1:
                trend_bias = "Bearish 📉"
            elif pcr < 0.8:
                trend_bias = "Bullish 📈"
            else:
                trend_bias = "Neutral ⚖️"

        return f"""
        <h3>🔎 Market Insights</h3>
        <ul>
            <li><b>Spot Price:</b> {spot_price}</li>
            <li><b>Total CE OI:</b> {format_to_crore(total_ce_oi)} Cr</li>
            <li><b>Total PE OI:</b> {format_to_crore(total_pe_oi)} Cr</li>
            <li><b>Put-Call Ratio (PCR):</b> {pcr}</li>
            <li><b>Sajid Sir : About Price</b> {ltpch_trend}</li>
            <li><b>Sajid Sir : About Buyers</b> {oichp_trend}</li>
            <li><b>Sajid Sir : About Transaction</b> {volume_trend}</li>
            <li><b>Strongest Support (PE OI):</b> {strongest_support}</li>
            <li><b>Strongest Resistance (CE OI):</b> {strongest_resistance}</li>
            <li><b>Trend Bias:</b> {trend_bias}</li>
        </ul>
        """
    except Exception as e:
        return f"<p>Error in analysis: {e}</p>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True)
