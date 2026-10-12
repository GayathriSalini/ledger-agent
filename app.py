
import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import Flask, request, jsonify

app = Flask(__name__)

INDIA_TZ = timezone(timedelta(hours=5, minutes=30))

RATES = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.13"),
    "INR": Decimal("0.01161"),
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_orders():
    # Keep the latest updated version of each order.
    latest = {}

    with open("orders.ndjson", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            order = json.loads(line)
            order_id = order["id"]
            previous = latest.get(order_id)

            if (
                previous is None
                or parse_time(order["updated_at"])
                > parse_time(previous["updated_at"])
            ):
                latest[order_id] = order

    return list(latest.values())


ORDERS = load_orders()


def usd(order):
    currency = order["currency"]
    if currency not in RATES:
        raise ValueError(f"Unsupported currency: {currency}")

    return Decimal(str(order["amount"])) * RATES[currency]


def money(value):
    return float(
        Decimal(value).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def month_filter(question):
    q = question.lower()

    for name, number in MONTHS.items():
        if re.search(r"\b" + name + r"\b", q):
            year_match = re.search(r"\b(20\d{2})\b", q)
            year = int(year_match.group(1)) if year_match else None
            return number, year

    return None, None


def filter_orders(question):
    q = question.lower()
    data = ORDERS[:]

    # Explicit status filters take priority.
    if re.search(r"\b(refund|refunded|refunds)\b", q):
        data = [o for o in data if o["status"] == "refunded"]

    elif re.search(r"\b(void|voided|cancelled|canceled)\b", q):
        data = [o for o in data if o["status"] == "void"]

    elif re.search(r"\b(paid|revenue|sales|sold|earning|earnings)\b", q):
        data = [o for o in data if o["status"] == "paid"]

    # Region.
    for region in ("North", "South", "East", "West", "Central"):
        if re.search(r"\b" + region.lower() + r"\b", q):
            data = [
                o for o in data
                if o["region"].lower() == region.lower()
            ]
            break

    # Product: use exact product names from the data.
    products = sorted(
        {o["product"] for o in ORDERS},
        key=len,
        reverse=True,
    )

    for product in products:
        if re.search(
            r"(?<!\w)" + re.escape(product.lower()) + r"(?!\w)",
            q,
        ):
            data = [
                o for o in data
                if o["product"].lower() == product.lower()
            ]
            break

    # Customer ID.
    customer_match = re.search(r"\bC\d{4}\b", question, re.I)

    if customer_match:
        customer_id = customer_match.group().upper()
        data = [
            o for o in data
            if o["customer"].upper() == customer_id
        ]

    # Month and year are interpreted in Asia/Kolkata.
    month, year = month_filter(q)

    if month is not None:
        filtered = []

        for order in data:
            dt = parse_time(order["created_at"]).astimezone(INDIA_TZ)

            if dt.month == month and (
                year is None or dt.year == year
            ):
                filtered.append(order)

        data = filtered

    # Specific order ID.
    order_match = re.search(r"\bORD-\d+\b", question, re.I)

    if order_match:
        order_id = order_match.group().upper()
        data = [
            o for o in data
            if o["id"].upper() == order_id
        ]

    return data


def is_count_question(q):
    return (
        any(
            phrase in q
            for phrase in (
                "how many",
                "number of",
                "count of",
                "count the",
                "count all",
                "total number",
            )
        )
        or q.startswith("count ")
    )


def is_ranking_question(q):
    return any(
        phrase in q
        for phrase in (
            "top-selling",
            "top selling",
            "best-selling",
            "best selling",
            "bestselling",
            "highest",
            "most revenue",
            "most sales",
            "largest revenue",
            "highest revenue",
            "highest sales",
            "top product",
            "leading product",
        )
    )


def answer_question(question):
    q = question.lower().strip()
    data = filter_orders(question)

    if not data:
        return 0

    # RANKINGS must be handled before totals, counts, and averages.
    # Otherwise a "top product by revenue" question may return a
    # monetary total instead of the product name.
    if is_ranking_question(q):
        if "product" in q or "top-selling" in q or "top selling" in q:
            totals = defaultdict(lambda: Decimal("0"))

            for order in data:
                totals[order["product"]] += usd(order)

            if not totals:
                return 0

            return max(totals, key=totals.get)

        if "customer" in q or "customers" in q:
            totals = defaultdict(lambda: Decimal("0"))

            for order in data:
                totals[order["customer"]] += usd(order)

            return max(totals, key=totals.get) if totals else 0

        if "region" in q:
            totals = defaultdict(lambda: Decimal("0"))

            for order in data:
                totals[order["region"]] += usd(order)

            return max(totals, key=totals.get) if totals else 0

    # Average must come before generic count handling because
    # average questions can also contain the words "how many".
    if any(
        word in q
        for word in ("average", "avg", "on average", "mean")
    ):
        return money(
            sum((usd(o) for o in data), Decimal("0")) / len(data)
        )

    # Distinct customers, not the number of orders.
    if is_count_question(q) and (
        "customer" in q or "customers" in q
    ):
        return len({o["customer"] for o in data})

    # Number of orders.
    if is_count_question(q) or "how many orders" in q:
        return len(data)

    # Quantity of units, rather than number of orders.
    if any(
        word in q
        for word in ("quantity", "units", "items", "pieces")
    ) and not any(
        word in q
        for word in (
            "revenue", "sales", "amount", "value",
            "money", "usd", "dollar", "average",
        )
    ):
        return sum(int(o["qty"]) for o in data)

    # Default: total amount in USD.
    return money(
        sum((usd(o) for o in data), Decimal("0"))
    )


@app.get("/")
def health():
    return jsonify({
        "service": "Acme Appliances Ledger Agent",
        "orders_loaded": len(ORDERS),
    })


@app.post("/")
def ask():
    body = request.get_json(silent=True) or {}
    question = body.get("question")

    if not isinstance(question, str) or not question.strip():
        return jsonify({
            "error": "Provide a non-empty question"
        }), 400

    try:
        answer = answer_question(question)
        return jsonify({"answer": answer})

    except Exception:
        app.logger.exception("Could not answer question")
        return jsonify({
            "error": "Could not answer question"
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
