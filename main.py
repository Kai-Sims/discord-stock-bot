import asyncio
import json
import os
import re
import time
from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

import discord
import pandas as pd
import praw
import requests
import yfinance as yf
from discord.ext import commands, tasks
from dotenv import load_dotenv


load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID")

CATEGORY_NAME = "Stock Market Bot"
WATCHLIST_FILE = "watchlist.json"
SCANNER_UNIVERSE_FILE = "scanner_universe.json"
US_STOCK_UNIVERSE_FILE = "us_stock_universe.json"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
DEFAULT_WATCHLIST = ["AAPL", "TSLA", "NVDA", "AMD", "SPY", "QQQ"]
DEFAULT_SCANNER_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GOOGL", "AMZN",
    "NFLX", "AVGO", "ORCL", "CRM", "ADBE", "INTC", "QCOM", "MU",
    "PLTR", "SOFI", "RIVN", "LCID", "UBER", "SHOP", "COIN", "HOOD",
    "JPM", "BAC", "V", "MA", "PYPL", "DIS", "NKE", "COST", "WMT",
    "HD", "CAT", "DE", "BA", "GE", "XOM", "CVX", "OXY",
    "SPY", "QQQ", "IWM", "DIA", "SMH", "XLK", "XLF", "XLE",
]
SCAN_RESULT_LIMIT = 10
MIN_PRICE_FILTER = 5.00
MIN_AVG_VOLUME_FILTER = 500000
MAX_UNIVERSE_SCAN_TICKERS = 500
FULL_SCAN_RESULT_LIMIT = 15
CUSTOM_SCAN_RESULT_LIMIT = 15
MARKET_TIMEZONE = "America/Los_Angeles"
EOD_SUMMARY_HOUR = 13
EOD_SUMMARY_MINUTE = 30
EOD_SUMMARY_CHANNEL = "trade-journal"
WSB_SUBREDDIT = "wallstreetbets"
WSB_CHECK_INTERVAL_MINUTES = 10
WSB_POST_LIMIT = 50
WSB_MENTION_LOOKBACK_MINUTES = 60
WSB_MIN_MENTIONS_TO_TRACK = 2
WSB_MAX_TRACKED_TICKERS = 25
WSB_MENTIONS_FILE = "wsb_mentions.json"
WSB_TRACKING_FILE = "wsb_tracking.json"
ENABLE_DAILY_SCANNER = False
ALERT_INTERVAL_MINUTES = 5
NEAR_DAILY_HIGH_THRESHOLD = 0.995
NEAR_DAILY_LOW_THRESHOLD = 1.005
NEAR_52W_HIGH_THRESHOLD = 0.99
NEAR_52W_LOW_THRESHOLD = 1.01
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
VOLUME_SPIKE_MULTIPLE = 1.8
CHANNEL_NAMES = [
    "stock-alerts",
    "options-alerts",
    "daily-highs",
    "daily-lows",
    "fifty-two-week-highs",
    "fifty-two-week-lows",
    "rsi-alerts",
    "volume-spikes",
    "earnings-alerts",
    "news-alerts",
    "watchlist",
    "stock-ideas",
    "wsb-mentions",
    "wsb-tracking",
    "wsb-alerts",
    "bot-status",
    "trade-journal",
    "bot-commands",
]

sent_alerts = set()
last_scan_times = {}
broad_scan_times = {}
current_universe_scan_limit = MAX_UNIVERSE_SCAN_TICKERS
last_eod_summary_date = None
VALID_SCAN_TYPES = {"balanced", "momentum", "breakouts", "oversold", "pullbacks", "volume"}
CUSTOM_FILTER_KEYS = {
    "rsi",
    "price",
    "volume",
    "relvol",
    "change5d",
    "near52whigh",
    "near52wlow",
    "above20ma",
    "above50ma",
    "above200ma",
    "below20ma",
    "below50ma",
    "below200ma",
    "trend",
}
SCANNER_DISCLAIMER = "Scanner results are for research only and are not financial advice."
WSB_DISCLAIMER = "WSB tracking is for research only and is not financial advice."
WSB_FALSE_POSITIVES = {
    "A", "I", "YOLO", "DD", "CEO", "CFO", "SEC", "ETF", "USA", "USD",
    "FED", "CPI", "GDP", "IV", "ITM", "OTM", "ATM", "AI", "API", "IPO",
    "LOL", "IMO", "TOS", "RH", "IRS", "FOMO", "ATH", "EOD", "AH", "PM",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HAS",
    "HAVE", "THIS", "THAT", "WITH", "FROM", "WHAT", "WHEN", "MOON", "CALL",
    "PUT", "PUTS", "HOLD", "GAIN", "LOSS", "BUY", "SELL", "OPEN", "CLOSE",
    "HIGH", "LOW", "RED", "GREEN", "NEWS", "POST", "MOD", "WSB",
}
FALLBACK_WSB_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GOOGL", "GOOG", "AMZN",
    "NFLX", "AVGO", "ORCL", "CRM", "ADBE", "INTC", "QCOM", "MU", "PLTR",
    "SOFI", "RIVN", "LCID", "UBER", "SHOP", "COIN", "HOOD", "GME", "AMC",
    "BB", "NOK", "SPY", "QQQ", "IWM", "DIA", "SMH", "ARKK", "JPM", "BAC",
    "V", "MA", "PYPL", "DIS", "NKE", "COST", "WMT", "HD", "XOM", "CVX",
}
EXCLUDED_SECURITY_KEYWORDS = [
    "Warrant",
    "Right",
    "Unit",
    "Preferred",
    "Preference",
    "Note",
    "Bond",
    "Debenture",
]


def get_guild_id():
    """Read and validate GUILD_ID from the environment."""
    if not GUILD_ID_RAW:
        print("Missing GUILD_ID. Add your Discord server ID to the .env file.")
        return None

    try:
        return int(GUILD_ID_RAW)
    except ValueError:
        print(
            "GUILD_ID must be a number. Enable Developer Mode in Discord, "
            "right-click your server, and copy the server ID."
        )
        return None


def normalize_ticker(ticker):
    """Clean up ticker symbols typed by users."""
    if ticker is None:
        return ""

    return ticker.strip().upper().removeprefix("$")


def save_watchlist(tickers):
    """Save uppercase, sorted, unique tickers to watchlist.json."""
    clean_tickers = sorted(
        {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    )

    with open(WATCHLIST_FILE, "w", encoding="utf-8") as file:
        json.dump({"tickers": clean_tickers}, file, indent=2)

    return clean_tickers


def load_watchlist():
    """Load watchlist.json, creating or repairing it when needed."""
    if not os.path.exists(WATCHLIST_FILE):
        print("watchlist.json not found. Creating it with the default watchlist.")
        return save_watchlist(DEFAULT_WATCHLIST)

    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        print("watchlist.json has invalid JSON. Falling back to the default watchlist.")
        return save_watchlist(DEFAULT_WATCHLIST)
    except OSError as error:
        print(f"Could not read watchlist.json: {error}")
        return [normalize_ticker(ticker) for ticker in DEFAULT_WATCHLIST]

    tickers = data.get("tickers", [])
    if not isinstance(tickers, list):
        print("watchlist.json must contain a tickers list. Resetting to default.")
        return save_watchlist(DEFAULT_WATCHLIST)

    return save_watchlist(tickers)


def save_scanner_universe(tickers):
    """Save uppercase, sorted, unique tickers to scanner_universe.json."""
    clean_tickers = sorted(
        {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    )

    with open(SCANNER_UNIVERSE_FILE, "w", encoding="utf-8") as file:
        json.dump({"tickers": clean_tickers}, file, indent=2)

    return clean_tickers


def load_scanner_universe():
    """Load scanner_universe.json, creating or repairing it when needed."""
    if not os.path.exists(SCANNER_UNIVERSE_FILE):
        print("scanner_universe.json not found. Creating it with the default universe.")
        return save_scanner_universe(DEFAULT_SCANNER_UNIVERSE)

    try:
        with open(SCANNER_UNIVERSE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        print("scanner_universe.json has invalid JSON. Falling back to default universe.")
        return save_scanner_universe(DEFAULT_SCANNER_UNIVERSE)
    except OSError as error:
        print(f"Could not read scanner_universe.json: {error}")
        return sorted({normalize_ticker(ticker) for ticker in DEFAULT_SCANNER_UNIVERSE})

    tickers = data.get("tickers", [])
    if not isinstance(tickers, list):
        print("scanner_universe.json must contain a tickers list. Resetting to default.")
        return save_scanner_universe(DEFAULT_SCANNER_UNIVERSE)

    return save_scanner_universe(tickers)


def save_us_stock_universe(tickers, last_updated=None):
    """Save the broad US stock universe with metadata."""
    clean_tickers = sorted(
        {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    )
    data = {
        "tickers": clean_tickers,
        "last_updated": last_updated or datetime.now().isoformat(timespec="seconds"),
        "source": "nasdaqtrader",
    }

    with open(US_STOCK_UNIVERSE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)

    return clean_tickers


def load_us_stock_universe_data():
    """Load broad US universe metadata from disk."""
    if not os.path.exists(US_STOCK_UNIVERSE_FILE):
        print("us_stock_universe.json is missing. Run !refreshuniverse first.")
        return {"tickers": [], "last_updated": None, "source": "nasdaqtrader"}

    try:
        with open(US_STOCK_UNIVERSE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        print("us_stock_universe.json has invalid JSON. Run !refreshuniverse.")
        return {"tickers": [], "last_updated": None, "source": "nasdaqtrader"}
    except OSError as error:
        print(f"Could not read us_stock_universe.json: {error}")
        return {"tickers": [], "last_updated": None, "source": "nasdaqtrader"}

    tickers = data.get("tickers", [])
    if not isinstance(tickers, list) or not tickers:
        print("US stock universe is empty. Run !refreshuniverse first.")
        tickers = []

    return {
        "tickers": sorted({normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}),
        "last_updated": data.get("last_updated"),
        "source": data.get("source", "nasdaqtrader"),
    }


def load_us_stock_universe():
    """Return broad US stock universe tickers, if available."""
    return load_us_stock_universe_data()["tickers"]


def is_excluded_security_name(security_name):
    return any(keyword.lower() in security_name.lower() for keyword in EXCLUDED_SECURITY_KEYWORDS)


def clean_universe_symbol(symbol):
    symbol = symbol.strip().upper().replace(".", "-")
    if any(character in symbol for character in ["^", "/", " "]):
        return ""
    if not all(character.isalnum() or character == "-" for character in symbol):
        return ""
    return symbol


def parse_nasdaqtrader_text(text, symbol_field):
    """Parse Nasdaq Trader pipe-delimited text into common stock tickers."""
    tickers = []
    lines = [line for line in text.splitlines() if "|" in line]
    if not lines:
        return tickers

    headers = lines[0].split("|")
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue

        values = line.split("|")
        if len(values) != len(headers):
            continue

        row = dict(zip(headers, values))
        symbol = clean_universe_symbol(row.get(symbol_field, ""))
        security_name = row.get("Security Name", "")
        is_test_issue = row.get("Test Issue", "N").upper() == "Y"
        is_etf = row.get("ETF", "N").upper() == "Y"

        if not symbol or is_test_issue or is_etf or is_excluded_security_name(security_name):
            continue

        tickers.append(symbol)

    return tickers


def download_us_stock_universe():
    """Download and save a broad US stock universe from Nasdaq Trader files."""
    response_nasdaq = requests.get(NASDAQ_LISTED_URL, timeout=30)
    response_nasdaq.raise_for_status()
    response_other = requests.get(OTHER_LISTED_URL, timeout=30)
    response_other.raise_for_status()

    tickers = []
    tickers.extend(parse_nasdaqtrader_text(response_nasdaq.text, "Symbol"))
    tickers.extend(parse_nasdaqtrader_text(response_other.text, "ACT Symbol"))

    final_tickers = save_us_stock_universe(tickers)
    print(f"Downloaded and saved {len(final_tickers)} broad US stock tickers.")
    return final_tickers


def calculate_rsi(close_prices, period=14):
    """Calculate RSI from a pandas Series of close prices."""
    prices = pd.Series(close_prices).dropna()
    if len(prices) <= period:
        return None

    delta = prices.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    average_gain = gains.rolling(window=period).mean()
    average_loss = losses.rolling(window=period).mean()
    relative_strength = average_gain / average_loss
    rsi = 100 - (100 / (1 + relative_strength))

    latest_rsi = rsi.dropna()
    if latest_rsi.empty:
        return None

    return float(latest_rsi.iloc[-1])


def _to_float(value):
    if pd.isna(value):
        return None
    return float(value)


def _latest_trading_day(data):
    if data.empty:
        return data

    latest_date = data.index[-1].date()
    return data[data.index.date == latest_date]


def analyze_stock(ticker):
    """Fetch market data and return a clean stock analysis dictionary."""
    ticker = normalize_ticker(ticker)
    if not ticker:
        return None

    try:
        stock = yf.Ticker(ticker)
        intraday = stock.history(period="5d", interval="5m", auto_adjust=False)
        daily = stock.history(period="1y", interval="1d", auto_adjust=False)
    except Exception as error:
        print(f"Could not fetch data for {ticker}: {error}")
        return None

    if intraday.empty or daily.empty:
        print(f"No usable market data found for {ticker}.")
        return None

    current_day = _latest_trading_day(intraday)
    if current_day.empty:
        current_day = intraday

    close_prices = daily["Close"].dropna()
    if close_prices.empty:
        print(f"No close price data found for {ticker}.")
        return None

    intraday_close = intraday["Close"].dropna()
    if intraday_close.empty:
        print(f"No intraday close price data found for {ticker}.")
        return None

    latest_price = _to_float(intraday_close.iloc[-1])
    if latest_price is None:
        print(f"No latest price found for {ticker}.")
        return None

    day_high = _to_float(current_day["High"].max())
    day_low = _to_float(current_day["Low"].min())
    fifty_two_week_high = _to_float(daily["High"].max())
    fifty_two_week_low = _to_float(daily["Low"].min())
    moving_average_20 = _to_float(close_prices.rolling(window=20).mean().iloc[-1])
    moving_average_50 = _to_float(close_prices.rolling(window=50).mean().iloc[-1])
    moving_average_200 = _to_float(close_prices.rolling(window=200).mean().iloc[-1])
    rsi = calculate_rsi(close_prices)
    five_day_change = None
    if len(close_prices) >= 6:
        five_days_ago = _to_float(close_prices.iloc[-6])
        if five_days_ago and five_days_ago > 0:
            five_day_change = ((latest_price - five_days_ago) / five_days_ago) * 100

    volume_data = intraday["Volume"].dropna()
    latest_intraday_volume = _to_float(volume_data.iloc[-1]) if not volume_data.empty else None
    average_intraday_volume = _to_float(volume_data.tail(78).mean()) if not volume_data.empty else None
    daily_volume_data = daily["Volume"].dropna()
    current_volume = _to_float(daily_volume_data.iloc[-1]) if not daily_volume_data.empty else None
    average_volume_20d = (
        _to_float(daily_volume_data.tail(20).mean()) if not daily_volume_data.empty else None
    )
    relative_volume = None
    if current_volume is not None and average_volume_20d is not None and average_volume_20d > 0:
        relative_volume = current_volume / average_volume_20d

    volume_spike = (
        relative_volume is not None and relative_volume >= VOLUME_SPIKE_MULTIPLE
    )
    percent_below_52w_high = None
    if fifty_two_week_high is not None and fifty_two_week_high > 0:
        percent_below_52w_high = ((fifty_two_week_high - latest_price) / fifty_two_week_high) * 100

    percent_above_52w_low = None
    if fifty_two_week_low is not None and fifty_two_week_low > 0:
        percent_above_52w_low = ((latest_price - fifty_two_week_low) / fifty_two_week_low) * 100

    moving_averages = [moving_average_20, moving_average_50, moving_average_200]
    if moving_average_200 is not None and latest_price < moving_average_200 * 0.85:
        trend = "High-risk"
    elif all(value is not None and latest_price > value for value in moving_averages):
        trend = "Bullish"
    elif all(value is not None and latest_price < value for value in moving_averages):
        trend = "Bearish"
    else:
        trend = "Mixed"

    return {
        "ticker": ticker,
        "price": latest_price,
        "latest_price": latest_price,
        "day_high": day_high,
        "day_low": day_low,
        "year_high": fifty_two_week_high,
        "year_low": fifty_two_week_low,
        "fifty_two_week_high": fifty_two_week_high,
        "fifty_two_week_low": fifty_two_week_low,
        "ma20": moving_average_20,
        "ma50": moving_average_50,
        "ma200": moving_average_200,
        "moving_average_20": moving_average_20,
        "moving_average_50": moving_average_50,
        "moving_average_200": moving_average_200,
        "rsi": rsi,
        "current_volume": current_volume,
        "volume": current_volume,
        "avg_volume_20": average_volume_20d,
        "average_volume_20d": average_volume_20d,
        "relative_volume": relative_volume,
        "change_5d_percent": five_day_change,
        "five_day_change": five_day_change,
        "percent_below_52w_high": percent_below_52w_high,
        "percent_above_52w_low": percent_above_52w_low,
        "latest_volume": latest_intraday_volume,
        "average_volume": average_intraday_volume,
        "volume_spike": volume_spike,
        "trend": trend,
    }


def is_near(value, target, percent=0.03):
    if value is None or target is None or target == 0:
        return False
    return abs(value - target) / target <= percent


def calculate_score(stock_data, scan_type):
    """Score scanner results without creating trading instructions."""
    price = stock_data["price"]
    ma20 = stock_data["moving_average_20"]
    ma50 = stock_data["moving_average_50"]
    ma200 = stock_data["moving_average_200"]
    rsi = stock_data["rsi"]
    rel_volume = stock_data["relative_volume"]
    five_day_change = stock_data["five_day_change"]
    high_52w = stock_data["fifty_two_week_high"]
    score = 0

    if scan_type == "balanced":
        for scan_name in ["momentum", "breakouts", "pullbacks", "volume"]:
            score += calculate_score(stock_data, scan_name) * 0.35
        if rsi is not None and 35 <= rsi <= 75:
            score += 1
        return round(score, 2)

    if scan_type == "momentum":
        if price is not None and ma20 is not None and price > ma20:
            score += 2
        if price is not None and ma50 is not None and price > ma50:
            score += 2
        if price is not None and ma200 is not None and price > ma200:
            score += 2
        if rsi is not None and 45 <= rsi <= 70:
            score += 2
        if rel_volume is not None and rel_volume > 1.2:
            score += 1
        if five_day_change is not None and five_day_change > 0:
            score += 1

    elif scan_type == "breakouts":
        if price is not None and high_52w is not None and price >= high_52w * 0.97:
            score += 4
        if price is not None and ma50 is not None and price > ma50:
            score += 2
        if price is not None and ma200 is not None and price > ma200:
            score += 2
        if rsi is not None and 50 <= rsi <= 75:
            score += 1
        if rel_volume is not None and rel_volume > 1.2:
            score += 1

    elif scan_type == "oversold":
        if rsi is not None and rsi < 35:
            score += 4
        if rsi is not None and rsi < 30:
            score += 2
        if is_near(price, ma20) or is_near(price, ma50):
            score += 1
        if rel_volume is not None and rel_volume > 1.2:
            score += 1
        if price is not None and ma200 is not None and price < ma200 * 0.85:
            score -= 2

    elif scan_type == "pullbacks":
        if price is not None and ma200 is not None and price > ma200:
            score += 3
        if is_near(price, ma20):
            score += 2
        if is_near(price, ma50):
            score += 2
        if rsi is not None and 35 <= rsi <= 55:
            score += 2
        if ma50 is not None and ma200 is not None and ma50 > ma200:
            score += 1

    elif scan_type == "volume":
        if rel_volume is not None and rel_volume > 2.0:
            score += 5
        elif rel_volume is not None and rel_volume > 1.5:
            score += 3
        if price is not None and ma50 is not None and price > ma50:
            score += 1
        if rsi is not None and 40 <= rsi <= 70:
            score += 1

    return score


def scanner_signal(stock_data, scan_type):
    ticker = stock_data["ticker"]
    if scan_type == "momentum":
        return f"{ticker} is matching an uptrend momentum screen."
    if scan_type == "breakouts":
        return f"{ticker} is screening near its 52-week high area."
    if scan_type == "oversold":
        return f"{ticker} is showing possible oversold bounce characteristics."
    if scan_type == "pullbacks":
        return f"{ticker} is screening as an uptrend pullback candidate."
    if scan_type == "volume":
        return f"{ticker} is showing unusual relative volume."
    return f"{ticker} is a balanced watchlist candidate across multiple scanner factors."


def format_percent(value):
    if value is None:
        return "N/A"
    return f"{value:,.2f}%"


def format_multiple(value):
    if value is None:
        return "N/A"
    return f"{value:,.2f}x"


def format_money(value):
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def format_number(value):
    if value is None:
        return "N/A"
    return f"{value:,.1f}"


def format_stock_check(analysis):
    ticker = analysis["ticker"]
    return (
        f"{ticker} Stock Check\n"
        f"Price: {format_money(analysis['price'])}\n"
        f"Day High: {format_money(analysis['day_high'])}\n"
        f"Day Low: {format_money(analysis['day_low'])}\n"
        f"52W High: {format_money(analysis['fifty_two_week_high'])}\n"
        f"52W Low: {format_money(analysis['fifty_two_week_low'])}\n"
        f"RSI: {format_number(analysis['rsi'])}\n"
        f"20MA: {format_money(analysis['moving_average_20'])}\n"
        f"50MA: {format_money(analysis['moving_average_50'])}\n"
        f"200MA: {format_money(analysis['moving_average_200'])}\n"
        f"Trend: {analysis['trend']}"
    )


def format_scan_type(scan_type):
    return "Balanced" if scan_type == "balanced" else scan_type.title()


def get_download_frame(downloaded, ticker):
    """Extract one ticker frame from yfinance.download output."""
    if downloaded.empty:
        return pd.DataFrame()

    if isinstance(downloaded.columns, pd.MultiIndex):
        if ticker in downloaded.columns.get_level_values(0):
            return downloaded[ticker].dropna(how="all")
        if ticker in downloaded.columns.get_level_values(1):
            return downloaded.xs(ticker, axis=1, level=1).dropna(how="all")
        return pd.DataFrame()

    return downloaded.dropna(how="all")


def prefilter_tickers(tickers):
    """Filter the broad universe before full technical analysis."""
    unique_tickers = sorted({normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)})
    candidates = []
    batch_size = 100

    for start in range(0, len(unique_tickers), batch_size):
        batch = unique_tickers[start:start + batch_size]
        print(f"Prefiltering {min(start + batch_size, len(unique_tickers))}/{len(unique_tickers)}...")

        try:
            downloaded = yf.download(
                batch,
                period="1mo",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as error:
            print(f"Prefilter batch failed: {error}")
            continue

        for ticker in batch:
            data = get_download_frame(downloaded, ticker)
            if data.empty or "Close" not in data or "Volume" not in data:
                continue

            close_prices = data["Close"].dropna()
            volumes = data["Volume"].dropna()
            if close_prices.empty or volumes.empty:
                continue

            latest_close = _to_float(close_prices.iloc[-1])
            average_volume = _to_float(volumes.tail(20).mean())
            if latest_close is None or average_volume is None:
                continue

            if latest_close >= MIN_PRICE_FILTER and average_volume >= MIN_AVG_VOLUME_FILTER:
                candidates.append({"ticker": ticker, "average_volume": average_volume})

    candidates.sort(key=lambda item: item["average_volume"], reverse=True)
    limited_candidates = candidates[:current_universe_scan_limit]
    print(f"Prefilter kept {len(limited_candidates)} ticker(s) from {len(unique_tickers)} total.")
    return [candidate["ticker"] for candidate in limited_candidates]


def build_scanner_results(scan_type="balanced", broad=False):
    tickers = load_us_stock_universe() if broad else load_scanner_universe()
    if broad:
        tickers = prefilter_tickers(tickers)

    results = []
    total = len(tickers)
    batch_size = 50

    for start in range(0, total, batch_size):
        batch = tickers[start:start + batch_size]

        for ticker in batch:
            stock_data = analyze_stock(ticker)
            if stock_data is None:
                continue

            score = calculate_score(stock_data, scan_type)
            if score <= 0:
                continue

            stock_data["scanner_score"] = score
            stock_data["scanner_signal"] = scanner_signal(stock_data, scan_type)
            results.append(stock_data)

        if broad:
            print(f"Scanning {min(start + batch_size, total)}/{total}...")
            if start + batch_size < total:
                time.sleep(1)

    results.sort(key=lambda item: item["scanner_score"], reverse=True)
    result_limit = FULL_SCAN_RESULT_LIMIT if broad else SCAN_RESULT_LIMIT
    return results[:result_limit]


def format_scanner_results(results, scan_type, broad=False):
    title = f"Scanner Results — {format_scan_type(scan_type)}"
    title_prefix = "Broad Scanner Results" if broad else "Scanner Results"
    title = f"{title_prefix} — {format_scan_type(scan_type)}"
    title = f"{title_prefix} \u2014 {format_scan_type(scan_type)}"
    if not results:
        return (
            f"{title}\n"
            "No scanner results matched strongly enough right now.\n\n"
            f"{SCANNER_DISCLAIMER}"
        )

    lines = [title]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"{index}. {result['ticker']}",
                f"   Price: {format_money(result['price'])}",
                f"   RSI: {format_number(result['rsi'])}",
                f"   Trend: {result['trend']}",
                f"   Rel Volume: {format_multiple(result['relative_volume'])}",
                f"   5D Change: {format_percent(result['five_day_change'])}",
                f"   Signal: {result['scanner_signal']}",
                f"   Score: {result['scanner_score']}",
            ]
        )

    lines.extend(["", SCANNER_DISCLAIMER])
    return "\n".join(lines)


def format_integer(value):
    if value is None:
        return "N/A"
    return f"{value:,.0f}"


def custom_scan_help_message():
    return (
        "Custom scan examples:\n"
        "!scan custom rsi>70\n"
        "!scan custom rsi<30 price>5 volume>1000000\n"
        "!scan custom rsi=45-65 above50ma=true above200ma=true\n"
        "!scan custom relvol>1.5 change5d>3\n"
        "!scan all custom rsi<35 price>10 volume>500000\n\n"
        f"{SCANNER_DISCLAIMER}"
    )


def parse_number(value, token):
    try:
        return float(value)
    except ValueError:
        raise ValueError(
            f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
        )


def parse_custom_filters(filter_text: str) -> dict:
    """Parse custom scanner filters typed in Discord."""
    parsed_filters = []
    errors = []
    tokens = filter_text.split()

    for token in tokens:
        operator = None
        for possible_operator in [">", "<", "="]:
            if possible_operator in token:
                operator = possible_operator
                break

        if operator is None:
            errors.append(
                f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
            )
            continue

        key, raw_value = token.split(operator, 1)
        key = key.lower().strip()
        raw_value = raw_value.lower().strip()

        if key not in CUSTOM_FILTER_KEYS:
            errors.append(f"Unknown filter: {token}. Use !scanhelp to see supported filters.")
            continue

        if not raw_value:
            errors.append(
                f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
            )
            continue

        if key in {"above20ma", "above50ma", "above200ma", "below20ma", "below50ma", "below200ma"}:
            if operator != "=" or raw_value not in {"true", "false"}:
                errors.append(
                    f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
                )
                continue
            parsed_filters.append({"key": key, "operator": operator, "value": raw_value == "true", "token": token})
            continue

        if key == "trend":
            if operator != "=" or raw_value not in {"bullish", "bearish", "mixed", "high-risk"}:
                errors.append(
                    f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
                )
                continue
            parsed_filters.append({"key": key, "operator": operator, "value": raw_value, "token": token})
            continue

        if key in {"near52whigh", "near52wlow"} and operator != "<":
            errors.append(
                f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
            )
            continue

        if operator == "=":
            range_match = re.fullmatch(r"(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)", raw_value)
            if range_match is None:
                errors.append(
                    f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
                )
                continue

            low_text, high_text = range_match.groups()
            try:
                low = parse_number(low_text, token)
                high = parse_number(high_text, token)
            except ValueError as error:
                errors.append(str(error))
                continue

            if low > high:
                errors.append(
                    f"Invalid filter format: {token}. Example valid filters: rsi>70, price>10, rsi=40-60."
                )
                continue

            parsed_filters.append({"key": key, "operator": "range", "low": low, "high": high, "token": token})
            continue

        try:
            value = parse_number(raw_value, token)
        except ValueError as error:
            errors.append(str(error))
            continue

        parsed_filters.append({"key": key, "operator": operator, "value": value, "token": token})

    return {"filters": parsed_filters, "errors": errors, "text": filter_text}


def custom_metric_value(stock_data, key):
    metric_map = {
        "rsi": "rsi",
        "price": "latest_price",
        "volume": "current_volume",
        "relvol": "relative_volume",
        "change5d": "change_5d_percent",
        "near52whigh": "percent_below_52w_high",
        "near52wlow": "percent_above_52w_low",
    }
    return stock_data.get(metric_map[key])


def moving_average_filter_matches(stock_data, key, expected):
    latest_price = stock_data.get("latest_price")
    ma_key = key.replace("above", "").replace("below", "")
    ma_map = {"20ma": "ma20", "50ma": "ma50", "200ma": "ma200"}
    moving_average = stock_data.get(ma_map[ma_key])
    if latest_price is None or moving_average is None:
        return False

    actual = latest_price > moving_average if key.startswith("above") else latest_price < moving_average
    return actual == expected


def filter_matches(stock_data, custom_filter):
    key = custom_filter["key"]
    operator = custom_filter["operator"]

    if key.startswith("above") or key.startswith("below"):
        return moving_average_filter_matches(stock_data, key, custom_filter["value"])

    if key == "trend":
        return stock_data.get("trend", "").lower() == custom_filter["value"]

    value = custom_metric_value(stock_data, key)
    if value is None:
        return False

    if operator == ">":
        return value > custom_filter["value"]
    if operator == "<":
        return value < custom_filter["value"]
    if operator == "range":
        return custom_filter["low"] <= value <= custom_filter["high"]

    return False


def custom_reason(stock_data, custom_filter):
    key = custom_filter["key"]
    token = custom_filter["token"]

    if key == "near52whigh":
        return f"{format_percent(stock_data.get('percent_below_52w_high'))} below 52-week high matched {token}"
    if key == "near52wlow":
        return f"{format_percent(stock_data.get('percent_above_52w_low'))} above 52-week low matched {token}"
    if key == "volume":
        return f"Volume {format_integer(stock_data.get('current_volume'))} matched {token}"
    if key == "relvol":
        return f"Relative volume {format_multiple(stock_data.get('relative_volume'))} matched {token}"
    if key == "change5d":
        return f"5D change {format_percent(stock_data.get('change_5d_percent'))} matched {token}"
    if key == "price":
        return f"Price {format_money(stock_data.get('latest_price'))} matched {token}"
    if key == "rsi":
        return f"RSI {format_number(stock_data.get('rsi'))} matched {token}"
    if key == "trend":
        return f"Trend {stock_data.get('trend')} matched {token}"
    return f"{token} matched"


def stock_matches_custom_filters(stock_data: dict, filters: dict) -> tuple[bool, list[str]]:
    """Return whether a stock matches custom filters and explain why."""
    reasons = []
    for custom_filter in filters["filters"]:
        if not filter_matches(stock_data, custom_filter):
            return False, []
        reasons.append(custom_reason(stock_data, custom_filter))

    return True, reasons


def score_custom_match(stock_data: dict, filters: dict) -> float:
    """Score custom matches as research ranking, not buy or sell scoring."""
    score = len(filters["filters"])

    if stock_data.get("relative_volume") is not None and stock_data["relative_volume"] > 1.5:
        score += 1
    if stock_data.get("current_volume") is not None and stock_data["current_volume"] > 1000000:
        score += 1

    trend_filter = next((item for item in filters["filters"] if item["key"] == "trend"), None)
    if trend_filter and trend_filter["value"] == "bullish" and stock_data.get("trend") == "Bullish":
        score += 1

    rsi_filter = next((item for item in filters["filters"] if item["key"] == "rsi"), None)
    rsi = stock_data.get("rsi")
    if rsi_filter and rsi is not None:
        explicitly_extreme = (
            (rsi_filter["operator"] == ">" and rsi_filter["value"] >= 70)
            or (rsi_filter["operator"] == "<" and rsi_filter["value"] <= 30)
        )
        if rsi_filter["operator"] == "range":
            explicitly_extreme = rsi_filter["low"] >= 70 or rsi_filter["high"] <= 30
        if not explicitly_extreme and 30 < rsi < 70:
            score += 1

    return float(score)


def build_custom_scanner_results(filters, broad=False):
    tickers = load_us_stock_universe() if broad else load_scanner_universe()
    if broad:
        tickers = prefilter_tickers(tickers)

    results = []
    total = len(tickers)
    batch_size = 50

    for start in range(0, total, batch_size):
        batch = tickers[start:start + batch_size]

        for ticker in batch:
            stock_data = analyze_stock(ticker)
            if stock_data is None:
                continue

            matches, reasons = stock_matches_custom_filters(stock_data, filters)
            if not matches:
                continue

            stock_data["custom_score"] = score_custom_match(stock_data, filters)
            stock_data["match_reasons"] = reasons
            results.append(stock_data)

        if broad:
            print(f"Scanning {min(start + batch_size, total)}/{total}...")
            if start + batch_size < total:
                time.sleep(1)

    results.sort(key=lambda item: item["custom_score"], reverse=True)
    return {
        "results": results[:CUSTOM_SCAN_RESULT_LIMIT],
        "scanned_count": total,
        "match_count": len(results),
    }


def format_custom_scanner_results(scan_data, filters, broad=False):
    source = "broad US universe" if broad else "scanner list"
    lines = [
        "Custom Scanner Results",
        f"Scan source: {source}",
        f"Filters used: {filters['text']}",
        f"Number of tickers scanned: {scan_data['scanned_count']}",
        f"Number of matches found: {scan_data['match_count']}",
        "",
    ]

    if not scan_data["results"]:
        lines.extend(["No custom scanner results matched right now.", "", SCANNER_DISCLAIMER])
        return "\n".join(lines)

    for index, result in enumerate(scan_data["results"], start=1):
        lines.extend(
            [
                f"{index}. {result['ticker']}",
                f"   Price: {format_money(result['latest_price'])}",
                f"   RSI: {format_number(result['rsi'])}",
                f"   Trend: {result['trend']}",
                f"   Rel Volume: {format_multiple(result['relative_volume'])}",
                f"   Volume: {format_integer(result['current_volume'])}",
                f"   5D Change: {format_percent(result['change_5d_percent'])}",
                f"   Match Reasons: {'; '.join(result['match_reasons'])}",
                f"   Score: {result['custom_score']}",
            ]
        )

    lines.extend(["", SCANNER_DISCLAIMER])
    return "\n".join(lines)


def market_now():
    return datetime.now(ZoneInfo(MARKET_TIMEZONE))


def is_weekday_market_day_now():
    """Return True Monday through Friday in Pacific time."""
    return market_now().weekday() < 5


def should_run_eod_summary():
    """Return True when the EOD summary should run for the day."""
    if not is_weekday_market_day_now():
        return False

    now = market_now()
    scheduled_time = datetime_time(EOD_SUMMARY_HOUR, EOD_SUMMARY_MINUTE)
    if now.time() < scheduled_time:
        return False

    return last_eod_summary_date != now.date().isoformat()


def eod_flags(analysis):
    flags = []
    price = analysis.get("latest_price")

    if price is None:
        return flags

    if analysis.get("day_high") is not None and price >= analysis["day_high"] * NEAR_DAILY_HIGH_THRESHOLD:
        flags.append("Near daily high")
    if analysis.get("day_low") is not None and price <= analysis["day_low"] * NEAR_DAILY_LOW_THRESHOLD:
        flags.append("Near daily low")
    if analysis.get("year_high") is not None and price >= analysis["year_high"] * NEAR_52W_HIGH_THRESHOLD:
        flags.append("Near 52-week high")
    if analysis.get("year_low") is not None and price <= analysis["year_low"] * NEAR_52W_LOW_THRESHOLD:
        flags.append("Near 52-week low")
    if analysis.get("rsi") is not None and analysis["rsi"] >= RSI_OVERBOUGHT:
        flags.append("RSI overbought")
    if analysis.get("rsi") is not None and analysis["rsi"] <= RSI_OVERSOLD:
        flags.append("RSI oversold")
    if analysis.get("volume_spike"):
        flags.append("Volume spike")

    return flags


def day_range_percent(analysis):
    high = analysis.get("day_high")
    low = analysis.get("day_low")
    if high is None or low is None or low <= 0:
        return None
    return ((high - low) / low) * 100


def build_eod_summary():
    """Build the end-of-day watchlist summary text."""
    tickers = load_watchlist()
    today = market_now().date().isoformat()
    analyses = []

    for ticker in tickers:
        analysis = analyze_stock(ticker)
        if analysis is None:
            analyses.append({"ticker": ticker, "error": True})
            continue
        analyses.append(analysis)

    bullish_count = sum(1 for item in analyses if item.get("trend") == "Bullish")
    bearish_count = sum(1 for item in analyses if item.get("trend") == "Bearish")
    mixed_count = sum(1 for item in analyses if item.get("trend") == "Mixed")
    overbought_count = sum(
        1 for item in analyses if item.get("rsi") is not None and item["rsi"] >= RSI_OVERBOUGHT
    )
    oversold_count = sum(
        1 for item in analyses if item.get("rsi") is not None and item["rsi"] <= RSI_OVERSOLD
    )
    volume_spike_count = sum(1 for item in analyses if item.get("volume_spike"))

    lines = [
        f"End-of-Day Watchlist Summary \u2014 {today}",
        "",
        f"Watchlist Count: {len(tickers)}",
        f"Bullish Trends: {bullish_count}",
        f"Bearish Trends: {bearish_count}",
        f"Mixed Trends: {mixed_count}",
        f"Overbought RSI Count: {overbought_count}",
        f"Oversold RSI Count: {oversold_count}",
        f"Volume Spike Count: {volume_spike_count}",
    ]

    for item in analyses:
        ticker = item["ticker"]
        if item.get("error"):
            lines.extend(["", f"{ticker}", "Data unavailable for this summary."])
            continue

        flags = eod_flags(item)
        lines.extend(
            [
                "",
                f"{ticker}",
                f"Price: {format_money(item['latest_price'])}",
                f"Day High: {format_money(item['day_high'])}",
                f"Day Low: {format_money(item['day_low'])}",
                f"Day Range: {format_percent(day_range_percent(item))}",
                f"RSI: {format_number(item['rsi'])}",
                f"Rel Volume: {format_multiple(item['relative_volume'])}",
                f"5D Change: {format_percent(item['change_5d_percent'])}",
                f"Trend: {item['trend']}",
                f"Notable Flags: {', '.join(flags) if flags else 'None'}",
            ]
        )

    lines.extend(["", "End-of-day summaries are for tracking and research only and are not financial advice."])
    return "\n".join(lines)


def eod_target_channel(guild):
    channel = get_channel_by_name(guild, EOD_SUMMARY_CHANNEL)
    if channel is not None:
        return channel

    print(f"Warning: #{EOD_SUMMARY_CHANNEL} is missing. Trying #stock-alerts instead.")
    fallback = get_channel_by_name(guild, "stock-alerts")
    if fallback is None:
        print("Warning: #stock-alerts is also missing. EOD summary was not sent.")
    return fallback


def utc_timestamp():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json_file(path, default_data):
    if not os.path.exists(path):
        return default_data

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        print(f"Could not read {path}: {error}")
        return default_data


def write_json_file(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def reddit_credentials_configured():
    return all(
        [
            os.getenv("REDDIT_CLIENT_ID"),
            os.getenv("REDDIT_CLIENT_SECRET"),
            os.getenv("REDDIT_USER_AGENT"),
        ]
    )


def get_reddit_client():
    """Create a read-only Reddit client when credentials are configured."""
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")

    if not client_id or not client_secret or not user_agent:
        print(
            "Reddit credentials are missing. Add REDDIT_CLIENT_ID, "
            "REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT to your .env file."
        )
        return None

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    reddit.read_only = True
    return reddit


def load_valid_wsb_tickers():
    valid_tickers = set(load_scanner_universe())
    valid_tickers.update(load_us_stock_universe())
    valid_tickers.update(load_watchlist())
    valid_tickers.update(FALLBACK_WSB_TICKERS)
    return {normalize_ticker(ticker) for ticker in valid_tickers if normalize_ticker(ticker)}


def extract_tickers_from_text(text):
    """Extract normalized ticker symbols from WSB text."""
    if not text:
        return []

    valid_tickers = load_valid_wsb_tickers()
    candidates = re.findall(r"(?<![A-Za-z0-9])\$?([A-Z]{1,5})(?![A-Za-z0-9])", text)
    tickers = []

    for candidate in candidates:
        ticker = normalize_ticker(candidate)
        if ticker in WSB_FALSE_POSITIVES:
            continue
        if ticker not in valid_tickers:
            continue
        tickers.append(ticker)

    return sorted(set(tickers))


def fetch_wsb_posts():
    """Fetch recent r/wallstreetbets posts through PRAW."""
    reddit = get_reddit_client()
    if reddit is None:
        return []

    subreddit = reddit.subreddit(WSB_SUBREDDIT)
    posts_by_id = {}
    per_listing_limit = max(1, WSB_POST_LIMIT // 2)

    for submission in subreddit.new(limit=per_listing_limit):
        posts_by_id[submission.id] = submission
    for submission in subreddit.hot(limit=WSB_POST_LIMIT - len(posts_by_id)):
        posts_by_id[submission.id] = submission

    cutoff = datetime.utcnow().timestamp() - (WSB_MENTION_LOOKBACK_MINUTES * 60)
    posts = []

    for submission in posts_by_id.values():
        if submission.created_utc < cutoff:
            continue

        posts.append(
            {
                "title": submission.title or "",
                "selftext": submission.selftext or "",
                "score": int(submission.score or 0),
                "num_comments": int(submission.num_comments or 0),
                "created_utc": float(submission.created_utc),
                "permalink": f"https://www.reddit.com{submission.permalink}",
            }
        )

    return posts


def update_wsb_mentions():
    """Update WSB mention counts from recent posts."""
    posts = fetch_wsb_posts()
    mention_map = {}

    for post in posts:
        text = f"{post['title']}\n{post['selftext']}"
        tickers = extract_tickers_from_text(text)

        for ticker in tickers:
            if ticker not in mention_map:
                mention_map[ticker] = {
                    "ticker": ticker,
                    "mention_count": 0,
                    "unique_post_count": 0,
                    "total_score": 0,
                    "total_comments": 0,
                    "example_post_titles": [],
                    "last_seen": utc_timestamp(),
                }

            mention = mention_map[ticker]
            mention["mention_count"] += text.upper().count(ticker)
            mention["unique_post_count"] += 1
            mention["total_score"] += post["score"]
            mention["total_comments"] += post["num_comments"]
            if len(mention["example_post_titles"]) < 3:
                mention["example_post_titles"].append(post["title"])
            mention["last_seen"] = utc_timestamp()

    mentions = sorted(
        mention_map.values(),
        key=lambda item: (item["mention_count"], item["unique_post_count"], item["total_comments"]),
        reverse=True,
    )

    data = {
        "timestamp": utc_timestamp(),
        "subreddit": WSB_SUBREDDIT,
        "lookback_minutes": WSB_MENTION_LOOKBACK_MINUTES,
        "mentions": mentions,
    }
    write_json_file(WSB_MENTIONS_FILE, data)
    return mentions


def load_wsb_mentions():
    return read_json_file(
        WSB_MENTIONS_FILE,
        {"timestamp": None, "subreddit": WSB_SUBREDDIT, "mentions": []},
    )


def load_wsb_tracking():
    return read_json_file(
        WSB_TRACKING_FILE,
        {"timestamp": None, "subreddit": WSB_SUBREDDIT, "tracked": []},
    )


def update_wsb_tracking():
    """Update tracked WSB tickers with market measurements."""
    mentions_data = load_wsb_mentions()
    previous_tickers = {item.get("ticker") for item in load_wsb_tracking().get("tracked", [])}
    eligible_mentions = [
        mention
        for mention in mentions_data.get("mentions", [])
        if mention.get("mention_count", 0) >= WSB_MIN_MENTIONS_TO_TRACK
    ][:WSB_MAX_TRACKED_TICKERS]

    tracked = []
    for mention in eligible_mentions:
        ticker = mention["ticker"]
        analysis = analyze_stock(ticker)
        if analysis is None:
            continue

        tracked.append(
            {
                "timestamp": utc_timestamp(),
                "ticker": ticker,
                "price": analysis.get("latest_price"),
                "rsi": analysis.get("rsi"),
                "relative_volume": analysis.get("relative_volume"),
                "change_5d_percent": analysis.get("change_5d_percent"),
                "trend": analysis.get("trend"),
                "mention_count": mention.get("mention_count", 0),
                "unique_post_count": mention.get("unique_post_count", 0),
                "total_score": mention.get("total_score", 0),
                "total_comments": mention.get("total_comments", 0),
                "first_appearance": ticker not in previous_tickers,
            }
        )

    data = {
        "timestamp": utc_timestamp(),
        "subreddit": WSB_SUBREDDIT,
        "tracked": tracked,
    }
    write_json_file(WSB_TRACKING_FILE, data)
    return tracked


def format_wsb_mentions(mentions, limit=10):
    lines = ["WSB Mentions — Last Scan"]
    if not mentions:
        lines.append("No ticker mentions found in the latest scan.")
    for index, mention in enumerate(mentions[:limit], start=1):
        example = mention.get("example_post_titles", ["N/A"])[0]
        lines.extend(
            [
                "",
                f"{index}. {mention['ticker']}",
                f"   Mentions: {mention['mention_count']}",
                f"   Posts: {mention['unique_post_count']}",
                f"   Total Score: {mention['total_score']}",
                f"   Comments: {mention['total_comments']}",
                f"   Example: {example}",
            ]
        )

    lines.extend(["", WSB_DISCLAIMER])
    return "\n".join(lines)


def format_wsb_tracking(tracked, timestamp=None, limit=10):
    timestamp = timestamp or utc_timestamp()
    lines = [f"WSB Tracking Measurements — {timestamp}"]
    if not tracked:
        lines.append("No WSB tickers are currently being tracked.")
    for index, item in enumerate(tracked[:limit], start=1):
        lines.extend(
            [
                "",
                f"{index}. {item['ticker']}",
                f"   Price: {format_money(item['price'])}",
                f"   RSI: {format_number(item['rsi'])}",
                f"   Rel Volume: {format_multiple(item['relative_volume'])}",
                f"   5D Change: {format_percent(item['change_5d_percent'])}",
                f"   Trend: {item['trend']}",
                f"   Mentions: {item['mention_count']}",
                f"   Posts: {item['unique_post_count']}",
            ]
        )

    lines.extend(["", WSB_DISCLAIMER])
    return "\n".join(lines)


def wsb_alert_reasons(item):
    reasons = []
    if item.get("mention_count", 0) >= 10:
        reasons.append("High mention count")
    if item.get("relative_volume") is not None and item["relative_volume"] >= 2.0:
        reasons.append("relative volume spike")
    if item.get("rsi") is not None and item["rsi"] >= 75:
        reasons.append("RSI extreme")
    if item.get("rsi") is not None and item["rsi"] <= 25:
        reasons.append("RSI extreme")
    if item.get("first_appearance") and item.get("mention_count", 0) >= 5:
        reasons.append("first appearance")
    return reasons


def format_wsb_alert(item, reasons):
    return (
        f"WSB Attention Alert — {item['ticker']}\n\n"
        "Reason:\n"
        + "\n".join(f"- {reason}" for reason in reasons)
        + "\n\nMarket data:\n"
        f"Price: {format_money(item['price'])}\n"
        f"RSI: {format_number(item['rsi'])}\n"
        f"Rel Volume: {format_multiple(item['relative_volume'])}\n"
        f"5D Change: {format_percent(item['change_5d_percent'])}\n"
        f"Trend: {item['trend']}\n\n"
        "Reddit data:\n"
        f"Mentions: {item['mention_count']}\n"
        f"Posts: {item['unique_post_count']}\n"
        f"Comments: {item['total_comments']}\n"
        f"Total Score: {item['total_score']}\n\n"
        f"{WSB_DISCLAIMER}"
    )


def clear_wsb_files():
    write_json_file(
        WSB_MENTIONS_FILE,
        {"timestamp": None, "subreddit": WSB_SUBREDDIT, "mentions": []},
    )
    write_json_file(
        WSB_TRACKING_FILE,
        {"timestamp": None, "subreddit": WSB_SUBREDDIT, "tracked": []},
    )


def get_channel_by_name(guild, channel_name):
    """Find a text channel by name."""
    return discord.utils.get(guild.text_channels, name=channel_name)


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


async def send_long_message(destination, message):
    """Send long Discord messages in chunks without cutting entries awkwardly."""
    if len(message) <= 1900:
        await destination.send(message)
        return

    chunk = ""
    paragraphs = message.split("\n\n")
    for paragraph in paragraphs:
        addition = paragraph if not chunk else f"\n\n{paragraph}"
        if len(chunk) + len(addition) > 1900:
            if chunk:
                await destination.send(chunk)
                chunk = paragraph
            else:
                for start in range(0, len(paragraph), 1900):
                    await destination.send(paragraph[start:start + 1900])
                chunk = ""
        else:
            chunk += addition

    if chunk:
        await destination.send(chunk)


async def send_stock_alert(guild, channel_name, message):
    channel = get_channel_by_name(guild, channel_name)
    if channel is None and channel_name != "stock-alerts":
        channel = get_channel_by_name(guild, "stock-alerts")

    if channel is None:
        print(f"Missing alert channel #{channel_name}, and #stock-alerts was not found.")
        return

    await channel.send(message)


def should_send_alert(ticker, alert_type):
    today = datetime.now().date().isoformat()
    alert_key = f"{ticker}:{alert_type}:{today}"
    if alert_key in sent_alerts:
        return False

    sent_alerts.add(alert_key)
    return True


async def process_stock_alerts(guild, analysis):
    ticker = analysis["ticker"]
    price = analysis["price"]
    if price is None:
        return

    if should_send_alert(ticker, "summary"):
        await send_stock_alert(
            guild,
            "stock-alerts",
            f"{ticker} summary: {format_money(price)} | RSI {format_number(analysis['rsi'])} | Trend: {analysis['trend']}",
        )

    if (
        analysis["day_high"] is not None
        and price >= analysis["day_high"] * NEAR_DAILY_HIGH_THRESHOLD
        and should_send_alert(ticker, "daily_high")
    ):
        await send_stock_alert(
            guild,
            "daily-highs",
            f"{ticker} is near its daily high. Price: {format_money(price)}, Day High: {format_money(analysis['day_high'])}",
        )

    if (
        analysis["day_low"] is not None
        and price <= analysis["day_low"] * NEAR_DAILY_LOW_THRESHOLD
        and should_send_alert(ticker, "daily_low")
    ):
        await send_stock_alert(
            guild,
            "daily-lows",
            f"{ticker} is near its daily low. Price: {format_money(price)}, Day Low: {format_money(analysis['day_low'])}",
        )

    if (
        analysis["fifty_two_week_high"] is not None
        and price >= analysis["fifty_two_week_high"] * NEAR_52W_HIGH_THRESHOLD
        and should_send_alert(ticker, "52w_high")
    ):
        await send_stock_alert(
            guild,
            "fifty-two-week-highs",
            f"{ticker} is near its 52-week high. Price: {format_money(price)}, 52W High: {format_money(analysis['fifty_two_week_high'])}",
        )

    if (
        analysis["fifty_two_week_low"] is not None
        and price <= analysis["fifty_two_week_low"] * NEAR_52W_LOW_THRESHOLD
        and should_send_alert(ticker, "52w_low")
    ):
        await send_stock_alert(
            guild,
            "fifty-two-week-lows",
            f"{ticker} is near its 52-week low. Price: {format_money(price)}, 52W Low: {format_money(analysis['fifty_two_week_low'])}",
        )

    if analysis["rsi"] is not None and analysis["rsi"] >= RSI_OVERBOUGHT:
        if should_send_alert(ticker, "rsi_overbought"):
            await send_stock_alert(
                guild,
                "rsi-alerts",
                f"{ticker} may be overbought. RSI: {format_number(analysis['rsi'])}",
            )

    if analysis["rsi"] is not None and analysis["rsi"] <= RSI_OVERSOLD:
        if should_send_alert(ticker, "rsi_oversold"):
            await send_stock_alert(
                guild,
                "rsi-alerts",
                f"{ticker} may be oversold. RSI: {format_number(analysis['rsi'])}",
            )

    if analysis["volume_spike"] and should_send_alert(ticker, "volume_spike"):
        await send_stock_alert(
            guild,
            "volume-spikes",
            f"{ticker} has a possible volume spike. Latest 5-minute volume: {format_number(analysis['latest_volume'])}, Average: {format_number(analysis['average_volume'])}",
        )


@tasks.loop(hours=24)
async def daily_scanner_loop():
    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("Daily scanner could not find the configured Discord server.")
        return

    results = await asyncio.to_thread(build_scanner_results, "balanced")
    message = format_scanner_results(results, "balanced")
    channel = get_channel_by_name(guild, "stock-ideas")
    if channel is None:
        print("Daily scanner could not find #stock-ideas.")
        return

    await channel.send(message)


@daily_scanner_loop.before_loop
async def before_daily_scanner_loop():
    await bot.wait_until_ready()
    now = datetime.now()
    next_run = now.replace(hour=13, minute=15, second=0, microsecond=0)
    if now >= next_run:
        next_run = next_run + timedelta(days=1)
    await asyncio.sleep((next_run - now).total_seconds())


@tasks.loop(minutes=ALERT_INTERVAL_MINUTES)
async def stock_alert_loop():
    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("Stock alert loop could not find the configured Discord server.")
        return

    tickers = load_watchlist()
    if not tickers:
        print("Stock alert loop skipped because the watchlist is empty.")
        return

    print(f"Running stock alert check for {len(tickers)} ticker(s).")

    for ticker in tickers:
        analysis = await asyncio.to_thread(analyze_stock, ticker)
        if analysis is None:
            print(f"Skipping {ticker}; no valid stock data was returned.")
            continue

        await process_stock_alerts(guild, analysis)


@tasks.loop(minutes=5)
async def eod_summary_loop():
    global last_eod_summary_date

    if not should_run_eod_summary():
        return

    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("EOD summary loop could not find the configured Discord server.")
        return

    channel = eod_target_channel(guild)
    if channel is None:
        return

    summary = await asyncio.to_thread(build_eod_summary)
    await send_long_message(channel, summary)
    last_eod_summary_date = market_now().date().isoformat()
    print(f"EOD summary sent for {last_eod_summary_date}.")


@tasks.loop(minutes=WSB_CHECK_INTERVAL_MINUTES)
async def wsb_tracker_loop():
    if not reddit_credentials_configured():
        print("WSB tracker skipped because Reddit credentials are not configured.")
        return

    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("WSB tracker could not find the configured Discord server.")
        return

    try:
        mentions = await asyncio.to_thread(update_wsb_mentions)
        tracked = await asyncio.to_thread(update_wsb_tracking)
    except Exception as error:
        print(f"WSB tracker update failed: {error}")
        return

    mentions_channel = get_channel_by_name(guild, "wsb-mentions")
    tracking_channel = get_channel_by_name(guild, "wsb-tracking")
    alerts_channel = get_channel_by_name(guild, "wsb-alerts")

    if mentions_channel:
        await send_long_message(mentions_channel, format_wsb_mentions(mentions))
    else:
        print("Warning: #wsb-mentions is missing.")

    if tracking_channel:
        await send_long_message(tracking_channel, format_wsb_tracking(tracked))
    else:
        print("Warning: #wsb-tracking is missing.")

    if alerts_channel:
        for item in tracked:
            reasons = wsb_alert_reasons(item)
            if reasons:
                await send_long_message(alerts_channel, format_wsb_alert(item, reasons))
    else:
        print("Warning: #wsb-alerts is missing.")


async def setup_stock_channels(guild):
    """Create the stock bot category and channels if they do not exist."""
    if guild is None:
        print("Error: Could not find the Discord server. Check GUILD_ID.")
        return None

    bot_member = guild.me
    if bot_member is None:
        bot_member = guild.get_member(bot.user.id)

    permissions = bot_member.guild_permissions if bot_member else None
    if permissions is None or not permissions.manage_channels:
        print(
            "Error: The bot needs the Manage Channels permission to create "
            "categories and channels."
        )
        return None

    category = discord.utils.get(guild.categories, name=CATEGORY_NAME)

    if category:
        print(f'Category already exists: "{CATEGORY_NAME}"')
    else:
        category = await guild.create_category(CATEGORY_NAME)
        print(f'Created category: "{CATEGORY_NAME}"')

    created_channels = []
    existing_channels = []

    for channel_name in CHANNEL_NAMES:
        channel = discord.utils.get(category.text_channels, name=channel_name)

        if channel:
            print(f"Channel already exists: #{channel_name}")
            existing_channels.append(channel)
            continue

        channel = await guild.create_text_channel(channel_name, category=category)
        print(f"Created channel: #{channel_name}")
        created_channels.append(channel)

    return {
        "category": category,
        "created_channels": created_channels,
        "existing_channels": existing_channels,
    }


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print(
            "Error: The bot could not find that server. Make sure the bot is "
            "invited and GUILD_ID is correct."
        )
        return

    print(f"Connected to server: {guild.name} (ID: {guild.id})")

    try:
        await setup_stock_channels(guild)
    except discord.Forbidden:
        print(
            "Error: Discord denied the channel setup request. Give the bot "
            "Manage Channels permission and try again."
        )
    except discord.HTTPException as error:
        print(f"Error: Discord returned an API error during setup: {error}")

    if not stock_alert_loop.is_running():
        stock_alert_loop.start()
        print(f"Stock alert loop started. Checking every {ALERT_INTERVAL_MINUTES} minutes.")

    if not eod_summary_loop.is_running():
        eod_summary_loop.start()
        print(
            f"EOD summary loop started. Scheduled for "
            f"{EOD_SUMMARY_HOUR:02d}:{EOD_SUMMARY_MINUTE:02d} Pacific."
        )

    if not wsb_tracker_loop.is_running():
        wsb_tracker_loop.start()
        print(f"WSB tracker loop started. Checking every {WSB_CHECK_INTERVAL_MINUTES} minutes.")

    if ENABLE_DAILY_SCANNER and not daily_scanner_loop.is_running():
        daily_scanner_loop.start()
        print("Daily scanner loop started.")


@bot.command(name="setup")
@commands.guild_only()
async def setup_command(ctx):
    """Manually run the stock bot channel setup."""
    try:
        result = await setup_stock_channels(ctx.guild)
    except discord.Forbidden:
        print("Error: Missing Manage Channels permission.")
        await ctx.send("I need Manage Channels permission to set up channels.")
        return
    except discord.HTTPException as error:
        print(f"Error: Discord returned an API error during setup: {error}")
        await ctx.send("Discord returned an error while setting up channels.")
        return

    if result is None:
        await ctx.send("Channel setup could not run. Check the bot terminal.")
        return

    created_count = len(result["created_channels"])
    existing_count = len(result["existing_channels"])
    await ctx.send(
        f"Setup complete. Created {created_count} channel(s); "
        f"{existing_count} already existed."
    )


@bot.command(name="channels")
@commands.guild_only()
async def channels_command(ctx):
    """List the stock bot channels."""
    category = discord.utils.get(ctx.guild.categories, name=CATEGORY_NAME)

    if category is None:
        await ctx.send('No "Stock Market Bot" category found. Run `!setup` first.')
        return

    channel_mentions = []
    for channel_name in CHANNEL_NAMES:
        channel = discord.utils.get(category.text_channels, name=channel_name)
        if channel:
            channel_mentions.append(channel.mention)

    if not channel_mentions:
        await ctx.send("No stock bot channels were found. Run `!setup` first.")
        return

    await ctx.send("Stock bot channels:\n" + "\n".join(channel_mentions))


@bot.command(name="ping")
async def ping_command(ctx):
    """Confirm that the bot is online."""
    await ctx.send("Stock bot is online.")


def command_help_text():
    return (
        "Stock bot commands:\n"
        "!setup - Create the stock bot channel structure\n"
        "!channels - List stock bot channels\n"
        "!ping - Check whether the bot is online\n"
        "!add AAPL - Add a ticker to the watchlist\n"
        "!remove AAPL - Remove a ticker from the watchlist\n"
        "!watchlist - Show tracked tickers\n"
        "!check AAPL - Check one ticker\n"
        "!alerts - Show alert loop status\n"
        "!scan momentum - Run a preset scanner\n"
        "!scan custom rsi>70 - Run a custom scanner\n"
        "!scan all momentum - Run a broad scanner\n"
        "!scanhelp - Show custom scan filters\n"
        "!eodsummary - Manually post the end-of-day watchlist summary\n"
        "!eodstatus - Show EOD summary schedule/status\n"
        "!seteodtime 13 30 - Set EOD summary time for this bot session\n"
        "!wsbstatus - Show WallStreetBets tracker status\n"
        "!wsbmentions - Show current WSB ticker mentions\n"
        "!wsbtrack - Show WSB-tracked tickers with market measurements\n"
        "!wsbrefresh - Manually refresh WSB mentions and tracking\n"
        "!wsbsettings - Show WSB tracker settings\n"
        "!wsbclear - Clear WSB tracker JSON files"
    )


@bot.command(name="help")
async def help_command(ctx):
    """Show bot commands."""
    await ctx.send(command_help_text())


@bot.command(name="commands")
async def commands_command(ctx):
    """Show bot commands."""
    await ctx.send(command_help_text())


@bot.command(name="about")
async def about_command(ctx):
    """Show a short description of the bot."""
    await ctx.send(
        "This is one Discord stock research bot with watchlists, scanners, custom scans, "
        "end-of-day summaries, and WallStreetBets mention tracking.\n\n"
        f"{SCANNER_DISCLAIMER}\n{WSB_DISCLAIMER}"
    )


@bot.command(name="add")
@commands.guild_only()
async def add_command(ctx, ticker=None):
    """Add a ticker to the watchlist."""
    ticker = normalize_ticker(ticker)
    tickers = load_watchlist()

    if not ticker:
        await ctx.send("Please provide a ticker, like `!add AAPL`.")
        return

    if ticker in tickers:
        await ctx.send(f"{ticker} is already in the watchlist.")
        return

    tickers.append(ticker)
    save_watchlist(tickers)
    await ctx.send(f"Added {ticker} to the watchlist.")


@bot.command(name="remove")
@commands.guild_only()
async def remove_command(ctx, ticker=None):
    """Remove a ticker from the watchlist."""
    ticker = normalize_ticker(ticker)
    tickers = load_watchlist()

    if not ticker:
        await ctx.send("Please provide a ticker, like `!remove TSLA`.")
        return

    if ticker not in tickers:
        await ctx.send(f"{ticker} is not in the watchlist.")
        return

    tickers.remove(ticker)
    save_watchlist(tickers)
    await ctx.send(f"Removed {ticker} from the watchlist.")


@bot.command(name="watchlist")
@commands.guild_only()
async def watchlist_command(ctx):
    """Display all currently tracked tickers."""
    tickers = load_watchlist()

    if not tickers:
        await ctx.send("Current watchlist:\nNo tickers are being tracked.")
        return

    await ctx.send("Current watchlist:\n" + ", ".join(tickers))


@bot.command(name="clearwatchlist")
@commands.guild_only()
async def clear_watchlist_command(ctx):
    """Clear all tickers from the watchlist."""
    save_watchlist([])
    await ctx.send("Watchlist cleared.")


@bot.command(name="resetwatchlist")
@commands.guild_only()
async def reset_watchlist_command(ctx):
    """Reset the watchlist to the default tickers."""
    save_watchlist(DEFAULT_WATCHLIST)
    await ctx.send("Watchlist reset to default.")


@bot.command(name="check")
@commands.guild_only()
async def check_command(ctx, ticker=None):
    """Run a stock check for one ticker."""
    ticker = normalize_ticker(ticker)
    if not ticker:
        await ctx.send("Please provide a ticker, like `!check AAPL`.")
        return

    await ctx.send(f"Checking {ticker}. This may take a moment...")
    analysis = await asyncio.to_thread(analyze_stock, ticker)

    if analysis is None:
        await ctx.send(f"I could not find recent stock data for {ticker}.")
        return

    await ctx.send(format_stock_check(analysis))


@bot.command(name="alerts")
@commands.guild_only()
async def alerts_command(ctx):
    """Show background alert loop status."""
    tickers = load_watchlist()
    status = "running" if stock_alert_loop.is_running() else "stopped"

    await ctx.send(
        "Stock alerts status:\n"
        f"Loop: {status}\n"
        f"Tracked tickers: {len(tickers)}\n"
        f"Check interval: {ALERT_INTERVAL_MINUTES} minutes"
    )


@bot.command(name="eodsummary")
@commands.guild_only()
async def eodsummary_command(ctx):
    """Manually generate and post the EOD summary in the current channel."""
    await ctx.send("Building end-of-day watchlist summary. This may take a moment...")
    summary = await asyncio.to_thread(build_eod_summary)
    await send_long_message(ctx, summary)


@bot.command(name="eodstatus")
@commands.guild_only()
async def eodstatus_command(ctx):
    """Show EOD summary schedule and status."""
    tickers = load_watchlist()
    status = "running" if eod_summary_loop.is_running() else "stopped"
    await ctx.send(
        "EOD summary status:\n"
        f"Loop: {status}\n"
        f"Scheduled time: {EOD_SUMMARY_HOUR:02d}:{EOD_SUMMARY_MINUTE:02d} Pacific\n"
        f"Last summary date: {last_eod_summary_date or 'Never'}\n"
        f"Target channel: #{EOD_SUMMARY_CHANNEL}\n"
        f"Tracked stocks: {len(tickers)}"
    )


@bot.command(name="seteodtime")
@commands.guild_only()
async def seteodtime_command(ctx, hour=None, minute=None):
    """Set the EOD summary time for this bot session."""
    global EOD_SUMMARY_HOUR, EOD_SUMMARY_MINUTE

    try:
        new_hour = int(hour)
        new_minute = int(minute)
    except (TypeError, ValueError):
        await ctx.send("Please provide a valid Pacific time, like `!seteodtime 13 30`.")
        return

    if new_hour < 0 or new_hour > 23:
        await ctx.send("Hour must be between 0 and 23.")
        return

    if new_minute < 0 or new_minute > 59:
        await ctx.send("Minute must be between 0 and 59.")
        return

    EOD_SUMMARY_HOUR = new_hour
    EOD_SUMMARY_MINUTE = new_minute
    await ctx.send(
        f"EOD summary time set to {EOD_SUMMARY_HOUR:02d}:{EOD_SUMMARY_MINUTE:02d} Pacific "
        "for this bot session."
    )


@bot.command(name="scan")
@commands.guild_only()
async def scan_command(ctx, *args):
    """Run an on-demand scanner across scanner_universe.json or the broad universe."""
    broad = False
    scan_type = "balanced"
    custom_filter_text = ""

    if args:
        cleaned_args = [argument.lower().strip() for argument in args]
        if cleaned_args[0] == "all":
            broad = True
            if len(cleaned_args) > 1:
                scan_type = cleaned_args[1]
            if scan_type == "custom":
                custom_filter_text = " ".join(args[2:])
        else:
            scan_type = cleaned_args[0]
            if scan_type == "custom":
                custom_filter_text = " ".join(args[1:])

    if scan_type == "custom":
        if not custom_filter_text.strip():
            await ctx.send(custom_scan_help_message())
            return

        parsed_filters = parse_custom_filters(custom_filter_text)
        if parsed_filters["errors"]:
            await ctx.send(f"{parsed_filters['errors'][0]}\n\n{SCANNER_DISCLAIMER}")
            return

        now = datetime.now().timestamp()

        if broad:
            last_broad_scan_time = broad_scan_times.get(ctx.guild.id, 0)
            if now - last_broad_scan_time < 600:
                await ctx.send(
                    "Broad scans are rate-limited because scanning thousands of tickers can hit "
                    "data-provider limits. Please wait before running another broad scan.\n\n"
                    f"{SCANNER_DISCLAIMER}"
                )
                return

            if not load_us_stock_universe():
                await ctx.send(
                    "The broad US stock universe is empty. Run `!refreshuniverse` first.\n\n"
                    f"{SCANNER_DISCLAIMER}"
                )
                return

            broad_scan_times[ctx.guild.id] = now
            await ctx.send(
                "Starting broad universe scan. This may take a few minutes. "
                "Results will post here when finished.\n\n"
                f"{SCANNER_DISCLAIMER}"
            )
            scan_data = await asyncio.to_thread(build_custom_scanner_results, parsed_filters, True)
            await send_long_message(ctx, format_custom_scanner_results(scan_data, parsed_filters, broad=True))
            return

        last_scan_time = last_scan_times.get(ctx.author.id, 0)
        if now - last_scan_time < 60:
            await ctx.send(f"Please wait before running another scan.\n\n{SCANNER_DISCLAIMER}")
            return

        last_scan_times[ctx.author.id] = now
        await ctx.send(
            "Running custom scanner. This may take a few minutes...\n\n"
            f"{SCANNER_DISCLAIMER}"
        )
        scan_data = await asyncio.to_thread(build_custom_scanner_results, parsed_filters, False)
        await send_long_message(ctx, format_custom_scanner_results(scan_data, parsed_filters, broad=False))
        return

    if scan_type not in VALID_SCAN_TYPES:
        await ctx.send(
            "Unknown scan type. Try: !scan, !scan momentum, !scan breakouts, "
            "!scan oversold, !scan pullbacks, !scan volume, or !scan custom rsi>70.\n\n"
            f"{SCANNER_DISCLAIMER}"
        )
        return

    now = datetime.now().timestamp()

    if broad:
        last_broad_scan_time = broad_scan_times.get(ctx.guild.id, 0)
        if now - last_broad_scan_time < 600:
            await ctx.send(
                "Broad scans are rate-limited because scanning thousands of tickers can hit "
                "data-provider limits. Please wait before running another broad scan.\n\n"
                f"{SCANNER_DISCLAIMER}"
            )
            return

        if not load_us_stock_universe():
            await ctx.send(
                "The broad US stock universe is empty. Run `!refreshuniverse` first.\n\n"
                f"{SCANNER_DISCLAIMER}"
            )
            return

        broad_scan_times[ctx.guild.id] = now
        await ctx.send(
            "Starting broad universe scan. This may take a few minutes. "
            "Results will post here when finished.\n\n"
            f"{SCANNER_DISCLAIMER}"
        )
        results = await asyncio.to_thread(build_scanner_results, scan_type, True)
        await send_long_message(ctx, format_scanner_results(results, scan_type, broad=True))
        return

    last_scan_time = last_scan_times.get(ctx.author.id, 0)
    if now - last_scan_time < 60:
        await ctx.send(f"Please wait before running another scan.\n\n{SCANNER_DISCLAIMER}")
        return

    last_scan_times[ctx.author.id] = now
    await ctx.send(
        f"Running {format_scan_type(scan_type).lower()} scanner. This may take a few minutes...\n\n"
        f"{SCANNER_DISCLAIMER}"
    )

    results = await asyncio.to_thread(build_scanner_results, scan_type)
    await send_long_message(ctx, format_scanner_results(results, scan_type))


@bot.command(name="scannerlist")
@commands.guild_only()
async def scannerlist_command(ctx):
    """Show all tickers in the scanner universe."""
    tickers = load_scanner_universe()
    if not tickers:
        await ctx.send(f"Scanner universe is empty.\n\n{SCANNER_DISCLAIMER}")
        return

    await send_long_message(ctx, "Scanner universe:\n" + ", ".join(tickers) + f"\n\n{SCANNER_DISCLAIMER}")


@bot.command(name="scanhelp")
@commands.guild_only()
async def scanhelp_command(ctx):
    """Show supported custom scanner filters."""
    await ctx.send(
        "Custom scan filters:\n"
        "rsi>number, rsi<number, rsi=low-high\n"
        "price>number, price<number, price=low-high\n"
        "volume>number, volume<number\n"
        "relvol>number, relvol<number\n"
        "change5d>number, change5d<number, change5d=low-high\n"
        "near52whigh<number, near52wlow<number\n"
        "above20ma=true, above50ma=true, above200ma=true\n"
        "below20ma=true, below50ma=true, below200ma=true\n"
        "trend=bullish, trend=bearish, trend=mixed, trend=high-risk\n\n"
        "Examples:\n"
        "!scan custom rsi>70\n"
        "!scan custom rsi<30 price>5 volume>1000000\n"
        "!scan custom rsi=45-65 above50ma=true above200ma=true\n"
        "!scan custom relvol>1.5 change5d>3\n"
        "!scan all custom rsi<35 price>10 volume>500000\n\n"
        f"{SCANNER_DISCLAIMER}"
    )


@bot.command(name="scanadd")
@commands.guild_only()
async def scanadd_command(ctx, ticker=None):
    """Add a ticker to the scanner universe."""
    ticker = normalize_ticker(ticker)
    if not ticker:
        await ctx.send(f"Please provide a ticker, like `!scanadd RKLB`.\n\n{SCANNER_DISCLAIMER}")
        return

    tickers = load_scanner_universe()
    if ticker in tickers:
        await ctx.send(f"{ticker} is already in the scanner universe.\n\n{SCANNER_DISCLAIMER}")
        return

    tickers.append(ticker)
    save_scanner_universe(tickers)
    await ctx.send(f"Added {ticker} to the scanner universe.\n\n{SCANNER_DISCLAIMER}")


@bot.command(name="scanremove")
@commands.guild_only()
async def scanremove_command(ctx, ticker=None):
    """Remove a ticker from the scanner universe."""
    ticker = normalize_ticker(ticker)
    if not ticker:
        await ctx.send(f"Please provide a ticker, like `!scanremove RKLB`.\n\n{SCANNER_DISCLAIMER}")
        return

    tickers = load_scanner_universe()
    if ticker not in tickers:
        await ctx.send(f"{ticker} is not in the scanner universe.\n\n{SCANNER_DISCLAIMER}")
        return

    tickers.remove(ticker)
    save_scanner_universe(tickers)
    await ctx.send(f"Removed {ticker} from the scanner universe.\n\n{SCANNER_DISCLAIMER}")


@bot.command(name="scanreset")
@commands.guild_only()
async def scanreset_command(ctx):
    """Reset the scanner universe to its starter tickers."""
    save_scanner_universe(DEFAULT_SCANNER_UNIVERSE)
    await ctx.send(f"Scanner universe reset to default.\n\n{SCANNER_DISCLAIMER}")


@bot.command(name="refreshuniverse")
@commands.guild_only()
async def refreshuniverse_command(ctx):
    """Download and rebuild the broad US stock universe."""
    await ctx.send(
        "Refreshing the broad US stock universe. This may take a moment...\n\n"
        f"{SCANNER_DISCLAIMER}"
    )

    try:
        tickers = await asyncio.to_thread(download_us_stock_universe)
    except requests.RequestException as error:
        print(f"Could not refresh US stock universe: {error}")
        await ctx.send(
            "Could not refresh the US stock universe. The data source may be unavailable.\n\n"
            f"{SCANNER_DISCLAIMER}"
        )
        return

    await ctx.send(
        f"US stock universe refreshed. Saved {len(tickers):,} tickers. "
        "Use !scan all momentum to scan a filtered batch. Broad scans can take time.\n\n"
        f"{SCANNER_DISCLAIMER}"
    )


@bot.command(name="universecount")
@commands.guild_only()
async def universecount_command(ctx):
    """Show broad US stock universe metadata."""
    data = load_us_stock_universe_data()
    await ctx.send(
        "US stock universe:\n"
        f"Ticker count: {len(data['tickers']):,}\n"
        f"Last updated: {data['last_updated'] or 'Never'}\n"
        f"Source: {data['source']}\n\n"
        f"{SCANNER_DISCLAIMER}"
    )


@bot.command(name="setscanlimit")
@commands.guild_only()
async def setscanlimit_command(ctx, number=None):
    """Set the broad scan ticker limit for this bot session."""
    global current_universe_scan_limit

    if number is None:
        await ctx.send(
            f"Current broad scan limit: {current_universe_scan_limit:,} tickers.\n\n"
            f"{SCANNER_DISCLAIMER}"
        )
        return

    try:
        new_limit = int(number)
    except ValueError:
        await ctx.send(f"Please provide a whole number between 50 and 1000.\n\n{SCANNER_DISCLAIMER}")
        return

    if new_limit < 50 or new_limit > 1000:
        await ctx.send(f"Scan limit must be between 50 and 1000.\n\n{SCANNER_DISCLAIMER}")
        return

    current_universe_scan_limit = new_limit
    await ctx.send(
        f"Broad scan limit set to {current_universe_scan_limit:,} tickers for this bot session.\n\n"
        f"{SCANNER_DISCLAIMER}"
    )


@bot.command(name="scanfilters")
@commands.guild_only()
async def scanfilters_command(ctx):
    """Show current broad scan filters."""
    await ctx.send(
        "Broad scan filters:\n"
        f"Minimum price: {format_money(MIN_PRICE_FILTER)}\n"
        f"Minimum 20-day average volume: {MIN_AVG_VOLUME_FILTER:,}\n"
        f"Max tickers scanned: {current_universe_scan_limit:,}\n"
        "ETFs excluded by default: Yes\n"
        "Warrants/units/preferreds excluded when possible: Yes\n\n"
        f"{SCANNER_DISCLAIMER}"
    )


@bot.command(name="wsbstatus")
@commands.guild_only()
async def wsbstatus_command(ctx):
    """Show WSB tracker status."""
    tracked = load_wsb_tracking().get("tracked", [])
    credentials = "configured" if reddit_credentials_configured() else "missing"
    loop_status = "running" if wsb_tracker_loop.is_running() else "stopped"
    await ctx.send(
        "WSB tracker status:\n"
        f"Reddit credentials: {credentials}\n"
        f"Loop: {loop_status}\n"
        f"Check interval: {WSB_CHECK_INTERVAL_MINUTES} minutes\n"
        f"Subreddit: r/{WSB_SUBREDDIT}\n"
        f"Currently tracked WSB tickers: {len(tracked)}\n\n"
        f"{WSB_DISCLAIMER}"
    )


@bot.command(name="wsbmentions")
@commands.guild_only()
async def wsbmentions_command(ctx):
    """Show current top WSB ticker mentions."""
    mentions = load_wsb_mentions().get("mentions", [])
    await send_long_message(ctx, format_wsb_mentions(mentions))


@bot.command(name="wsbtrack")
@commands.guild_only()
async def wsbtrack_command(ctx):
    """Show WSB-tracked tickers with market measurements."""
    data = load_wsb_tracking()
    await send_long_message(ctx, format_wsb_tracking(data.get("tracked", []), data.get("timestamp")))


@bot.command(name="wsbrefresh")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def wsbrefresh_command(ctx):
    """Manually refresh WSB mentions and tracking."""
    if not reddit_credentials_configured():
        await ctx.send(
            "Reddit credentials are missing. Add REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, "
            "and REDDIT_USER_AGENT to your .env file.\n\n"
            f"{WSB_DISCLAIMER}"
        )
        return

    await ctx.send(f"Refreshing WSB mentions and tracking. This may take a moment...\n\n{WSB_DISCLAIMER}")
    try:
        mentions = await asyncio.to_thread(update_wsb_mentions)
        tracked = await asyncio.to_thread(update_wsb_tracking)
    except Exception as error:
        print(f"Manual WSB refresh failed: {error}")
        await ctx.send(f"WSB refresh failed. Check the terminal for details.\n\n{WSB_DISCLAIMER}")
        return

    await send_long_message(ctx, format_wsb_mentions(mentions))
    await send_long_message(ctx, format_wsb_tracking(tracked))


@wsbrefresh_command.error
async def wsbrefresh_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(
            "Please wait before running another WSB refresh. Reddit API access can be rate-limited.\n\n"
            f"{WSB_DISCLAIMER}"
        )
        return
    raise error


@bot.command(name="wsbsettings")
@commands.guild_only()
async def wsbsettings_command(ctx):
    """Show WSB tracker settings."""
    await ctx.send(
        "WSB tracker settings:\n"
        f"Subreddit: r/{WSB_SUBREDDIT}\n"
        f"Check interval: {WSB_CHECK_INTERVAL_MINUTES} minutes\n"
        f"Post limit: {WSB_POST_LIMIT}\n"
        f"Mention lookback: {WSB_MENTION_LOOKBACK_MINUTES} minutes\n"
        f"Min mentions to track: {WSB_MIN_MENTIONS_TO_TRACK}\n"
        f"Max tracked tickers: {WSB_MAX_TRACKED_TICKERS}\n\n"
        f"{WSB_DISCLAIMER}"
    )


@bot.command(name="wsbclear")
@commands.guild_only()
async def wsbclear_command(ctx):
    """Clear WSB mentions and tracking files."""
    clear_wsb_files()
    await ctx.send(f"WSB mention and tracking files cleared.\n\n{WSB_DISCLAIMER}")


print("Tip: On Windows, if `python` does not work, run this bot with `py main.py`.")

if not DISCORD_BOT_TOKEN:
    print("Missing DISCORD_BOT_TOKEN. Create a .env file in this project folder.")
else:
    bot.run(DISCORD_BOT_TOKEN)
