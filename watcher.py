#!/usr/bin/env python3
"""
Noon Minutes Discount Watcher — v5.0 (Digest & Clean UI)
--------------------------------------------------------
- Gathers all discounts across sweeps.
- Sends grouped, compact digests sorted by priority & discount.
- Disables bulky link previews to keep Telegram clean.
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

BUILTIN_BROAD_KEYWORDS = [
    "حليب", "لبن", "زبادي", "جبن", "قشطة", "زبده", "بيض", "عصير", "ماء", "بيبسي", "كولا", "قهوة", "شاي",
    "أرز", "مكرونة", "زيت", "سكر", "طحين", "خبز", "توست", "شوكولاتة", "شيبس", "بسكويت", "حلويات", "مكسرات",
    "دجاج", "صدور", "لحم", "مفروم", "سمك", "تونة", "برجر", "ناجت", "روبيان", "مجمدات", "مثلجات", "ايس كريم",
    "خضار", "فواكه", "طماطم", "بطاطس", "بصل", "ليمون", "موز", "تفاح", "برتقال", "صابون", "شامبو", "منظف", 
    "تايد", "اريال", "برسيل", "منعم ملابس", "كمفورت", "داوني", "فانيش", "كلوركس", "صابون صحون", "فيري", 
    "كبسولات غسيل", "ديتول", "مطهر", "جيل غسيل", "مناديل", "فاين", "اكياس نفايات", "حلاو", "علك", "لبان",
    "بروتين", "واي بروتين", "بروتين بار", "سناك", "شاحن", "سماعة", "باوربانك", "سلك", "كابل", "ايفون", 
    "ايباد", "انكر", "فطور", "كورن فليكس", "عسل", "مربى", "صلصة", "اندومي", "نودلز", "المراعي", "نادك", 
    "الصافي", "ساديا", "دو", "رضوى", "امريكانا", "هرفي", "كبير", "نوتيلا", "عروض", "خصم", "تخفيضات"
]

env_keywords = [k.strip() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
KEYWORDS = list(dict.fromkeys(env_keywords + BUILTIN_BROAD_KEYWORDS))

PRIORITY_1_THRESHOLD = float(os.environ.get("PRIORITY_1_THRESHOLD", "80"))
PRIORITY_2_THRESHOLD = float(os.environ.get("PRIORITY_2_THRESHOLD", "70"))

MIN_CYCLE_SLEEP = int(os.environ.get("MIN_CYCLE_SLEEP", "600"))
MAX_CYCLE_SLEEP = int(os.environ.get("MAX_CYCLE_SLEEP", "900"))
MIN_REQUEST_DELAY = float(os.environ.get("MIN_REQUEST_DELAY", "1.2"))
MAX_REQUEST_DELAY = float(os.environ.get("MAX_REQUEST_DELAY", "2.8"))

STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
RESEND_AFTER_SECONDS = 60 * 60 * 24  # 24 ساعة تباعد للمنتجات الثابتة

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("noon-watcher")

# ----------------------------- HELPERS & CORE ----------------------------- #

def clean_md(text: str) -> str:
    """تنظيف النصوص لتجنب أخطاء تنسيق تيليجرام Markdown"""
    for char in ["*", "_", "`", "[", "]"]:
        text = text.replace(char, "")
    return text.strip()

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
                "disable_web_page_preview": True,  # إلغاء الصور الكبيرة المزعجة
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

def extract_deals_from_response(data: dict, keyword: str):
    deals = []

    def handle_product(product: dict):
        sku = product.get("sku", "")
        title = product.get("title", "منتج بدون اسم")
        brand = product.get("brand", "")
        size_info = product.get("sizeInfo", "")
        price = product.get("price")
        offer_price = product.get("offerPrice")

        if (
            isinstance(price, (int, float))
            and isinstance(offer_price, (int, float))
            and price > offer_price > 0
        ):
            discount = (price - offer_price) / price * 100
            deals.append({
                "id": sku,
                "title": f"{brand} {title}".strip(),
                "size": size_info,
                "current_price": offer_price,
                "original_price": price,
                "discount": discount,
                "keyword": keyword,
            })

        vbs = product.get("variantsBottomSheet") or {}
        for variant in vbs.get("variants", []) or []:
            v_price = variant.get("price")
            v_striked = variant.get("strikedPrice")
            v_discount = variant.get("discountPercent")
            v_sku = variant.get("sku", sku)

            if (
                isinstance(v_price, (int, float))
                and isinstance(v_striked, (int, float))
                and v_striked > v_price > 0
            ):
                discount = v_discount if isinstance(v_discount, (int, float)) else (
                    (v_striked - v_price) / v_striked * 100
                )
                deals.append({
                    "id": v_sku,
                    "title": f"{brand} {title} ({variant.get('qtyText', '')})".strip(),
                    "size": variant.get("title", size_info),
                    "current_price": v_price,
                    "original_price": v_striked,
                    "discount": discount,
                    "keyword": keyword,
                })

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "instantProductBox" and isinstance(node.get("product"), dict):
                handle_product(node["product"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return deals

def search_keyword(session: requests.Session, keyword: str):
    params = {"q": keyword}
    try:
        resp = session.get(API_ENDPOINT, params=params, timeout=20)
    except Exception as e:
        log.warning(f"Connection error for '{keyword}': {e}")
        return []

    if resp.status_code != 200:
        return []

    try:
        data = resp.json()
    except Exception:
        return []

    return extract_deals_from_response(data, keyword)

def build_deal_line(deal: dict, is_increased: bool) -> str:
    title_safe = clean_md(deal['title'])
    size_str = f" ({clean_md(deal['size'])})" if deal['size'] else ""
    search_link = f"https://minutes.noon.com/saudi-en/search/?q={quote(deal['title'])}"
    
    tag = " 📈" if is_increased else ""
    return (
        f"• [{title_safe}{size_str}]({search_link}){tag}\n"
        f"   └ 💰 `{deal['current_price']:.2f} ر.س` ~{deal['original_price']:.2f}~ "
        f"| خصم: *{deal['discount']:.0f}%*\n"
    )

def send_digest(p1_deals: list, p2_deals: list):
    """إرسال العروض مجمعة في رسائل منظمة مع تقسيمها تلقائياً إذا طالت"""
    sections = []

    if p1_deals:
        p1_deals.sort(key=lambda x: x[0]["discount"], reverse=True)
        lines = [f"🚨 *أخطاء تسعيرية مؤكدة (Priority 1 ≥ {PRIORITY_1_THRESHOLD:.0f}%)*:\n"]
        for d, inc in p1_deals:
            lines.append(build_deal_line(d, inc))
        sections.append("\n".join(lines))

    if p2_deals:
        p2_deals.sort(key=lambda x: x[0]["discount"], reverse=True)
        lines = [f"⚡ *عروض قوية جداً (Priority 2 ≥ {PRIORITY_2_THRESHOLD:.0f}%)*:\n"]
        for d, inc in p2_deals:
            lines.append(build_deal_line(d, inc))
        sections.append("\n".join(lines))

    if not sections:
        return

    full_text = f"📍 *تحديث عروض نون مينتس — {LOCATION_LABEL}*\n" + "—" * 20 + "\n\n" + "\n\n".join(sections)

    # تقسيم الرسالة إذا تجاوزت 3800 حرف
    if len(full_text) <= 3800:
        send_telegram(full_text)
    else:
        chunks = []
        current_chunk = ""
        for line in full_text.split("\n"):
            if len(current_chunk) + len(line) + 1 > 3800:
                chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        if current_chunk:
            chunks.append(current_chunk)

        for chunk in chunks:
            send_telegram(chunk)
            time.sleep(1)

def run_one_cycle(session: requests.Session, seen: dict) -> int:
    now = time.time()
    p1_queue = []
    p2_queue = []
    total_found = 0

    log.info(f"Scanning {len(KEYWORDS)} catalog segments...")

    for kw in KEYWORDS:
        log.info(f"🔍 Sweeping: {kw}")
        deals = search_keyword(session, kw)

        for deal in deals:
            discount = deal["discount"]
            if discount < PRIORITY_2_THRESHOLD:
                continue

            did = deal["id"]
            state = seen.get(did)
            is_increased = False

            if state:
                if isinstance(state, (int, float)):
                    last_sent = state
                    last_discount = 0
                else:
                    last_sent = state.get("time", 0)
                    last_discount = state.get("discount", 0)

                if (now - last_sent) < RESEND_AFTER_SECONDS and discount <= last_discount:
                    continue

                if (now - last_sent) < RESEND_AFTER_SECONDS and discount > last_discount:
                    is_increased = True

            seen[did] = {"time": now, "discount": discount}
            total_found += 1

            if discount >= PRIORITY_1_THRESHOLD:
                p1_queue.append((deal, is_increased))
            else:
                p2_queue.append((deal, is_increased))

        time.sleep(random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY))

    if p1_queue or p2_queue:
        send_digest(p1_queue, p2_queue)

    save_seen(seen)
    return total_found

SINGLE_CYCLE = os.environ.get("SINGLE_CYCLE", "false").lower() == "true"

def main():
    log.info("🚀 Starting Noon Minutes Digest Watcher")
    session = build_session()
    seen = load_seen()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("⚠️ Telegram token/chat_id not set — alerts will only be logged.")

    if SINGLE_CYCLE:
        try:
            sent = run_one_cycle(session, seen)
            log.info(f"✅ Single sweep finished. Deals found: {sent}")
        except Exception as e:
            log.error(f"❌ Error in sweep: {e}")
        return

    while True:
        try:
            sent = run_one_cycle(session, seen)
            log.info(f"✅ Sweep finished. Deals found: {sent}")
        except Exception as e:
            log.error(f"❌ Error in sweep: {e}")

        sleep_for = random.uniform(MIN_CYCLE_SLEEP, MAX_CYCLE_SLEEP)
        log.info(f"😴 Sleeping {sleep_for/60:.1f} minutes before next sweep...")
        time.sleep(sleep_for)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("🛑 Stopped manually.")
