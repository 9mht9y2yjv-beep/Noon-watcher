#!/usr/bin/env python3
"""
Noon Minutes Full Catalog Discount Watcher — v6.5 (Production Final)
---------------------------------------------------------------------
- 100% Crash-proof Telegram HTML formatting (True strikethrough & safe escaping).
- Direct Noon Minutes SKU search links.
- Smart deduplication with fallback IDs.
- Automatic department sorting & minimum cash-saving threshold.
"""

import os
import sys
import json
import time
import html
import random
import logging
from urllib.parse import quote

import requests

# ----------------------------- CONFIGURATION ----------------------------- #

API_ENDPOINT = "https://minutes.noon.com/_svc/catalog/search"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Cache-Control": "no-cache, max-age=0, must-revalidate, no-store",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
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

# التصنيفات الكبرى الشاملة لجميع منتجات المتجر
CORE_CATEGORIES = [
    "عروض", "تخفيضات", "سوبرماركت", "بروتين", "ألبان وبيض", "مخبوزات وحلويات",
    "لحوم ودواجن", "مجمدات", "مشروبات وقهوة", "سناكات ومكسرات", "عناية شخصية",
    "نظافة ومنزل", "إلكترونيات وشواحن", "صيدلية وصحة", "خضار وفواكه", "فطور ومربيات"
]

# معايير التنبيه والوفر المالي
PRIORITY_1_DISCOUNT = float(os.environ.get("PRIORITY_1_DISCOUNT", "80"))
PRIORITY_1_MIN_SAVING = float(os.environ.get("PRIORITY_1_MIN_SAVING", "15"))  # وفر لا يقل عن 15 ريال

PRIORITY_2_DISCOUNT = float(os.environ.get("PRIORITY_2_DISCOUNT", "65"))
PRIORITY_2_MIN_SAVING = float(os.environ.get("PRIORITY_2_MIN_SAVING", "10"))  # وفر لا يقل عن 10 ريال

MIN_CYCLE_SLEEP = int(os.environ.get("MIN_CYCLE_SLEEP", "600"))
MAX_CYCLE_SLEEP = int(os.environ.get("MAX_CYCLE_SLEEP", "900"))
STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
RESEND_AFTER_SECONDS = 60 * 60 * 20  # 20 ساعة قبل إعادة إرسال نفس السلعة

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("noon-watcher")

# ----------------------------- CORE FUNCTIONS ----------------------------- #

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
        log.warning(f"Failed to save seen.json: {e}")

def send_telegram_html(message_html: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials missing, logging to stdout:")
        print(message_html)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message_html,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(f"Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        log.warning(f"Telegram network error: {e}")

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
        sku = str(product.get("sku") or "").strip()
        title = str(product.get("title") or "منتج").strip()
        brand = str(product.get("brand") or "").strip()
        size_info = str(product.get("sizeInfo") or "").strip()
        price = product.get("price")
        offer_price = product.get("offerPrice")

        full_title = f"{brand} {title}".strip()
        unique_id = sku if sku else f"gen_{hash(full_title)}"

        if (
            isinstance(price, (int, float))
            and isinstance(offer_price, (int, float))
            and price > offer_price > 0
        ):
            discount = ((price - offer_price) / price) * 100
            savings = price - offer_price
            deals.append({
                "id": unique_id,
                "sku": sku,
                "title": full_title,
                "size": size_info,
                "current_price": float(offer_price),
                "original_price": float(price),
                "discount": discount,
                "savings": savings,
                "keyword": keyword,
            })

        vbs = product.get("variantsBottomSheet") or {}
        for variant in vbs.get("variants", []) or []:
            v_price = variant.get("price")
            v_striked = variant.get("strikedPrice")
            v_sku = str(variant.get("sku") or sku).strip()
            v_discount = variant.get("discountPercent")
            v_qty = str(variant.get("qtyText") or "").strip()
            v_title = f"{brand} {title} ({v_qty})".strip() if v_qty else full_title
            v_unique_id = v_sku if v_sku else f"gen_{hash(v_title)}"

            if (
                isinstance(v_price, (int, float))
                and isinstance(v_striked, (int, float))
                and v_striked > v_price > 0
            ):
                discount = float(v_discount) if isinstance(v_discount, (int, float)) else (
                    ((v_striked - v_price) / v_striked) * 100
                )
                deals.append({
                    "id": v_unique_id,
                    "sku": v_sku,
                    "title": v_title,
                    "size": str(variant.get("title") or size_info).strip(),
                    "current_price": float(v_price),
                    "original_price": float(v_striked),
                    "discount": discount,
                    "savings": float(v_striked - v_price),
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

def search_department(session: requests.Session, category: str):
    params = {
        "q": category,
        "sort[by]": "discount",
        "sort[dir]": "desc",
        "limit": 50,
        "page": 1,
    }
    try:
        resp = session.get(API_ENDPOINT, params=params, timeout=20)
        if resp.status_code == 429:
            log.warning("Rate limit reached. Pausing for 30s...")
            time.sleep(30)
            return []
        if resp.status_code != 200:
            return []
        return extract_deals_from_response(resp.json(), category)
    except Exception as e:
        log.warning(f"Network error sweeping '{category}': {e}")
        return []

def build_deal_html(deal: dict, is_increased: bool) -> str:
    title_escaped = html.escape(deal['title'])
    size_escaped = f" ({html.escape(deal['size'])})" if deal['size'] else ""
    
    # رابط دقيق يفتح منتج المينتس مباشرة بالـ SKU أو بالاسم
    search_query = deal['sku'] if deal['sku'] else deal['title']
    noon_link = f"https://minutes.noon.com/saudi-ar/search/?q={quote(search_query)}"
    
    tag = " 📈 <b>(الخصم ارتفع)</b>" if is_increased else ""
    
    return (
        f"• <a href=\"{noon_link}\"><b>{title_escaped}{size_escaped}</b></a>{tag}\n"
        f"   └ 💰 <code>{deal['current_price']:.2f} ر.س</code> <s>{deal['original_price']:.2f}</s> "
        f"| وفر: <b>{deal['savings']:.1f} ر.س</b> (<b>{deal['discount']:.0f}%-</b>)\n"
    )

def dispatch_digest(p1: list, p2: list):
    sections = []

    if p1:
        p1.sort(key=lambda x: (x[0]["discount"], x[0]["savings"]), reverse=True)
        body = ["🚨 <b>أخطاء تسعيرية وصفقات استثنائية (Priority 1):</b>\n"]
        for d, inc in p1:
            body.append(build_deal_html(d, inc))
        sections.append("\n".join(body))

    if p2:
        p2.sort(key=lambda x: (x[0]["discount"], x[0]["savings"]), reverse=True)
        body = ["⚡ <b>عروض وتخفيضات قوية (Priority 2):</b>\n"]
        for d, inc in p2:
            body.append(build_deal_html(d, inc))
        sections.append("\n".join(body))

    if not sections:
        return

    header = f"🛒 <b>رادار صفقات نون مينتس — {html.escape(LOCATION_LABEL)}</b>\n" + "—" * 25 + "\n\n"
    full_message = header + "\n\n".join(sections)

    if len(full_message) <= 3800:
        send_telegram_html(full_message)
    else:
        chunk = ""
        for line in full_message.split("\n"):
            if len(chunk) + len(line) + 1 > 3800:
                send_telegram_html(chunk)
                chunk = line + "\n"
                time.sleep(1.5)
            else:
                chunk += line + "\n"
        if chunk:
            send_telegram_html(chunk)

def run_one_cycle(session: requests.Session, seen: dict) -> int:
    now = time.time()
    p1_list = []
    p2_list = []
    total_found = 0

    log.info(f"Sweeping {len(CORE_CATEGORIES)} comprehensive catalog departments...")

    for cat in CORE_CATEGORIES:
        log.info(f"🔎 Scanning: {cat}")
        deals = search_department(session, cat)

        for deal in deals:
            disc = deal["discount"]
            save_val = deal["savings"]

            # تصنيف الأولوية بناءً على نسبة الخصم والوفر المالي الحقيقي
            is_p1 = (disc >= PRIORITY_1_DISCOUNT and save_val >= PRIORITY_1_MIN_SAVING) or (save_val >= 40)
            is_p2 = (disc >= PRIORITY_2_DISCOUNT and save_val >= PRIORITY_2_MIN_SAVING) and not is_p1

            if not (is_p1 or is_p2):
                continue

            did = deal["id"]
            state = seen.get(did)
            is_increased = False

            if state:
                last_time = state if isinstance(state, (int, float)) else state.get("time", 0)
                last_disc = 0 if isinstance(state, (int, float)) else state.get("discount", 0)

                if (now - last_time) < RESEND_AFTER_SECONDS and disc <= last_disc:
                    continue

                if (now - last_time) < RESEND_AFTER_SECONDS and disc > last_disc:
                    is_increased = True

            seen[did] = {"time": now, "discount": disc}
            total_found += 1

            if is_p1:
                p1_list.append((deal, is_increased))
            else:
                p2_list.append((deal, is_increased))

        time.sleep(random.uniform(1.2, 2.5))

    if p1_list or p2_list:
        dispatch_digest(p1_list, p2_list)

    save_seen(seen)
    return total_found

SINGLE_CYCLE = os.environ.get("SINGLE_CYCLE", "false").lower() == "true"

def main():
    log.info("🚀 Noon Minutes Robust Engine Active.")
    session = build_session()
    seen = load_seen()

    if SINGLE_CYCLE:
        sent = run_one_cycle(session, seen)
        log.info(f"✅ Single sweep complete. Deals processed: {sent}")
        return

    while True:
        try:
            sent = run_one_cycle(session, seen)
            log.info(f"✅ Sweep complete. Deals processed: {sent}")
        except Exception as e:
            log.error(f"⚠️ Error in cycle: {e}")

        sleep_seconds = random.uniform(MIN_CYCLE_SLEEP, MAX_CYCLE_SLEEP)
        log.info(f"😴 Sleeping for {sleep_seconds/60:.1f} minutes...")
        time.sleep(sleep_seconds)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Terminated manually.")
