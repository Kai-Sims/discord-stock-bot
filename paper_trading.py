import uuid
from datetime import datetime

from storage import data_path, read_json, write_json


PAPER_TRADES_FILE = data_path("paper_trades.json")
PAPER_TRADING_DISCLAIMER = "Paper trading is simulated for research only and is not financial advice. This bot does not place real trades."


def load_paper_trades():
    data = read_json(PAPER_TRADES_FILE, {"trades": []})
    if not isinstance(data, dict) or not isinstance(data.get("trades"), list):
        data = {"trades": []}
    return data


def save_paper_trades(data):
    if not isinstance(data, dict):
        data = {"trades": []}
    data.setdefault("trades", [])
    write_json(PAPER_TRADES_FILE, data)


def add_paper_trade(ticker, side, quantity, price, reason=""):
    data = load_paper_trades()
    trade = {
        "id": str(uuid.uuid4()),
        "ticker": str(ticker).upper(),
        "side": side,
        "quantity": float(quantity),
        "price": float(price),
        "date": datetime.now().isoformat(timespec="seconds"),
        "reason": reason or "",
        "status": "open",
    }
    data["trades"].append(trade)
    save_paper_trades(data)
    return trade


def recent_trades(limit=10):
    return list(reversed(load_paper_trades().get("trades", [])))[0:limit]


def clear_paper_trades():
    save_paper_trades({"trades": []})


def close_position(ticker, price, reason=""):
    ticker = str(ticker).upper()
    data = load_paper_trades()
    open_quantity = 0
    for trade in data["trades"]:
        if trade.get("ticker") != ticker or trade.get("status") != "open":
            continue
        if trade.get("side") == "buy":
            open_quantity += float(trade.get("quantity", 0))
        elif trade.get("side") == "sell":
            open_quantity -= float(trade.get("quantity", 0))

    if open_quantity <= 0:
        return None

    trade = {
        "id": str(uuid.uuid4()),
        "ticker": ticker,
        "side": "sell",
        "quantity": open_quantity,
        "price": float(price),
        "date": datetime.now().isoformat(timespec="seconds"),
        "reason": reason or "Close paper position",
        "status": "open",
    }
    data["trades"].append(trade)
    for item in data["trades"]:
        if item.get("ticker") == ticker:
            item["status"] = "closed"
    save_paper_trades(data)
    return trade


def open_positions():
    lots = {}
    for trade in load_paper_trades().get("trades", []):
        if trade.get("status") != "open":
            continue
        ticker = trade.get("ticker")
        lots.setdefault(ticker, {"quantity": 0.0, "cost": 0.0})
        quantity = float(trade.get("quantity", 0))
        price = float(trade.get("price", 0))
        if trade.get("side") == "buy":
            lots[ticker]["quantity"] += quantity
            lots[ticker]["cost"] += quantity * price
        elif trade.get("side") == "sell":
            lots[ticker]["quantity"] -= quantity
            lots[ticker]["cost"] -= quantity * price

    positions = []
    for ticker, values in lots.items():
        quantity = values["quantity"]
        if quantity <= 0:
            continue
        positions.append(
            {
                "ticker": ticker,
                "quantity": quantity,
                "average_cost": values["cost"] / quantity if quantity else 0,
            }
        )
    return positions


def realized_pnl():
    pnl = 0.0
    lots = {}
    for trade in load_paper_trades().get("trades", []):
        ticker = trade.get("ticker")
        lots.setdefault(ticker, [])
        quantity = float(trade.get("quantity", 0))
        price = float(trade.get("price", 0))
        if trade.get("side") == "buy":
            lots[ticker].append([quantity, price])
            continue
        remaining = quantity
        while remaining > 0 and lots[ticker]:
            lot_quantity, lot_price = lots[ticker][0]
            used = min(remaining, lot_quantity)
            pnl += used * (price - lot_price)
            lot_quantity -= used
            remaining -= used
            if lot_quantity <= 0:
                lots[ticker].pop(0)
            else:
                lots[ticker][0][0] = lot_quantity
    return pnl
