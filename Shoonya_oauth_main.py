import time
import json
import hashlib
import requests
import pyotp
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
from time import sleep

from NorenApi import NorenApi
from config import user_configs


# ───────────────── CONFIG ─────────────────
cfg = user_configs["C210"]  ##  FILL YOUR USER ID HERE

USER_ID = cfg["user_id"]
CLIENT_ID = f"{USER_ID}_U"
PASSWORD = cfg["pwd"]
TOTP_SECRET = cfg["totp"]
SECRET_CODE = cfg["secret"]
API_KEY = cfg["api_key"]
IMEI = "abcd1234"

LOGIN_URL = f"https://trade.shoonya.com/OAuthlogin/investor-entry-level/login?api_key={CLIENT_ID}&route_to={USER_ID}+s+apikey"
TOKEN_URL = "https://trade.shoonya.com/NorenWClientAPI/GenAcsTok"


# ───────────────── API CLASS ─────────────────
class ShoonyaApiPy(NorenApi):
    def __init__(self):
        super().__init__(
            host="https://api.shoonya.com/NorenWClientAPI",
            websocket="wss://api.shoonya.com/NorenWSTP",
        )


api = ShoonyaApiPy()


# ───────────────── HELPER FUNCTIONS ─────────────────


def generate_totp():
    """Generate current TOTP"""
    return pyotp.TOTP(TOTP_SECRET).now()


def extract_auth_code(page):
    """Listen for auth code from redirect"""
    auth_code = None

    def handle_request(request):
        nonlocal auth_code
        if "code=" in request.url:
            parsed = urlparse(request.url)
            params = parse_qs(parsed.query)
            auth_code = params.get("code", [None])[0]

    page.on("request", handle_request)
    return lambda: auth_code


# ───────────────── AUTH FLOW ─────────────────


def get_auth_code():
    """Automate login and fetch auth code"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        get_code = extract_auth_code(page)

        try:
            print("🔐 Logging in...")

            page.goto(LOGIN_URL)

            page.fill("#lgnusrid", USER_ID)
            page.fill("#lgnpwd", PASSWORD)

            page.wait_for_selector("#lgnotp")
            page.fill("#lgnotp", generate_totp())

            page.click("button:has-text('LOGIN')")

            # Wait for redirect
            for _ in range(90):
                if get_code():
                    break
                page.wait_for_timeout(500)

            return get_code()

        except Exception as e:
            page.screenshot(path="error.png")
            print(f"❌ Login failed: {e}")
            return None

        finally:
            browser.close()


# ───────────────── TOKEN GENERATION ─────────────────


def get_api_token(auth_code):
    """Exchange auth code for API token"""
    checksum = hashlib.sha256(
        (CLIENT_ID + SECRET_CODE + auth_code).encode()
    ).hexdigest()

    payload = f'jData={{"code":"{auth_code}","checksum":"{checksum}"}}'
    headers = {"Authorization": f"Bearer {checksum}"}

    response = requests.post(TOKEN_URL, data=payload, headers=headers)

    try:
        data = response.json()
        return data.get("ActTok") or data.get("access_token")
    except:
        print("❌ Token parsing failed")
        print(response.text)
        return None


def get_web_token():
    """Login using WEB API"""
    otp = generate_totp()

    res = api.loginWEB(
        userid=USER_ID,
        password=PASSWORD,
        twoFA=str(otp),
        vendor_code=CLIENT_ID,
        api_secret=API_KEY,
        imei=IMEI,
    )

    print("User:", res.get("uname"))
    print("Message:", res.get("dmsg", "None"))

    return res.get("susertoken")


# ───────────────── WEBSOCKET ─────────────────


def start_websocket(access_token_web=None):
    """Start websocket and subscribe"""

    def on_msg(msg):
        print("Market Data:", msg)

    def on_order(msg):
        print("Order Update:", msg)

    def on_open():
        print("WebSocket Opened")
        api.subscribe(["BSE|1", "BSE|12", "NSE|26000", "NSE|26009"], feed_type="d")

    def on_close():
        print("WebSocket Closed")

    def on_error(err):
        print("Error:", err)

    api.start_websocket(
        subscribe_callback=on_msg,
        order_update_callback=on_order,
        socket_open_callback=on_open,
        socket_close_callback=on_close,
        socket_error_callback=on_error,
        access_token=access_token_web,
    )


# ───────────────── MAIN FLOW ─────────────────


def main():
    # Step 1: Get auth code
    auth_code = get_auth_code()
    if not auth_code:
        print("❌ Auth failed")
        return

    # Step 2: Get API token
    api_token = get_api_token(auth_code)
    print("🔑 API Token:", api_token)

    # Step 3: Get WEB token
    web_token = get_web_token()
    print("🌐 Web Token:", web_token)

    # Step 4: Start session
    api.set_session(USER_ID, api_token)

    # Step 5:
    ## Start websocket with WEB TOKEN
    start_websocket(web_token)

    ## OR

    ## Start websocket with API TOKEN
    start_websocket()
    # Keep alive
    while True:
        sleep(1)


if __name__ == "__main__":
    main()
