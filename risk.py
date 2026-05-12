from datetime import datetime


SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🚨"}


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _days_until(date_text):
    if not date_text:
        return None
    try:
        return (datetime.fromisoformat(str(date_text)).date() - datetime.now().date()).days
    except ValueError:
        return None


def calculate_risk_flags(stock_data, earnings_data=None, analyst_data=None, reddit_data=None):
    flags = []
    stock_data = stock_data or {}
    earnings_data = earnings_data or {}
    reddit_data = reddit_data or {}

    required = ["latest_price", "rsi", "relative_volume", "ma200"]
    if any(stock_data.get(key) is None for key in required):
        flags.append({"severity": "low", "message": "Data incomplete"})

    days_until = earnings_data.get("days_until")
    if days_until is None:
        days_until = _days_until(earnings_data.get("earnings_date"))
    if days_until == 0:
        flags.append({"severity": "high", "message": "Earnings today"})
    elif days_until == 1:
        flags.append({"severity": "high", "message": "Earnings tomorrow"})
    elif days_until is not None and 0 <= days_until <= 3:
        flags.append({"severity": "medium", "message": "Earnings within 3 days"})

    rsi = _num(stock_data.get("rsi"))
    if rsi is not None and rsi >= 80:
        flags.append({"severity": "high", "message": "RSI extremely overbought"})
    elif rsi is not None and rsi >= 70:
        flags.append({"severity": "medium", "message": "RSI overbought"})
    elif rsi is not None and rsi <= 30:
        flags.append({"severity": "medium", "message": "RSI oversold"})

    change_5d = _num(stock_data.get("change_5d_percent"))
    if change_5d is not None and change_5d > 20:
        flags.append({"severity": "medium", "message": "Stock is extended after a large 5-day move"})

    price = _num(stock_data.get("latest_price") or stock_data.get("price"))
    ma200 = _num(stock_data.get("ma200") or stock_data.get("moving_average_200"))
    if price is not None and ma200 is not None and price < ma200:
        flags.append({"severity": "medium", "message": "Below 200-day moving average"})

    rel_volume = _num(stock_data.get("relative_volume"))
    if rel_volume is not None and rel_volume < 0.7:
        flags.append({"severity": "low", "message": "Low relative volume"})

    mentions = _num(reddit_data.get("mention_count"))
    if mentions is not None and mentions >= 25:
        flags.append({"severity": "high", "message": "Very high Reddit attention"})

    day_change = _num(stock_data.get("day_change_percent"))
    if day_change is not None and abs(day_change) >= 8:
        flags.append({"severity": "medium", "message": "Large gap or unusual move"})

    percent_above_low = _num(stock_data.get("percent_above_52w_low"))
    if percent_above_low is not None and percent_above_low <= 5:
        flags.append({"severity": "medium", "message": "Near 52-week low"})

    return flags


def format_risk_flags(flags):
    if not flags:
        return "Risk Flags: None"
    return "Risk Flags: " + "; ".join(
        f"{SEVERITY_EMOJI.get(flag.get('severity'), '🟢')} {flag.get('message')}" for flag in flags
    )


def max_severity(flags):
    if not flags:
        return "low"
    return max((flag.get("severity", "low") for flag in flags), key=lambda item: SEVERITY_RANK.get(item, 1))


def severity_allows(severity, minimum):
    return SEVERITY_RANK.get(severity, 1) >= SEVERITY_RANK.get(minimum, 1)


def determine_alert_severity(alert_type, stock_data, risk_flags=None, signal_score=None):
    alert_type = str(alert_type or "").lower()
    stock_data = stock_data or {}
    risk_flags = risk_flags or []
    rel_volume = _num(stock_data.get("relative_volume")) or 0
    rsi = _num(stock_data.get("rsi"))

    severity = "low"
    if "52" in alert_type and rel_volume >= 1.5:
        severity = "high"
    elif "52" in alert_type:
        severity = "medium"
    elif "volume" in alert_type and rel_volume >= 3:
        severity = "high"
    elif "volume" in alert_type:
        severity = "medium"
    elif "rsi" in alert_type and rsi is not None and (rsi >= 80 or rsi <= 20):
        severity = "high"
    elif "rsi" in alert_type:
        severity = "medium"
    elif "wsb" in alert_type and rel_volume >= 3:
        severity = "critical"

    risk_severity = max_severity(risk_flags)
    if SEVERITY_RANK.get(risk_severity, 1) > SEVERITY_RANK.get(severity, 1):
        severity = risk_severity

    if signal_score and signal_score.get("score", 0) >= 8 and severity == "medium":
        severity = "high"

    return severity
