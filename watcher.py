#!/usr/bin/env python3
"""
Noon Minutes Deal Hunter — v7.0
--------------------------------

Goal:
    Find unusually strong Noon Minutes discounts, not merely high percentages.

Main improvements:
    - Pagination
    - Retry / exponential backoff
    - Smart price history
    - Price anomaly detection
    - Deal scoring
    - SKU/variant deduplication
    - Price-drop alerts
    - Telegram batching
    - Telegram retry
    - Stable fallback IDs
    - State cleanup
    - Better filtering
    - Safer HTML
"""

import os
import sys
import json
import time
import html
import random
import hashlib
import logging
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# CONFIG
# ============================================================

API_ENDPOINT = "https://minutes.noon.com/_svc/catalog/search"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LOCATION_LABEL = os.environ.get(
    "LOCATION_LABEL",
    "حي طويق، الرياض"
)

NOON_COOKIES = os.environ.get("NOON_COOKIES", "")


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Cache-Control": "no-cache, max-age=0, must-revalidate, no-store",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Mobile/15E148 Safari/604.1"
    ),

    "x-platform": os.environ.get("X_PLATFORM", "mweb"),
    "x-cms": os.environ.get("X_CMS", "v2"),
    "x-build": os.environ.get("X_BUILD", "17801"),
    "x-experience": os.environ.get("X_EXPERIENCE", "nooninstant"),
    "x-mp": os.environ.get("X_MP", "nooninstant"),

    "x-visitor-id": os.environ.get(
        "X_VISITOR_ID",
        "ae53dba7-0002-4b38-8c9e-b7fc88fabc35"
    ),

    "x-locale": os.environ.get("X_LOCALE", "ar-sa"),

    "x-lat": os.environ.get(
        "X_LAT",
        "245868084"
    ),

    "x-lng": os.environ.get(
        "X_LNG",
        "465789328"
    ),

    "x-border-enabled": "true",

    "x-ecom-zonecode": os.environ.get(
        "X_ECOM_ZONECODE",
        "SA-RUH-S17"
    ),

    "x-mp-country": "sa",

    "x-nooninstant-zonecode": os.environ.get(
        "X_NOONINSTANT_ZONECODE",
        "W00055702A"
    ),

    "x-rocket-enabled": "true",

    "x-services-zonecode": os.environ.get(
        "X_SERVICES_ZONECODE",
        "SERVICES-SA-RIYADH"
    ),
}


# ------------------------------------------------------------
# SCANNING
# ------------------------------------------------------------

CORE_CATEGORIES = [
    "عروض",
    "تخفيضات",
    "سوبرماركت",
    "بروتين",
    "ألبان وبيض",
    "مخبوزات وحلويات",
    "لحوم ودواجن",
    "مجمدات",
    "مشروبات وقهوة",
    "سناكات ومكسرات",
    "عناية شخصية",
    "نظافة ومنزل",
    "إلكترونيات وشواحن",
    "صيدلية وصحة",
    "خضار وفواكه",
    "فطور ومربيات",
]


# ------------------------------------------------------------
# DEAL FILTERS
# ------------------------------------------------------------

PRIORITY_1_DISCOUNT = float(
    os.environ.get("PRIORITY_1_DISCOUNT", "80")
)

PRIORITY_1_MIN_SAVING = float(
    os.environ.get("PRIORITY_1_MIN_SAVING", "15")
)

PRIORITY_2_DISCOUNT = float(
    os.environ.get("PRIORITY_2_DISCOUNT", "65")
)

PRIORITY_2_MIN_SAVING = float(
    os.environ.get("PRIORITY_2_MIN_SAVING", "10")
)

# Very cheap products aren't interesting even if discount % is huge.
MIN_CURRENT_PRICE = float(
    os.environ.get("MIN_CURRENT_PRICE", "2")
)

# A huge absolute saving is always interesting.
ABSOLUTE_SAVE_PRIORITY = float(
    os.environ.get("ABSOLUTE_SAVE_PRIORITY", "40")
)


# ------------------------------------------------------------
# HISTORY / ALERTING
# ------------------------------------------------------------

RESEND_AFTER_SECONDS = int(
    os.environ.get(
        "RESEND_AFTER_SECONDS",
        str(60 * 60 * 20)
    )
)

PRICE_DROP_ALERT_PERCENT = float(
    os.environ.get("PRICE_DROP_ALERT_PERCENT", "12")
)

HISTORY_WINDOW = int(
    os.environ.get("HISTORY_WINDOW", "30")
)

STALE_STATE_SECONDS = int(
    os.environ.get(
        "STALE_STATE_SECONDS",
        str(60 * 60 * 24 * 30)
    )
)


# ------------------------------------------------------------
# PERFORMANCE
# ------------------------------------------------------------

MIN_CYCLE_SLEEP = int(
    os.environ.get("MIN_CYCLE_SLEEP", "600")
)

MAX_CYCLE_SLEEP = int(
    os.environ.get("MAX_CYCLE_SLEEP", "900")
)

MIN_REQUEST_DELAY = float(
    os.environ.get("MIN_REQUEST_DELAY", "1.2")
)

MAX_REQUEST_DELAY = float(
    os.environ.get("MAX_REQUEST_DELAY", "2.5")
)

PAGE_SIZE = int(
    os.environ.get("PAGE_SIZE", "50")
)

MAX_PAGES = int(
    os.environ.get("MAX_PAGES", "10")
)

MAX_TELEGRAM_DEALS = int(
    os.environ.get("MAX_TELEGRAM_DEALS", "35")
)


# ------------------------------------------------------------
# FILES
# ------------------------------------------------------------

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

STATE_FILE = os.path.join(
    BASE_DIR,
    "seen.json"
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

log = logging.getLogger("noon-deal-hunter")


# ============================================================
# STATE
# ============================================================

def load_seen() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception as e:
        log.warning(
            f"Could not load state: {e}"
        )

    return {}


def save_seen(seen: Dict[str, Any]) -> None:
    temp_file = STATE_FILE + ".tmp"

    try:
        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                seen,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            STATE_FILE
        )

    except Exception as e:
        log.warning(
            f"Could not save state: {e}"
        )


def cleanup_seen(seen: Dict[str, Any]) -> None:
    now = time.time()

    dead = []

    for key, state in seen.items():

        if not isinstance(state, dict):
            continue

        last_seen = state.get(
            "last_seen",
            state.get("time", 0)
        )

        if (
            isinstance(last_seen, (int, float))
            and now - last_seen > STALE_STATE_SECONDS
        ):
            dead.append(key)

    for key in dead:
        del seen[key]

    if dead:
        log.info(
            f"🧹 Removed {len(dead)} stale products from memory."
        )


# ============================================================
# HELPERS
# ============================================================

def stable_id(text: str) -> str:
    """
    Stable across Python restarts.
    Unlike hash(), this doesn't change between processes.
    """
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()[:24]


def safe_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)

    try:
        return float(value)
    except Exception:
        return None


def normalize_text(value: Any) -> str:
    return " ".join(
        str(value or "").split()
    ).strip()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_html(
    message_html: str,
    retries: int = 3
) -> bool:

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning(
            "Telegram credentials missing."
        )
        print(message_html)
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    for attempt in range(retries):

        try:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message_html,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )

            if response.status_code == 200:
                return True

            if response.status_code == 429:
                retry_after = 5

                try:
                    retry_after = int(
                        response.json()
                        .get("parameters", {})
                        .get("retry_after", 5)
                    )
                except Exception:
                    pass

                log.warning(
                    f"Telegram rate limit. Sleeping "
                    f"{retry_after}s."
                )

                time.sleep(retry_after)
                continue

            log.warning(
                f"Telegram error "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            )

        except Exception as e:
            log.warning(
                f"Telegram network error: {e}"
            )

        time.sleep(
            2 ** attempt
        )

    return False


def send_large_message(message: str) -> None:

    MAX_LENGTH = 3800

    if len(message) <= MAX_LENGTH:
        send_telegram_html(message)
        return

    lines = message.split("\n")
    chunk = ""

    for line in lines:

        if len(chunk) + len(line) + 1 > MAX_LENGTH:

            if chunk.strip():
                send_telegram_html(chunk)

            chunk = line + "\n"

            time.sleep(1.2)

        else:
            chunk += line + "\n"

    if chunk.strip():
        send_telegram_html(chunk)


# ============================================================
# SESSION
# ============================================================

def build_session() -> requests.Session:

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    if NOON_COOKIES:

        for pair in NOON_COOKIES.split(";"):

            if "=" not in pair:
                continue

            key, value = pair.strip().split(
                "=",
                1
            )

            session.cookies.set(
                key,
                value
            )

    return session


# ============================================================
# HTTP SEARCH
# ============================================================

def request_page(
    session: requests.Session,
    category: str,
    page: int
) -> Optional[Dict[str, Any]]:

    params = {
        "q": category,
        "sort[by]": "discount",
        "sort[dir]": "desc",
        "limit": PAGE_SIZE,
        "page": page,
    }

    for attempt in range(4):

        try:

            response = session.get(
                API_ENDPOINT,
                params=params,
                timeout=20,
            )

            status = response.status_code

            if status == 200:

                try:
                    return response.json()
                except Exception as e:

                    log.warning(
                        f"Invalid JSON for "
                        f"'{category}' page {page}: "
                        f"{e}"
                    )

                    return None

            if status == 429:

                wait = min(
                    60,
                    5 * (2 ** attempt)
                )

                log.warning(
                    f"429 for '{category}' "
                    f"page {page}. "
                    f"Sleeping {wait}s."
                )

                time.sleep(wait)
                continue

            if status in (500, 502, 503, 504):

                wait = min(
                    30,
                    2 * (2 ** attempt)
                )

                log.warning(
                    f"{status} for '{category}' "
                    f"page {page}. "
                    f"Retrying in {wait}s."
                )

                time.sleep(wait)
                continue

            log.warning(
                f"Unexpected HTTP {status} "
                f"for '{category}' page {page}"
            )

            return None

        except requests.RequestException as e:

            wait = min(
                30,
                2 * (2 ** attempt)
            )

            log.warning(
                f"Network error for "
                f"'{category}' page {page}: "
                f"{e}. Retry in {wait}s."
            )

            time.sleep(wait)

    return None


# ============================================================
# PRODUCT EXTRACTION
# ============================================================

def extract_deals(
    data: Dict[str, Any],
    keyword: str
) -> List[Dict[str, Any]]:

    deals = []

    def handle_product(product: Dict[str, Any]):

        sku = normalize_text(
            product.get("sku")
        )

        title = normalize_text(
            product.get("title")
            or "منتج"
        )

        brand = normalize_text(
            product.get("brand")
        )

        size_info = normalize_text(
            product.get("sizeInfo")
        )

        full_title = (
            f"{brand} {title}"
            if brand
            else title
        ).strip()

        price = safe_float(
            product.get("price")
        )

        offer_price = safe_float(
            product.get("offerPrice")
        )

        if (
            price is not None
            and offer_price is not None
            and price > offer_price > 0
        ):

            discount = (
                (price - offer_price)
                / price
                * 100
            )

            savings = (
                price - offer_price
            )

            product_id = (
                sku
                or stable_id(
                    full_title
                    + "|"
                    + size_info
                )
            )

            deals.append({
                "id": product_id,
                "sku": sku,
                "title": full_title,
                "size": size_info,
                "current_price": offer_price,
                "original_price": price,
                "discount": discount,
                "savings": savings,
                "keyword": keyword,
            })

        # ----------------------------
        # Variants
        # ----------------------------

        vbs = (
            product.get(
                "variantsBottomSheet"
            )
            or {}
        )

        variants = (
            vbs.get("variants")
            or []
        )

        for variant in variants:

            v_price = safe_float(
                variant.get("price")
            )

            v_striked = safe_float(
                variant.get("strikedPrice")
            )

            if not (
                v_price is not None
                and v_striked is not None
                and v_striked > v_price > 0
            ):
                continue

            v_sku = normalize_text(
                variant.get("sku")
            ) or sku

            qty = normalize_text(
                variant.get("qtyText")
            )

            variant_title = normalize_text(
                variant.get("title")
            )

            display_title = full_title

            if qty:
                display_title += f" ({qty})"

            v_discount = safe_float(
                variant.get("discountPercent")
            )

            if v_discount is None:

                v_discount = (
                    (v_striked - v_price)
                    / v_striked
                    * 100
                )

            variant_id = (
                v_sku
                or stable_id(
                    display_title
                    + "|"
                    + variant_title
                )
            )

            deals.append({
                "id": variant_id,
                "sku": v_sku,
                "title": display_title,
                "size": variant_title or size_info,
                "current_price": v_price,
                "original_price": v_striked,
                "discount": v_discount,
                "savings": v_striked - v_price,
                "keyword": keyword,
            })

    def walk(node: Any):

        if isinstance(node, dict):

            if (
                node.get("type")
                == "instantProductBox"
                and isinstance(
                    node.get("product"),
                    dict
                )
            ):
                handle_product(
                    node["product"]
                )

            for value in node.values():
                walk(value)

        elif isinstance(node, list):

            for item in node:
                walk(item)

    walk(data)

    return deals


# ============================================================
# PAGINATED SEARCH
# ============================================================

def search_department(
    session: requests.Session,
    category: str
) -> List[Dict[str, Any]]:

    all_deals = []

    log.info(
        f"🔎 Scanning: {category}"
    )

    for page in range(1, MAX_PAGES + 1):

        data = request_page(
            session,
            category,
            page
        )

        if not data:
            break

        page_deals = extract_deals(
            data,
            category
        )

        if not page_deals:
            break

        all_deals.extend(
            page_deals
        )

        log.info(
            f"   page {page}: "
            f"{len(page_deals)} discounted items"
        )

        # Usually indicates the final page.
        if len(page_deals) < PAGE_SIZE:
            break

        time.sleep(
            random.uniform(
                MIN_REQUEST_DELAY,
                MAX_REQUEST_DELAY
            )
        )

    return all_deals


# ============================================================
# PRICE HISTORY
# ============================================================

def update_history(
    state: Dict[str, Any],
    deal: Dict[str, Any],
    now: float
) -> Dict[str, Any]:

    item_id = deal["id"]

    old = state.get(
        item_id,
        {}
    )

    if not isinstance(old, dict):
        old = {}

    history = old.get(
        "history",
        []
    )

    if not isinstance(history, list):
        history = []

    current_price = deal[
        "current_price"
    ]

    # Don't add identical consecutive prices.
    if (
        not history
        or history[-1].get("price")
        != current_price
    ):
        history.append({
            "time": now,
            "price": current_price,
            "discount": deal["discount"],
        })

    history = history[-HISTORY_WINDOW:]

    prices = [
        x["price"]
        for x in history
        if isinstance(
            x.get("price"),
            (int, float)
        )
        and x["price"] > 0
    ]

    average_price = (
        sum(prices) / len(prices)
        if prices
        else current_price
    )

    historical_low = (
        min(prices)
        if prices
        else current_price
    )

    previous_price = old.get(
        "current_price"
    )

    price_drop_percent = 0.0

    if (
        isinstance(
            previous_price,
            (int, float)
        )
        and previous_price > 0
        and current_price < previous_price
    ):
        price_drop_percent = (
            (previous_price - current_price)
            / previous_price
            * 100
        )

    new_state = {
        "last_seen": now,
        "last_sent": old.get(
            "last_sent",
            0
        ),
        "current_price": current_price,
        "discount": deal["discount"],
        "original_price": deal[
            "original_price"
        ],
        "average_price": average_price,
        "historical_low": historical_low,
        "price_drop_percent": price_drop_percent,
        "history": history,
    }

    return new_state


# ============================================================
# DEAL SCORING
# ============================================================

def calculate_deal_score(
    deal: Dict[str, Any],
    state: Dict[str, Any]
) -> Tuple[float, List[str]]:

    discount = deal["discount"]
    savings = deal["savings"]
    current = deal["current_price"]

    score = 0.0
    reasons = []

    # --------------------------------
    # Discount percentage
    # --------------------------------

    if discount >= 90:
        score += 55
        reasons.append("خصم 90%+")

    elif discount >= 80:
        score += 40
        reasons.append("خصم 80%+")

    elif discount >= 70:
        score += 27
        reasons.append("خصم 70%+")

    elif discount >= 60:
        score += 18
        reasons.append("خصم 60%+")

    # --------------------------------
    # Absolute saving
    # --------------------------------

    if savings >= 100:
        score += 35
        reasons.append("وفر 100+ ريال")

    elif savings >= 50:
        score += 25
        reasons.append("وفر 50+ ريال")

    elif savings >= 40:
        score += 20
        reasons.append("وفر 40+ ريال")

    elif savings >= 25:
        score += 12
        reasons.append("وفر 25+ ريال")

    elif savings >= 15:
        score += 6

    # --------------------------------
    # Historical price anomaly
    # --------------------------------

    average_price = safe_float(
        state.get("average_price")
    )

    if (
        average_price
        and average_price > current
    ):

        below_average = (
            (average_price - current)
            / average_price
            * 100
        )

        if below_average >= 50:
            score += 45
            reasons.append(
                "أقل من متوسطه التاريخي 50%+"
            )

        elif below_average >= 35:
            score += 30
            reasons.append(
                "أقل من متوسطه التاريخي 35%+"
            )

        elif below_average >= 25:
            score += 18
            reasons.append(
                "أقل من متوسطه التاريخي 25%+"
            )

        elif below_average >= 15:
            score += 8

    # --------------------------------
    # New price drop
    # --------------------------------

    price_drop = safe_float(
        state.get("price_drop_percent")
    ) or 0

    if price_drop >= 30:
        score += 35
        reasons.append(
            "هبوط سعري 30%+"
        )

    elif price_drop >= 20:
        score += 25
        reasons.append(
            "هبوط سعري 20%+"
        )

    elif price_drop >= 12:
        score += 12
        reasons.append(
            "هبوط سعري جديد"
        )

    # --------------------------------
    # Cheap-item penalty
    # --------------------------------

    if current < 5:
        score -= 15

    elif current < 10:
        score -= 5

    return score, reasons


# ============================================================
# PRIORITY
# ============================================================

def classify_deal(
    deal: Dict[str, Any],
    score: float
) -> Optional[str]:

    discount = deal["discount"]
    savings = deal["savings"]
    price = deal["current_price"]

    if price < MIN_CURRENT_PRICE:
        return None

    # Absolute monster saving.
    if savings >= ABSOLUTE_SAVE_PRIORITY:
        return "P1"

    # High discount + meaningful saving.
    if (
        discount >= PRIORITY_1_DISCOUNT
        and savings >= PRIORITY_1_MIN_SAVING
    ):
        return "P1"

    # Score-based anomaly.
    if score >= 70:
        return "P1"

    # P2.
    if (
        discount >= PRIORITY_2_DISCOUNT
        and savings >= PRIORITY_2_MIN_SAVING
    ):
        return "P2"

    if score >= 45:
        return "P2"

    return None


# ============================================================
# ALERT DECISION
# ============================================================

def should_alert(
    deal: Dict[str, Any],
    state: Dict[str, Any],
    now: float
) -> Tuple[bool, bool]:

    last_sent = safe_float(
        state.get("last_sent")
    ) or 0

    last_discount = safe_float(
        state.get("last_sent_discount")
    ) or 0

    current_discount = deal[
        "discount"
    ]

    current_price = deal[
        "current_price"
    ]

    last_sent_price = safe_float(
        state.get("last_sent_price")
    )

    # Never alerted before.
    if last_sent <= 0:
        return True, False

    time_since_alert = (
        now - last_sent
    )

    # Stronger discount since last alert.
    if (
        current_discount
        > last_discount + 2
    ):
        return True, True

    # Price dropped significantly since
    # last alert.
    if (
        last_sent_price
        and last_sent_price > current_price
    ):

        drop = (
            (last_sent_price - current_price)
            / last_sent_price
            * 100
        )

        if drop >= PRICE_DROP_ALERT_PERCENT:
            return True, True

    # Normal cooldown.
    if (
        time_since_alert
        < RESEND_AFTER_SECONDS
    ):
        return False, False

    # After cooldown, only alert if
    # it is still a qualifying deal.
    return True, False


# ============================================================
# MESSAGE
# ============================================================

def build_deal_html(
    deal: Dict[str, Any],
    priority: str,
    score: float,
    reasons: List[str],
    increased: bool
) -> str:

    title = html.escape(
        deal["title"]
    )

    size = html.escape(
        deal.get("size", "")
    )

    sku = html.escape(
        deal.get("sku", "")
    )

    current = deal[
        "current_price"
    ]

    original = deal[
        "original_price"
    ]

    savings = deal[
        "savings"
    ]

    discount = deal[
        "discount"
    ]

    query = (
        deal["sku"]
        if deal["sku"]
        else deal["title"]
    )

    link = (
        "https://minutes.noon.com/"
        "saudi-ar/search/?q="
        + quote(query)
    )

    priority_icon = (
        "🚨"
        if priority == "P1"
        else "🔥"
    )

    increased_text = (
        " 📈 <b>تطور أفضل من آخر تنبيه</b>"
        if increased
        else ""
    )

    reason_text = (
        " • ".join(
            html.escape(str(x))
            for x in reasons[:4]
        )
    )

    size_text = (
        f" — {size}"
        if size
        else ""
    )

    sku_text = (
        f"\n   └ SKU: <code>{sku}</code>"
        if sku
        else ""
    )

    return (
        f"{priority_icon} "
        f"<b>{priority}</b>{increased_text}\n"
        f"<a href=\"{link}\">"
        f"<b>{title}</b>"
        f"</a>{size_text}\n"
        f"💰 <b>{current:.2f} ر.س</b> "
        f"<s>{original:.2f}</s>\n"
        f"📉 خصم: <b>{discount:.0f}%</b>"
        f" | وفر: <b>{savings:.1f} ر.س</b>\n"
        f"🧠 Deal Score: "
        f"<b>{score:.0f}</b>\n"
        f"🎯 {reason_text}"
        f"{sku_text}\n"
    )


# ============================================================
# DIGEST
# ============================================================

def dispatch_digest(
    alerts: List[Dict[str, Any]]
) -> None:

    if not alerts:
        return

    # Highest score first.
    alerts.sort(
        key=lambda x: (
            x["score"],
            x["deal"]["savings"],
            x["deal"]["discount"],
        ),
        reverse=True
    )

    alerts = alerts[
        :MAX_TELEGRAM_DEALS
    ]

    p1 = [
        x for x in alerts
        if x["priority"] == "P1"
    ]

    p2 = [
        x for x in alerts
        if x["priority"] == "P2"
    ]

    sections = []

    header = (
        f"🛒 <b>NOON MINUTES DEAL HUNTER</b>\n"
        f"📍 {html.escape(LOCATION_LABEL)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if p1:

        body = [
            "🚨 <b>صفقات استثنائية / احتمال خطأ سعري</b>\n"
        ]

        for item in p1:
            body.append(
                build_deal_html(
                    item["deal"],
                    item["priority"],
                    item["score"],
                    item["reasons"],
                    item["increased"],
                )
            )

        sections.append(
            "\n".join(body)
        )

    if p2:

        body = [
            "🔥 <b>عروض قوية</b>\n"
        ]

        for item in p2:
            body.append(
                build_deal_html(
                    item["deal"],
                    item["priority"],
                    item["score"],
                    item["reasons"],
                    item["increased"],
                )
            )

        sections.append(
            "\n".join(body)
        )

    message = (
        header
        + "\n\n".join(sections)
    )

    send_large_message(
        message
    )


# ============================================================
# ONE CYCLE
# ============================================================

def run_one_cycle(
    session: requests.Session,
    seen: Dict[str, Any]
) -> int:

    now = time.time()

    cleanup_seen(
        seen
    )

    alerts = []

    # --------------------------------------------------------
    # Scan all departments
    # --------------------------------------------------------

    for category in CORE_CATEGORIES:

        deals = search_department(
            session,
            category
        )

        # ----------------------------------------------------
        # Deduplicate products inside this department.
        # Keep the strongest version.
        # ----------------------------------------------------

        unique_deals = {}

        for deal in deals:

            did = deal["id"]

            old = unique_deals.get(
                did
            )

            if (
                old is None
                or deal["discount"]
                > old["discount"]
            ):
                unique_deals[did] = deal

        # ----------------------------------------------------
        # Evaluate
        # ----------------------------------------------------

        for deal in unique_deals.values():

            did = deal["id"]

            old_state = seen.get(
                did,
                {}
            )

            if not isinstance(
                old_state,
                dict
            ):
                old_state = {}

            # Update history BEFORE scoring,
            # so the current price is part
            # of the current state.
            new_state = update_history(
                seen,
                deal,
                now
            )

            score, reasons = calculate_deal_score(
                deal,
                new_state
            )

            priority = classify_deal(
                deal,
                score
            )

            if not priority:
                seen[did] = new_state
                continue

            alert, increased = should_alert(
                deal,
                old_state,
                now
            )

            if not alert:
                seen[did] = new_state
                continue

            alerts.append({
                "deal": deal,
                "priority": priority,
                "score": score,
                "reasons": reasons,
                "increased": increased,
            })

            # ------------------------------------------------
            # Mark sent.
            # ------------------------------------------------

            new_state[
                "last_sent"
            ] = now

            new_state[
                "last_sent_discount"
            ] = deal["discount"]

            new_state[
                "last_sent_price"
            ] = deal["current_price"]

            new_state[
                "last_priority"
            ] = priority

            seen[did] = new_state

        time.sleep(
            random.uniform(
                MIN_REQUEST_DELAY,
                MAX_REQUEST_DELAY
            )
        )

    # --------------------------------------------------------
    # Send one digest.
    # --------------------------------------------------------

    if alerts:

        log.info(
            f"🚨 {len(alerts)} qualifying deals found."
        )

        dispatch_digest(
            alerts
        )

    else:

        log.info(
            "😴 No qualifying deals this cycle."
        )

    save_seen(
        seen
    )

    return len(alerts)


# ============================================================
# MAIN
# ============================================================

SINGLE_CYCLE = (
    os.environ
    .get(
        "SINGLE_CYCLE",
        "false"
    )
    .lower()
    == "true"
)


def main():

    log.info(
        "🚀 Noon Minutes Deal Hunter v7.0 ACTIVE"
    )

    session = build_session()
    seen = load_seen()

    if not TELEGRAM_BOT_TOKEN:
        log.warning(
            "⚠️ TELEGRAM_BOT_TOKEN missing."
        )

    if not TELEGRAM_CHAT_ID:
        log.warning(
            "⚠️ TELEGRAM_CHAT_ID missing."
        )

    if SINGLE_CYCLE:

        count = run_one_cycle(
            session,
            seen
        )

        log.info(
            f"✅ Single cycle complete: "
            f"{count} alerts."
        )

        return

    while True:

        try:

            count = run_one_cycle(
                session,
                seen
            )

            log.info(
                f"✅ Cycle complete: "
                f"{count} alerts."
            )

        except Exception as e:

            log.exception(
                f"❌ Cycle crashed: {e}"
            )

        sleep_seconds = random.uniform(
            MIN_CYCLE_SLEEP,
            MAX_CYCLE_SLEEP
        )

        log.info(
            f"😴 Sleeping "
            f"{sleep_seconds / 60:.1f} minutes..."
        )

        time.sleep(
            sleep_seconds
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        log.info(
            "🛑 Stopped manually."
        )
