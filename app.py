
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

app = Flask(__name__)

DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "orders.ndjson",
)
IST = ZoneInfo("Asia/Kolkata")

RATES = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.13"),
    "INR": Decimal("0.01161"),
}


def load_orders():
    latest = {}

    with open(DATA_FILE, "r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            order = json.loads(line)
            order_id = str(order.get("id", ""))

            if not order_id:
                continue

            updated = str(order.get("updated_at", ""))

            if (
                order_id not in latest
                or updated > str(
                    latest[order_id].get("updated_at", "")
                )
            ):
                latest[order_id] = order

    return list(latest.values())


ORDERS = load_orders()


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def usd(order):
    currency = str(order.get("currency", "USD")).upper()
    rate = RATES.get(currency)

    if rate is None:
        return Decimal("0")

    return Decimal(str(order.get("amount", 0))) * rate


def money(value):
    return float(
        Decimal(str(value)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def order_date(order, field="created_at"):
    value = order.get(field)

    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)

        return dt.astimezone(IST)
    except (ValueError, TypeError):
        return None


def filter_orders(question):
    q = normalize(question)
    data = ORDERS[:]

    # Status filter
    if "refunded" in q or "refund" in q:
        data = [
            o for o in data
            if normalize(o.get("status")) == "refunded"
        ]
    elif any(word in q for word in ("void", "cancelled", "canceled")):
        data = [
            o for o in data
            if normalize(o.get("status")) == "void"
        ]
    elif any(
        word in q
        for word in (
            "paid", "revenue", "sales", "sold", "selling",
            "earned", "worth", "order value",
        )
    ):
        data = [
            o for o in data
            if normalize(o.get("status")) == "paid"
        ]

    # Region filter
    for region in ("Central", "East", "North", "South", "West"):
        if re.search(r"\b" + region.lower() + r"\b", q):
            data = [
                o for o in data
                if normalize(o.get("region")) == region.lower()
            ]
            break

    # Product filter
    products = sorted(
        {
            str(o.get("product", ""))
            for o in ORDERS
            if o.get("product")
        },
        key=len,
        reverse=True,
    )

    for product in products:
        if normalize(product) in q:
            data = [
                o for o in data
                if normalize(o.get("product")) == normalize(product)
            ]
            break

    # Customer ID filter
    customer_ids = {
        str(o.get("customer", "")).upper()
        for o in ORDERS
        if o.get("customer")
    }

    for customer_id in customer_ids:
        if customer_id.lower() in q:
            data = [
                o for o in data
                if str(o.get("customer", "")).upper() == customer_id
            ]
            break

    # Order ID filter
    order_ids = {str(o.get("id", "")) for o in ORDERS}

    for order_id in order_ids:
        if order_id and re.search(
            r"\b" + re.escape(order_id.lower()) + r"\b",
            q,
        ):
            data = [
                o for o in data
                if str(o.get("id", "")) == order_id
            ]
            break

    # Month and year filter, using Asia/Kolkata business dates
    months = {
        "january": 1, "jan": 1,
        "february": 2, "feb": 2,
        "march": 3, "mar": 3,
        "april": 4, "apr": 4,
        "may": 5,
        "june": 6, "jun": 6,
        "july": 7, "jul": 7,
        "august": 8, "aug": 8,
        "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10,
        "november": 11, "nov": 11,
        "december": 12, "dec": 12,
    }

    month_num = None
    year_num = None

    for name, number in months.items():
        match = re.search(r"\b" + name + r"\s+(\d{4})\b", q)

        if match:
            month_num = number
            year_num = int(match.group(1))
            break

    if month_num is None:
        match = re.search(r"\b(\d{4})-(\d{1,2})\b", q)

        if match:
            year_num = int(match.group(1))
            month_num = int(match.group(2))

    if month_num is not None and year_num is not None:
        data = [
            o for o in data
            if (
                order_date(o) is not None
                and order_date(o).year == year_num
                and order_date(o).month == month_num
            )
        ]

    return data


def is_count_question(q):
    return any(
        phrase in q
        for phrase in (
            "how many",
            "number of",
            "count of",
            "count the",
            "how much",
        )
    )


def is_ranking_question(q):
    ranking_words = (
        "top",
        "highest",
        "most",
        "best-selling",
        "best selling",
        "bestselling",
        "greatest",
        "largest",
        "leading",
        "lowest",
        "least",
        "fewest",
    )

    metric_words = (
        "revenue",
        "sales",
        "selling",
        "earned",
        "units",
        "quantity",
        "orders",
        "customers",
    )

    return (
        any(word in q for word in ranking_words)
        and any(word in q for word in metric_words)
    )


def answer_question(question):
    q = normalize(question)
    data = filter_orders(q)

    if not data:
        return 0

    # Handle distinct-customer counts BEFORE ranking questions.
    if (
        is_count_question(q)
        and "customer" in q
        and any(
            word in q
            for word in ("different", "distinct", "unique")
        )
    ):
        return len({
            str(o.get("customer", ""))
            for o in data
            if o.get("customer")
        })

    # Ranking questions
    if is_ranking_question(q):
        if any(
            word in q
            for word in ("unit", "quantity", "quantities")
        ):
            metric = "units"
        elif "customer" in q:
            metric = "customers"
        elif "order" in q and not any(
            word in q for word in ("revenue", "sales", "earned")
        ):
            metric = "orders"
        else:
            metric = "revenue"

        if "region" in q:
            entity_field = "region"
        elif "customer" in q:
            entity_field = "customer"
        elif "product" in q or any(
            phrase in q
            for phrase in (
                "top-selling", "top selling",
                "best-selling", "best selling",
                "bestselling",
            )
        ):
            entity_field = "product"
        else:
            entity_field = "product"

        totals = defaultdict(Decimal)

        for order in data:
            key = str(order.get(entity_field, ""))

            if not key:
                continue

            if metric == "units":
                totals[key] += Decimal(str(order.get("qty", 0)))
            elif metric == "customers":
                totals[key] += Decimal("1")
            elif metric == "orders":
                totals[key] += Decimal("1")
            else:
                totals[key] += usd(order)

        if not totals:
            return 0

        reverse = not any(
            word in q for word in ("lowest", "least", "fewest")
        )

        winner = sorted(
            totals.items(),
            key=lambda item: (item[1], item[0].lower()),
            reverse=reverse,
        )[0]

        asks_for_value = any(
            phrase in q
            for phrase in (
                "how much",
                "what amount",
                "what was the revenue",
                "what was the sales",
                "how many units",
                "how many orders",
                "revenue of",
                "sales of",
            )
        )

        if asks_for_value:
            if metric == "revenue":
                return money(winner[1])

            if winner[1] == winner[1].to_integral_value():
                return int(winner[1])

            return float(winner[1])

        return winner[0]

    # Average must precede generic count handling.
    if any(word in q for word in ("average", "avg", "mean")):
        total = sum((usd(o) for o in data), Decimal("0"))
        return money(total / Decimal(len(data)))

    # Other distinct-customer questions
    if (
        is_count_question(q)
        and "customer" in q
        and any(
            word in q
            for word in ("different", "distinct", "unique")
        )
    ):
        return len({
            str(o.get("customer", ""))
            for o in data
            if o.get("customer")
        })

    # General counts
    if is_count_question(q):
        if any(word in q for word in ("units", "quantity", "items")):
            return sum(int(o.get("qty", 0)) for o in data)

        return len(data)

    # Revenue and monetary totals
    if any(
        word in q
        for word in (
            "revenue", "sales", "earned", "total amount",
            "total value", "in usd", "dollars",
        )
    ):
        return money(sum((usd(o) for o in data), Decimal("0")))

    # Default: total revenue in USD
    return money(sum((usd(o) for o in data), Decimal("0")))


@app.get("/")
def health():
    return jsonify({
        "service": "Acme Appliances Ledger Agent",
        "orders_loaded": len(ORDERS),
    })


@app.post("/")
def answer():
    body = request.get_json(silent=True) or {}
    question = body.get("question")

    if not isinstance(question, str) or not question.strip():
        return jsonify({
            "error": "Please provide a non-empty question."
        }), 400

    try:
        result = answer_question(question)
        return jsonify({"answer": result})
    except Exception:
        app.logger.exception("Failed to answer question")
        return jsonify({
            "error": "Unable to answer question."
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
    )
