def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_signal_score(stock_data, analyst_data=None, reddit_data=None, earnings_data=None):
    analyst_data = analyst_data or {}
    reddit_data = reddit_data or {}
    earnings_data = earnings_data or {}
    components = {
        "trend": 0,
        "volume": 0,
        "momentum": 0,
        "rsi": 0,
        "analyst": 0,
        "earnings_risk": 0,
        "reddit_attention": 0,
    }
    notes = []

    trend = (stock_data or {}).get("trend")
    if trend == "Bullish":
        components["trend"] = 2
        notes.append("Bullish trend")
    elif trend == "Mixed":
        components["trend"] = 1
        notes.append("Mixed trend")
    elif trend == "Bearish":
        components["trend"] = -1
        notes.append("Bearish trend")

    rel_volume = _num((stock_data or {}).get("relative_volume"))
    if rel_volume is not None and rel_volume >= 2.0:
        components["volume"] = 2
        notes.append("Relative volume above 2.0x")
    elif rel_volume is not None and rel_volume >= 1.2:
        components["volume"] = 1
        notes.append("Relative volume above 1.2x")

    change_5d = _num((stock_data or {}).get("change_5d_percent"))
    if change_5d is not None and 3 <= change_5d <= 10:
        components["momentum"] = 2
        notes.append("Healthy 5-day momentum")
    elif change_5d is not None and 0 <= change_5d < 3:
        components["momentum"] = 1
        notes.append("Slight positive 5-day momentum")
    elif change_5d is not None and change_5d > 20:
        components["momentum"] = -1
        notes.append("Extended after a large 5-day move")

    rsi = _num((stock_data or {}).get("rsi"))
    if rsi is not None and 45 <= rsi <= 65:
        components["rsi"] = 1
        notes.append("RSI in a balanced range")
    elif rsi is not None and (rsi >= 80 or rsi <= 20):
        components["rsi"] = -1
        notes.append("RSI is extreme")

    recommendation_mean = _num(analyst_data.get("recommendation_mean"))
    target_upside = _num(analyst_data.get("target_upside_percent"))
    if recommendation_mean is not None and recommendation_mean <= 2.5:
        components["analyst"] += 1
        notes.append("Analyst rating support")
    if target_upside is not None and target_upside >= 10:
        components["analyst"] += 1
        notes.append("Analyst target upside above 10%")

    days_until_earnings = earnings_data.get("days_until")
    if days_until_earnings in (0, 1):
        components["earnings_risk"] = -2
        notes.append("Earnings today or tomorrow")
    elif days_until_earnings is not None and 0 <= days_until_earnings <= 3:
        components["earnings_risk"] = -1
        notes.append("Earnings within 3 days")

    mentions = _num(reddit_data.get("mention_count"))
    if mentions is not None and mentions >= 10:
        components["reddit_attention"] = 1
        notes.append("Elevated Reddit attention")
        if rsi is not None and rsi >= 70:
            components["reddit_attention"] = -1
            notes.append("Extreme attention with high RSI")

    raw_score = sum(components.values()) + 4
    score = max(0, min(10, round(float(raw_score), 1)))
    if score >= 8:
        label = "Very Strong"
    elif score >= 6:
        label = "Strong"
    elif score >= 4:
        label = "Moderate"
    else:
        label = "Weak"

    return {"score": score, "label": label, "components": components, "notes": notes}


def format_signal_score(score_data):
    if not score_data:
        return "Signal Score: N/A"
    return f"Signal Score: {score_data.get('score', 0):.1f}/10 - {score_data.get('label', 'N/A')}"
