#!/usr/bin/env python3
"""
Noon Minutes Discount Watcher — v4.0 (Smart AI Filter & Pricing Error Radar)
------------------------------------------------------------------
Calls the internal search endpoint directly:
    https://minutes.noon.com/_svc/catalog/search?q=<keyword>

Features:
1. Keeps your original custom keyword list intact.
2. Uses Gemini AI with live Google Search Grounding to double-check deals.
3. Automatically catches severe pricing errors (even if discount is listed as 0%).
4. Bypasses the 24-hour notification limit if a discount percentage increases.
"""

import os
import sys
import json
import time
import random
import logging
from urllib.parse import quote

import requests

# ----------------------------- CONFIG ----------------------------- #

API_ENDPOINT = "https://minutes.noon.com/_svc/catalog/search"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# الهيدرز الخاصة بالموقع ونطاق حي طويق بالرياض
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Cache-Control": "no-cache, max-age=0, must-revalidate, no-store",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
    ),
    "x-platform": os.environ.get("X_PLATFORM", "mweb"),
    "x-cms": os.environ.get("X_CMS", "v2"),
    "x-build": os.environ.get("X_BUILD", "17801"),
    "x-experience": os.environ.get("X_EXPERIENCE", "nooninstant"),
    "x-mp": os.environ.get("X_MP", "nooninstant"),
    "x-visitor-id": os.environ.get("X_VISITOR_ID", "ae53dba7-0002-4b38-8c9e-b7fc88fabc35"),
    "x-locale": os.environ.get("X_LOCALE", "ar-sa"),
    "x-lat": os.environ.get("X_LAT", "245868084"),
    "x-lng": os.environ.get("X_LNG", "465789328"),
    "x-border-enabled": "true",
    "x-ecom-zonecode": os.environ.get("X_ECOM_ZONECODE", "SA-RUH-S17"),
    "x-mp-country": "sa",
    "x-nooninstant-zonecode": os.environ.get("X_NOONINSTANT_ZONECODE", "W00055702A"),
    "x-rocket-enabled": "true",
    "x-services-zonecode": os.environ.get("X_SERVICES_ZONECODE", "SERVICES-SA-RIYADH"),
}

NOON_COOKIES = os.environ.get("NOON_COOKIES", "")
LOCATION_LABEL = os.environ.get("LOCATION_LABEL", "حي طويق، الرياض")

# قائمة الكلمات الافتراضية الخاصة بك (تُستبدل تلقائياً بقيم Secrets KEYWORDS إن وجدت)
DEFAULT_KEYWORDS = (
    "دجاج,صدور دجاج,لحم,مفروم,برجر,بطاطس,ناجت,روبيان,مجمدات,شاحن,سماعة,"
    "باوربانك,سلك,كابل,ايفون,ايباد,انكر,بروتين,واي بروتين,بروتين بار,سناك,"
    "شيبس,مكسرات,شوكولاتة,بسكويت,حليب,لبن,زبادي,جبن,عصير,دايت,ونة,بيض,ارز,قهوة"
)
KEYWORDS = [
    k.strip() for k in os.environ.get("KEYWORDS", DEFAULT_KEYWORDS).split(",") if k.strip()
]

PRIORITY_1_THRESHOLD = float(os.environ.get("PRIORITY_1_THRESHOLD", "80"))
PRIORITY_2_THRESHOLD = float(os.environ.get("PRIORITY_2_THRESHOLD", "70"))

MIN_CYCLE_SLEEP = int(os.environ.get("MIN_CYCLE_SLEEP", "600"))
MAX_CYCLE_SLEEP = int(os.environ.get("MAX_CYCLE_SLEEP", "900"))
MIN_REQUEST_DELAY = float(os.environ.get("MIN_REQUEST_DELAY", "2.0"))
MAX_REQUEST_DELAY = float(os.environ.get("MAX_REQUEST_DELAY", "5.0"))

STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
RESEND_AFTER_SECONDS = 60 * 60 * 24  # حد عدم التكرار (24 ساعة)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("noon-watcher")

# ----------------------------- STATE HELPERS ----------------------------- #

def load_seen() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_seen(seen: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Could not save state file: {e}")

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("No Telegram token/chat_id set — printing alert instead:")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(f"Telegram send failed: {r.status_code} {r.text}")
    except Exception as e:
        log.warning(f"Telegram send error: {e}")

def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if NOON_COOKIES:
        for pair in NOON_COOKIES.split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                s.cookies.set(k, v)
    return s

# ----------------------------- AI VERIFICATION ----------------------------- #

def verify_deal_with_ai(title, current_price):
    """
    يتصل بـ Gemini API مع تفعيل ميزة البحث الحي على جوجل لمقارنة السعر بالأسواق الأخرى
    كأمازون وجرير والصيدليات لكشف أخطاء التسعير أو الخصومات الوهمية.
    """
    api_key = os.environ
