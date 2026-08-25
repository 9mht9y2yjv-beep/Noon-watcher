#!/usr/bin/env python3
"""
Noon Minutes Full Catalog Discount Watcher — v6.0 (Enterprise Digest)
---------------------------------------------------------------------
- Scans full departmental categories with auto-sorting by highest discount.
- Filters out low-value micro discounts (requires meaningful SAR savings).
- Consolidates all alerts into one structured Telegram digest.
- Anti-ban jitter & exponential backoff included.
"""

import os
import sys
import json
import time
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

# التصنيفات الشاملة التي تغطي المتجر بالكامل بدون حصر
CORE_CATEGORIES = [
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

# معايير التنبيهات ونسب الخصم والوفر المالي
PRIORITY_1_DISCOUNT = float(os.environ.get("PRIORITY_1_DISCOUNT", "80"))  # خصم 80% فأعلى
PRIORITY_1_MIN_SAVING = float(os.environ.get("PRIORITY_1_MIN_SAVING", "15")) # وفر لا يقل عن 15 ريال

PRIORITY_2_DISCOUNT = float(os.environ.get("PRIORITY_2_DISCOUNT", "65"))  # خصم 65% فأعلى
PRIORITY_2_MIN_SAVING = float(os.environ.get("PRIORITY_2_MIN_SAVING", "10")) # وفر لا يقل عن 10 ريال

MIN_CYCLE_SLEEP = int(os.environ.get("MIN_CYCLE_SLEEP", "600"))
MAX_CYCLE_SLEEP = int(os.environ.get("MAX_CYCLE_SLEEP", "900"))
STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
RESEND_AFTER_SECONDS = 60 * 60 * 20  # 20 ساعة قبل إعادة إرسال نفس السلعة الثابتة

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("noon-watcher")

# ----------------------------- CORE FUNCTIONS ----------------------------- #

def clean_md(text: str) -> str:
    for ch in ["*", "_", "`", "[", "]"]:
        text = text.replace(ch, "")
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
        log.warning(f"Failed to save seen state: {e}")

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials missing, printing alert to stdout:")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
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
        sku = product.get("sku", "")
        title = product.get("title", "منتج")
        brand = product.get("brand", "")
        size_info = product.get("sizeInfo", "")
        price = product.get("price")
        offer_price = product.get("offerPrice")

        if (
            isinstance(price, (int, float))
            and isinstance(offer_price, (int, float))
            and price > offer_price > 0
        ):
            discount = ((price - offer_price) / price) * 100
            savings = price - offer_price
            deals.append({
                "id": sku,
                "title": f"{brand} {title}".strip(),
                "size": size_info,
                "current_price": offer_price,
                "original_price": price,
                "discount": discount,
                "savings": savings,
                "keyword": keyword,
            })

        # فحص الخيارات المتفرعة من المنتج إن وجدت
        vbs = product.get("variantsBottomSheet") or {}
        for variant in vbs.get("variants", []) or []:
            v_price = variant.get("price")
            v_striked = variant.get("strikedPrice")
            v_sku = variant.get("sku", sku)

            if (
                isinstance(v_price, (int, float))
                and isinstance(v_striked, (int, float))
                and v_striked > v_price > 0
            ):
                v_discount = ((v_striked - v_price) / v_striked) * 100
                v_savings = v_striked - v_price
                deals.append({
                    "id": v_sku,
                    "title": f"{brand} {title} ({variant.get('qtyText', '')})".strip(),
                    "size": variant.get("title", size_info),
                    "current_price": v_price,
                    "original_price": v_striked,
                    "discount": v_discount,
                    "savings": v_savings,
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
    # الفرز المباشر بنسبة التخفيض وجلب أكبر عدد ممكن
    params = {
        "q": category,
        "sort[by]": "discount",
        "sort[dir]": "desc",
        "limit": 50,
        "page": 1
    }
    try:
        resp = session.get(API_ENDPOINT, params=params, timeout=20)
        if resp.status_code == 429:
            log.warning("Rate limit detected! Cooling down for 30s...")
            time.sleep(30)
            return []
        if resp.status_code != 200:
            return []
        return extract_deals_from_response(resp.json(), category)
    except Exception as e:
        log.warning(f"Error sweeping category '{category}': {e}")
        return []

def format_deal_entry(deal: dict, is_increased: bool) -> str:
    title_safe = clean_md(deal['title'])
    size_str = f" ({clean_md(deal['size'])})" if deal['size'] else ""
    # رابط مباشر للمنتج
    noon_link = f"https://www.noon.com/saudi-ar/{deal['id']}/p/"
    inc_tag = " 📈 (الخصم ارتفع)" if is_increased else ""
    
    return (
        f"• [{title_safe}{size_str}]({noon_link}){inc_tag}\n"
        f"   └ 💰 `{deal['current_price']:.2f} ر.س` ~{deal['original_price']:.2f}~ "
        f"| وفر: *{deal['savings']:.1f} ر.س* (-{deal['discount']:.0f}%)\n"
    )

def dispatch_digest(p1: list, p2: list):
    sections = []

    if p1:
        p1.sort(key=lambda x: (x[0]["discount"], x[0]["savings"]), reverse=True)
        body = [f"🚨 *أخطاء تسعيرية وصفقات استثنائية (Priority 1)*:"]
        for d, inc in p1:
            body.append(format_deal_entry(d, inc))
        sections.append("\n".join(body))

    if p2:
        p2.sort(key=lambda x: (x[0]["discount"], x[0]["savings"]), reverse=True)
        body = [f"⚡ *عروض وتخفيضات قوية (Priority 2)*:"]
        for d, inc in p2:
            body.append(format_deal_entry(d, inc))
        sections.append("\n".join(body))

    if not sections:
        return

    full_message = f"🛒 *رادار صفقات نون مينتس — {LOCATION_LABEL}*\n" + "—" * 20 + "\n\n" + "\n\n".join(sections)

    # تجزئة الرسائل الطويلة لتفادي حدود تيليجرام
    if len(full_message) <= 3800:
        send_telegram(full_message)
    else:
        chunk = ""
        for line in full_message.split("\n"):
            if len(chunk) + len(line) + 1 > 3800:
                send_telegram(chunk)
                chunk = line + "\n"
                time.sleep(1)
            else:
                chunk += line + "\n"
        if chunk:
            send_telegram(chunk)

def run_one_cycle(session: requests.Session, seen: dict) -> int:
    now = time.time()
    p1_list = []
    p2_list = []
    total_captured = 0

    log.info(f"Initiating full sweep across {len(CORE_CATEGORIES)} departments...")

    for cat in CORE_CATEGORIES:
        log.info(f"🔎 Sweeping Department: {cat}")
        deals = search_department(session, cat)

        for deal in deals:
            disc = deal["discount"]
            save_val = deal["savings"]

            # تحديد الأولوية مع شرط الوفر المالي الأدنى
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

                # تخطي إذا كان مرسلاً حديثاً ولم يزد الخصم
                if (now - last_time) < RESEND_AFTER_SECONDS and disc <= last_disc:
                    continue

                if (now - last_time) < RESEND_AFTER_SECONDS and disc > last_disc:
                    is_increased = True

            seen[did] = {"time": now, "discount": disc}
            total_captured += 1

            if is_p1:
                p1_list.append((deal, is_increased))
            else:
                p2_list.append((deal, is_increased))

        # تأخير طبيعي ذكي بين الأقسام لتفادي الحظر
        time.sleep(random.uniform(1.5, 3.0))

    if p1_list or p2_list:
        dispatch_digest(p1_list, p2_list)

    save_seen(seen)
    return total_captured

SINGLE_CYCLE = os.environ.get("SINGLE_CYCLE", "false").lower() == "true"

def main():
    log.info("🚀 Noon Minutes Smart Engine Active.")
    session = build_session()
    seen = load_seen()

    if SINGLE_CYCLE:
        sent = run_one_cycle(session, seen)
        log.info(f"✅ Sweep complete. New alerts sent: {sent}")
        return

    while True:
        try:
            sent = run_one_cycle(session, seen)
            log.info(f"✅ Sweep finished. New alerts sent: {sent}")
        except Exception as e:
            log.error(f"⚠️ Error during execution: {e}")

        sleep_time = random.uniform(MIN_CYCLE_SLEEP, MAX_CYCLE_SLEEP)
        log.info(f"😴 Sleeping for {sleep_time/60:.1f} minutes...")
        time.sleep(sleep_time)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Process terminated.")
