import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from flask import Flask, request, jsonify

app = Flask(__name__)

RATES = {"USD": Decimal("1"), "EUR": Decimal("1.13"), "INR": Decimal("0.01161")}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def load_orders():
    latest = {}
    with open("orders.ndjson", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            order = json.loads(line)
            old = latest.get(order["id"])
            if old is None or parse_time(order["updated_at"]) > parse_time(old["updated_at"]):
                latest[order["id"]] = order
    return list(latest.values())

ORDERS = load_orders()

def usd(order):
    return Decimal(str(order["amount"])) * RATES[order["currency"]]

def money(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def month_filter(question):
    q = question.lower()
    for name, number in MONTHS.items():
        if name in q:
            year = re.search(r"\b(20\d{2})\b", q)
            return number, int(year.group(1)) if year else None
    return None, None

def filter_orders(question):
    q = question.lower()
    data = ORDERS[:]

    # Revenue means paid orders. Refund questions mean refunded orders.
    if any(x in q for x in ("refund", "refunded", "refunds")):
        data = [o for o in data if o["status"] == "refunded"]
    elif any(x in q for x in ("void", "voided", "cancelled", "canceled")):
        data = [o for o in data if o["status"] == "void"]
    elif any(x in q for x in ("revenue", "sales", "sold", "paid", "earning", "earnings")):
        data = [o for o in data if o["status"] == "paid"]

    # Region
    for region in ("North", "South", "East", "West", "Central"):
        if re.search(r"\b" + region.lower() + r"\b", q):
            data = [o for o in data if o["region"].lower() == region.lower()]
            break

    # Product
    products = sorted({o["product"] for o in ORDERS}, key=len, reverse=True)
    for product in products:
        if product.lower() in q:
            data = [o for o in data if o["product"].lower() == product.lower()]
            break

    # Customer ID
    customer = re.search(r"\bC\d{4}\b", question, re.I)
    if customer:
        data = [o for o in data if o["customer"].upper() == customer.group().upper()]

    # Date filter uses the ISO timestamp's instant converted to India time.
    month, year = month_filter(q)
    if month:
        filtered = []
        for o in data:
            dt = parse_time(o["created_at"])
            from datetime import timezone, timedelta
            india = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
            if india.month == month and (year is None or india.year == year):
                filtered.append(o)
        data = filtered

    # Explicit order ID
    order_id = re.search(r"\bORD-\d+\b", question, re.I)
    if order_id:
        data = [o for o in data if o["id"].upper() == order_id.group().upper()]

    return data

def answer_question(question):
    q = question.lower().strip()
    data = filter_orders(question)

    if not data:
        return 0

    if (
        any(x in q for x in ("how many", "number of", "count of", "count the"))
        and "customer" in q
    ):
        return len({o["customer"] for o in data})

    if any(x in q for x in ("how many", "number of", "count of", "count the")):
        return len(data)

    if any(x in q for x in ("average", "avg")):
        return money(sum((usd(o) for o in data), Decimal("0")) / len(data))

    if any(x in q for x in ("quantity", "units", "items")) and not any(
        x in q for x in ("revenue", "sales", "amount", "value", "money", "usd", "dollar")
    ):
        return sum(o["qty"] for o in data)

    # Monetary total; convert each order into USD.
    total = sum((usd(o) for o in data), Decimal("0"))
    return money(total)

@app.get("/")
def health():
    return jsonify({"service": "Acme Appliances Ledger Agent", "orders_loaded": len(ORDERS)})

@app.post("/")
def ask():
    body = request.get_json(silent=True) or {}
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return jsonify({"error": "Provide a non-empty question"}), 400
    try:
        return jsonify({"answer": answer_question(question)})
    except Exception as e:
        app.logger.exception("Could not answer question")
        return jsonify({"error": "Could not answer question"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
