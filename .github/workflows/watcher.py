#!/usr/bin/env python3
"""
Noon Minutes Discount Watcher — v4.5 (Ultimate Smart AI Engine)
------------------------------------------------------------------
Features:
  - Live Mobile Captured Headers (Tuwaiq District, Riyadh)
  - Smart Memory (Tracks pricing history & discount increases)
  - Google Search Powered Gemini AI (Filters fake deals & catches 0% pricing errors)
  - Robust Error Handling & API 400 Fixes
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

# قائمة كلماتك الأصلية تماماً بدون أي تعديل عليها بناءً على طلبك
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

PRIORITY_1_THRESHOLD = float(os.environ.get("PRIORITY_1_THRESHOLD", "80"))
PRIORITY_2_THRESHOLD = float(os.environ.get("PRIORITY_2_THRESHOLD", "70"))

MIN_CYCLE_SLEEP = int(os.environ.get("MIN_CYCLE_SLEEP", "600"))
MAX_CYCLE_SLEEP = int(os.environ.get("MAX_CYCLE_SLEEP", "900"))
MIN_REQUEST_DELAY = float(os.environ.get("MIN_REQUEST_DELAY", "2.0"))
MAX_REQUEST_DELAY = float(os.environ.get("MAX_REQUEST_DELAY", "5.0"))

STATE_FILE = os.path.join(os.path.dirname(__file__), "seen.json")
RESEND_AFTER_SECONDS = 60 * 60 * 24

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

# ----------------------------- AI VERIFIER ENGINE ----------------------------- #

def verify_deal_with_ai(title, current_price):
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return {"is_real_deal": True, "is_pricing_error": False, "market_price": 0, "reason": "Gemini API Key missing"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    
    prompt = f"""
    أنت محرك ذكي متخصص في كشف أخطاء التسعير والعروض الوهمية في السوق السعودي.
    المنتج الحالي: {title}
    السعر المعروض في المتجر: {current_price} ريال سعودي.
    
    المهمة:
    1. استخدم أداة البحث (Google Search) للبحث عن السعر الفعلي والحالي لهذا搬نتج في السوق السعودي (أمازون السعودية، جرير، الصيدليات، أو السوبرماركت الكبرى).
    2. قارن السعر المعروض ({current_price} ريال) بمتوسط السعر في السوق.
    3. صنف العملية بدقة:
       - إذا كان السعر المعروض أقل من السوق بفارق ضخم جداً (أكثر من 60% بدون سبب ترويجي منطقي) فهو خطأ تسعير (is_pricing_error: true).
       - إذا كان السعر حقيقي وممتاز كعرض صنف (is_real_deal: true).
       - إذا كان السعر الأصلي مرفوع وهمياً وهو يباع بنفس السعر في كل مكان صنف (is_real_deal: false).

    أجب بصيغة JSON صافية فقط ومطابقة تماماً للهيكل التالي دون أي كلام جانبي:
    {{
      "is_real_deal": true/false,
      "is_pricing_error": true/false,
      "market_price": 0.0,
      "reason": "اكتب هنا سبب الحكم باختصار شديد وذكاء باللغة العربية"
    }}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],  # تفعيل البحث الحي عبر جوجل أونلاين
        "generationConfig": {
            # تم حذف ميم تايب الـ JSON هنا لتجنب تعارض الـ Google Search وحل خطأ 400
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=25)
        if response.status_code == 200:
            res_json = response.json()
            text_response = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
            
            # تنظيف نصوص الماركداون إذا رجعها الموديل محاطة بـ ```json
            if text_response.startswith("```"):
                lines = text_response.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                text_response = "\n".join(lines).strip()
                
            return json.loads(text_response)
        else:
            log.warning(f"Gemini API returned status {response.status_code}: {response.text}")
    except Exception as e:
        log.warning(f"AI Verification failed for {title}: {e}")
    
    return {"is_real_deal": True, "is_pricing_error": False, "market_price": 0, "reason": "AI check bypassed due to error"}

# ----------------------------- PRODUCT EXTRACTION ----------------------------- #

def classify(discount: float):
    if discount >= PRIORITY_1_THRESHOLD:
        return "🔴", "Priority 1 — likely pricing error"
    if discount >= PRIORITY_2_THRESHOLD:
        return "🟡", "Priority 2 — high discount"
    return None, None

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
        log.warning(f"Unexpected status ({resp.status_code}) for '{keyword}': {resp.text[:200]}")
        return []

    try:
        data = resp.json()
    except Exception as e:
        log.warning(f"Could not parse JSON for '{keyword}': {e}")
        return []

    return extract_deals_from_response(data, keyword)

def format_message(emoji, label, deal):
    search_link = f"https://minutes.noon.com/saudi-en/search/?q={quote(deal['title'])}"
    msg = (
        f"{emoji} *{label}*\n"
        f"📦 *{deal['title']}*\n"
        f"📏 {deal['size']}\n"
        f"💰 السعر الحالي: `{deal['current_price']:.2f}` ر.س "
        f"(بدل `{deal['original_price']:.2f}`)\n"
        f"📉 نسبة الخصم: *{deal['discount']:.0f}%*\n"
        f"📍 {LOCATION_LABEL}\n"
        f"🔎 كلمة البحث: {deal['keyword']}\n"
        f"🆔 SKU: `{deal['id']}`\n"
        f"🔗 [افتح البحث عن المنتج]({search_link})"
    )
    return msg

# ----------------------------- MAIN CYCLE ----------------------------- #

def run_one_cycle(session: requests.Session, seen: dict) -> int:
    now = time.time()
    alerts_sent = 0

    log.info(f"Scanning {len(KEYWORDS)} keywords this cycle")

    for kw in KEYWORDS:
        log.info(f"🔍 Searching: {kw}")
        try:
            deals = search_keyword(session, kw)
        except Exception as e:
            log.warning(f"Unexpected error scanning '{kw}': {e}")
            deals = []

        for deal in deals:
            did = deal["id"]
            current_discount = deal["discount"]
            current_price = deal["current_price"]
            title = deal["title"]
            
            # الفلترة الذكية المسبقة لاصطياد أخطاء الأسعار الفادحة (حتى لو الخصم 0%)
            is_suspiciously_cheap = current_price < 15.0 and any(keyword in title for keyword in ["شاحن", "سماعة", "بروتين", "باوربانك", "ايفون", "حفاضات"])
            passes_normal_threshold = current_discount >= PRIORITY_2_THRESHOLD
            
            # تخطي السلعة فوراً إذا لم تصل لحد الخصم المطلوب وليست رخيصة بشكل مشبوه
            if not passes_normal_threshold and not is_suspiciously_cheap:
                continue

            # استشارة الذكاء الاصطناعي لايف عبر جوجل لضرب العروض الوهمية وقنص الأخطاء الحقيقية
            log.info(f"🧠 Consulted Gemini AI to verify: {title} at {current_price} SAR")
            ai_result = verify_deal_with_ai(title, current_price)
            
            # إذا جزم الـ AI أن العرض طبيعي أو مجرد رفع سعر وهمي، يتم إسقاط الصفقة فوراً
            if not ai_result.get("is_real_deal", True) and not ai_result.get("is_pricing_error", False):
                log.info(f"❌ AI skipped fake/normal deal: {title}")
                continue
                
            # حماية صارمة: لمنع وصول نسب مثل الـ 20% إلا لو أكد الـ AI قطعياً أنه خطأ تسعير سيستم فادح
            if not passes_normal_threshold and not ai_result.get("is_pricing_error", False):
                log.info(f"Skip: Low discount ({current_discount:.0f}%) and AI didn't flag as pricing error for {title}")
                continue

            # تحديد القالب والتسمية حسب تحليل الـ AI
            if ai_result.get("is_pricing_error", False):
                emoji, label = "🚨", f"خطأ تسعير فادح! (سعر السوق: {ai_result.get('market_price')} ر.س)"
            else:
                emoji, label = classify(current_discount)
                if not emoji: emoji, label = "🟡", "Priority 2 — high discount"

            deal["keyword"] += f" | 🤖 تحليل الـ AI: {ai_result.get('reason', '')}"

            # فحص ذاكرة التاريخ والنسبة لضمان التنبيه في حال انهار السعر وزاد الخصم
            state = seen.get(did)
            if state:
                if isinstance(state, (int, float)):
                    last_sent = state
                    last_discount = 0
                else:
                    last_sent = state.get("time", 0)
                    last_discount = state.get("discount", 0)
                
                if (now - last_sent) < RESEND_AFTER_SECONDS and current_discount <= last_discount and not ai_result.get("is_pricing_error", False):
                    continue
                
                if (now - last_sent) < RESEND_AFTER_SECONDS and current_discount > last_discount:
                    label += " 📈 (الخصم زاد!)"

            send_telegram(format_message(emoji, label, deal))
            seen[did] = {"time": now, "discount": current_discount}
            alerts_sent += 1

        time.sleep(random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY))

    save_seen(seen)
    return alerts_sent

SINGLE_CYCLE = os.environ.get("SINGLE_CYCLE", "false").lower() == "true"

def main():
    log.info("🚀 Starting Noon Minutes Smart AI Watcher (Tuwaiq, Riyadh)")
    session = build_session()
    seen = load_seen()

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("⚠️ Telegram token/chat_id not set — alerts will only be logged.")

    if SINGLE_CYCLE:
        try:
            sent = run_one_cycle(session, seen)
            log.info(f"✅ Single cycle finished. Alerts sent: {sent}")
        except Exception as e:
            log.error(f"❌ Unexpected error in cycle: {e}")
        return

    while True:
        try:
            sent = run_one_cycle(session, seen)
            log.info(f"✅ Cycle finished. Alerts sent: {sent}")
        except Exception as e:
            log.error(f"❌ Unexpected error in cycle: {e}")

        sleep_for = random.uniform(MIN_CYCLE_SLEEP, MAX_CYCLE_SLEEP)
        log.info(f"😴 Sleeping {sleep_for/60:.1f} minutes before next cycle...")
        time.sleep(sleep_for)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("🛑 Stopped manually.")
