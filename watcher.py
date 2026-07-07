#!/usr/bin/env python3
"""
Noon Minutes Watcher — v6.1 (Diagnostic & Algorithmic Engine)
------------------------------------------------------------------
Independent & Free. No External AI calls.
Includes full error logging to diagnose blank responses.
"""

import os
import sys
import json
import time
import random
import logging
import statistics
from urllib.parse import quote

import requests

# ----------------------------- CONFIG ----------------------------- #

API_ENDPOINT = "https://minutes.noon.com/_svc/catalog/search"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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
    "x-ecom-zonecode": os.environ.get("X_ECOM_ZONEcode", "SA-RUH-S17"),
    "x-mp-country": "sa",
    "x-nooninstant-zonecode": os.environ.get("X_NOONINSTANT_ZONECODE", "W00055702A"),
    "x-rocket-enabled": "true",
    "x-services-zonecode": os.environ.get("X_SERVICES_ZONECODE", "SERVICES-SA-RIYADH"),
}

NOON_COOKIES = os.environ.get("NOON_COOKIES", "")
LOCATION_LABEL = os.environ.get("LOCATION_LABEL", "حي طويق، الرياض")

DEFAULT_KEYWORDS = (
    "حليب,لبن,زبادي,جبن,بيض,عصير,مياه,مشروبات غازية,قهوة,شاي,"
    "أرز,مكرونة,زيت,سكر,ملح,طحين,خبز,معجنات,شوكولاتة,شيبس,"
    "بسكويت,حلويات,مكسرات,دجاج,لحم,سمك,نقانق,برجر,"
    "خضار,فواكه,طماط,بطاطس,بصل,ليمون,موز,تفاح,"
    "منظف,صابون,شامبو,معجون اسنان,مناديل,حفاضات,"
    "منتجات عناية,مطهر,مبيض,غسول,مزيل عرق,"
    "طعام قطط,طعام كلاب,مستلزمات اطفال,حليب اطفال"
)
KEYWORDS = [
    k.strip() for k in os.environ.get("KEYWORDS", DEFAULT_KEYWORDS).split(",") if k.strip()
]

# عتبات الفحص الرقمي
MIN_OBSERVATIONS = 3          
PRICE_ERROR_DROP_PCT = 60.0   
PEER_ERROR_DROP_PCT = 65.0    
OFFICIAL_DISCOUNT_MIN_PCT = 70.0  

MIN_CYCLE_SLEEP = int(os.environ.get("MIN_CYCLE_SLEEP", "600"))
MAX_CYCLE_SLEEP = int(os.environ.get("MAX_CYCLE_SLEEP", "900"))
MIN_REQUEST_DELAY = float(os.environ.get("MIN_REQUEST_DELAY", "1.0"))
MAX_REQUEST_DELAY = float(os.environ.get("MAX_REQUEST_DELAY", "2.5"))

SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "price_history.json")
RESEND_AFTER_SECONDS = 60 * 60 * 24   

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("noon-watcher")

# ----------------------------- HELPERS ----------------------------- #

def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_json(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Could not save {path}: {e}")

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("No Telegram token set — printing instead:")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": False}, timeout=15)
    except Exception as e:
        log.warning(f"Telegram fail: {e}")

def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if NOON_COOKIES:
        for pair in NOON_COOKIES.split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                s.cookies.set(k, v)
    return s

# ----------------------------- ENGINES ----------------------------- #

def search_keyword(session: requests.Session, keyword: str):
    try:
        resp = session.get(API_ENDPOINT, params={"q": keyword}, timeout=20)
        if resp.status_code == 200:
            items = extract_products(resp.json(), keyword)
            log.info(f"✅ Found {len(items)} raw products for '{keyword}'")
            return items
        else:
            log.error(f"⚠️ Noon API returned status code {resp.status_code} for '{keyword}'")
    except Exception as e:
        log.error(f"❌ Network/Connection error for '{keyword}': {e}")
    return []

def extract_products(data: dict, keyword: str):
    items = []
    def handle(p):
        try:
            sku = p.get("sku", "")
            brand = p.get("brand", "")
            title_text = p.get("title", "")
            title = f"{brand} {title_text}".strip() if brand else title_text.strip()
            if not title:
                title = "منتج بدون اسم"
                
            if p.get("offerPrice"):
                items.append({"id": sku, "title": title, "size": p.get("sizeInfo", ""), "current_price": float(p["offerPrice"]), "official_original": p.get("price"), "keyword": keyword})
            for v in p.get("variantsBottomSheet", {}).get("variants", []) or []:
                if v.get("price"):
                    v_sku = v.get("sku", sku)
                    qty_text = v.get("qtyText", "")
                    v_title = f"{title} ({qty_text})" if qty_text else title
                    items.append({"id": v_sku, "title": v_title.strip(), "size": v.get("title", ""), "current_price": float(v["price"]), "official_original": v.get("strikedPrice"), "keyword": keyword})
        except Exception as e:
            log.error(f"Error parsing specific product fields: {e}")
    
    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "instantProductBox" and n.get("product"): 
                handle(n["product"])
            for v in n.values(): 
                walk(v)
        elif isinstance(n, list):
            for i in n: 
                walk(i)
    try:
        walk(data)
    except Exception as e:
        log.error(f"Error walking response JSON structure: {e}")
    return items

# ----------------------------- CORE LOGIC ----------------------------- #

def run_one_cycle(session: requests.Session, seen: dict, history: dict) -> int:
    now = time.time()
    alerts_sent = 0
    has_history_changes = False

    for kw in KEYWORDS:
        log.info(f"🔍 Sweeping Category: {kw}")
        items = search_keyword(session, kw)
        if not items:
            continue

        all_prices_in_cycle = [item["current_price"] for item in items if item["current_price"] > 0]
        peer_median_price = statistics.median(all_prices_in_cycle) if len(all_prices_in_cycle) >= 3 else None

        for item in items:
            sku = item["id"]
            current = item["current_price"]
            pts = history.get(sku, [])
            
            baseline = statistics.median([p for _, p in pts]) if len(pts) >= MIN_OBSERVATIONS else None
            drop_pct = ((baseline - current) / baseline * 100) if baseline else None
            official_drop = ((item["official_original"] - current) / item["official_original"] * 100) if (item.get("official_original") and item["official_original"] > current) else 0

            emoji, label, reason = None, None, ""

            if drop_pct is not None:
                if drop_pct >= PRICE_ERROR_DROP_PCT:
                    emoji, label, reason = "🔴", "خطأ تسعير مؤكد تاريخياً!", f"السعر انهار عن متوسطه المعتاد بنسبة {drop_pct:.0f}%"
                elif official_drop >= OFFICIAL_DISCOUNT_MIN_PCT and drop_pct >= 20:
                    emoji, label, reason = "🟢", "خصم حقيقي ممتاز", f"أقل بنسبة {drop_pct:.0f}% عن سعره التاريخي"
                elif official_drop >= OFFICIAL_DISCOUNT_MIN_PCT and drop_pct < 12:
                    continue
            
            elif peer_median_price and peer_median_price > 0:
                drop_vs_peers_pct = ((peer_median_price - current) / peer_median_price * 100)
                if drop_vs_peers_pct >= PEER_ERROR_DROP_PCT:
                    emoji, label, reason = "🚨", "خطأ تسعير لحظي (مقارنة بالقسم)!", f"سعر المنتج منخفض بنسبة {drop_vs_peers_pct:.0f}% عن متوسط أسعار باقي المنتجات الشبيهة المعروضة معه الآن ({peer_median_price:.1f} ر.س)"
                elif official_drop >= OFFICIAL_DISCOUNT_MIN_PCT:
                    emoji, label, reason = "🟢", "خصم معلن قوي (منتج جديد)", f"نسبة تخفيض المتجر الرسمية {official_drop:.0f}% ولسا نجمع بياناته التاريخية"

            if emoji:
                key = f"{sku}:{emoji}"
                if not (seen.get(key) and (now - seen[key]) < RESEND_AFTER_SECONDS):
                    msg = (
                        f"{emoji} *{label}*\n"
                        f"📦 *{item['title']}*\n"
                        f"📏 {item['size']}\n"
                        f"💰 السعر الحالي: `{current:.2f}` ر.س\n"
                        f"💬 {reason}\n"
                        f"📍 {LOCATION_LABEL}\n"
                        f"🔎 كلمة البحث: {item['keyword']}\n"
                        f"🆔 SKU: `{sku}`\n"
                        f"🔗 [افتح نون](https://minutes.noon.com/saudi-en/search/?q={quote(item['title'])})"
                    )
                    send_telegram(msg)
                    seen[key] = now
                    alerts_sent += 1

            if not pts or pts[-1][1] != current:
                pts.append([now, current])
                history[sku] = [pt for pt in pts if pt[0] >= (now - 30*86400)][-30:] 
                has_history_changes = True

        time.sleep(random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY))

    save_json(SEEN_FILE, seen)
    if has_history_changes:
        save_json(HISTORY_FILE, history)
    return alerts_sent

def main():
    log.info("🚀 Starting Noon Minutes Smart Baseline & Peer Watcher v6.1")
    session = build_session()
    seen = load_json(SEEN_FILE)
    history = load_json(HISTORY_FILE)
    if os.environ.get("SINGLE_CYCLE", "false").lower() == "true":
        run_one_cycle(session, seen, history)
    else:
        while True:
            run_one_cycle(session, seen, history)
            time.sleep(random.uniform(MIN_CYCLE_SLEEP, MAX_CYCLE_SLEEP))

if __name__ == "__main__":
    main()
