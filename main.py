import asyncio
import contextlib
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

import discord
import pandas as pd
import requests
import yfinance as yf
from discord.ext import commands, tasks
from dotenv import load_dotenv

try:
    import praw
except ImportError:
    praw = None

from cache import get_cached_value, is_recently_rate_limited, set_cached_value
from paper_trading import (
    PAPER_TRADING_DISCLAIMER,
    add_paper_trade,
    clear_paper_trades,
    close_position,
    load_paper_trades,
    open_positions,
    realized_pnl,
    recent_trades,
)
from risk import SEVERITY_EMOJI, calculate_risk_flags, determine_alert_severity, format_risk_flags, severity_allows
from scoring import calculate_signal_score, format_signal_score
from settings import DEFAULT_BOT_SETTINGS, load_bot_settings, reset_bot_settings, save_bot_settings, set_setting
from storage import data_path, migrate_json_files_to_data, read_json, write_json


load_dotenv()

migrate_json_files_to_data(
    [
        "watchlist.json",
        "scanner_universe.json",
        "us_stock_universe.json",
        "earnings_cache.json",
        "stock_data_cache.json",
        "analyst_cache.json",
        "dividend_cache.json",
        "pauls_tracker_results.json",
        "wsb_mentions.json",
        "wsb_tracking.json",
        "bot_settings.json",
        "paper_trades.json",
        "promising_earnings.json",
    ]
)
BOT_SETTINGS = load_bot_settings()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID")

CHANNEL_CATEGORIES = {
    "Stock Bot — Core": [
        "bot-commands",
        "bot-status",
        "help-and-examples",
    ],
    "Stock Bot — Watchlist": [
        "watchlist",
        "stock-alerts",
        "options-alerts",
        "daily-highs",
        "daily-lows",
        "fifty-two-week-highs",
        "fifty-two-week-lows",
        "rsi-alerts",
        "volume-spikes",
        "news-alerts",
    ],
    "Stock Bot — Scanner": [
        "stock-ideas",
        "scanner-results",
        "broad-scanner",
        "custom-scans",
    ],
    "Stock Bot — Journal": [
        "trade-journal",
        "daily-summaries",
    ],
    "Stock Bot — Quarterly Reports": [
        "watchlist-earnings",
        "promising-earnings",
        "earnings-alerts",
    ],
    "Stock Bot — WallStreetBets": [
        "wsb-mentions",
        "wsb-tracking",
        "wsb-alerts",
    ],
    "Paul's Trackers": [
        "dividend-highs",
        "five-day-runners",
    ],
}
CHANNEL_CATEGORIES["Stock Bot — Briefings"] = [
    "morning-briefing",
    "market-briefing",
]
CHANNEL_CATEGORIES["Stock Bot — Paper Trading"] = [
    "paper-trades",
    "paper-portfolio",
    "paper-pnl",
]
CHANNEL_CATEGORIES["Stock Bot — Guide"] = [
    "how-to-read-alerts",
    "emoji-legend",
    "scanner-guide",
    "risk-and-score-guide",
    "paper-trading-guide",
]
GUIDE_CATEGORY_NAME = "Stock Bot \u2014 Guide"
for category_name in list(CHANNEL_CATEGORIES):
    if "Guide" in category_name and category_name != GUIDE_CATEGORY_NAME:
        CHANNEL_CATEGORIES.pop(category_name)
CHANNEL_CATEGORIES[GUIDE_CATEGORY_NAME] = [
    "how-to-read-alerts",
    "emoji-legend",
    "scanner-guide",
    "risk-and-score-guide",
    "paper-trading-guide",
]
CHANNEL_CATEGORIES["Stock Bot — Best Setups"] = [
    "best-setups",
    "do-not-chase",
    "setup-watch",
]
CHANNEL_CATEGORIES["Stock Bot — Signal Performance"] = [
    "signal-performance",
    "signal-log",
    "signal-review",
]
CHANNEL_CATEGORIES["Stock Bot — Levels"] = [
    "support-resistance",
    "levels-alerts",
]
CHANNEL_CATEGORIES["Stock Bot — Options"] = [
    "options-watch",
    "unusual-options",
    "options-risk",
    "options-checks",
]
CHANNEL_CATEGORIES["Stock Bot — Watchlist Groups"] = [
    "watchlist-groups",
    "group-alerts",
]
for channel_list in CHANNEL_CATEGORIES.values():
    if "earnings-alerts" in channel_list and channel_list != CHANNEL_CATEGORIES.get("Stock Bot â€” Quarterly Reports"):
        channel_list[:] = [channel for channel in channel_list if channel != "earnings-alerts"]
for category_name, channel_list in CHANNEL_CATEGORIES.items():
    if "Quarterly Reports" in category_name and "earnings-alerts" not in channel_list:
        channel_list.append("earnings-alerts")
GUIDE_CHANNELS = {
    "how-to-read-alerts": "How to Read Stock Bot Alerts",
    "emoji-legend": "Emoji Legend",
    "scanner-guide": "How to Read Scanner Results",
    "risk-and-score-guide": "Signal Score and Risk Flags Guide",
    "paper-trading-guide": "Paper Trading Guide",
}
GUIDE_MESSAGE_MARKER = "[STOCK_BOT_GUIDE_MESSAGE]"
WATCHLIST_FILE = data_path("watchlist.json")
SCANNER_UNIVERSE_FILE = data_path("scanner_universe.json")
US_STOCK_UNIVERSE_FILE = data_path("us_stock_universe.json")
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
SCAN_RESULT_LIMIT = int(BOT_SETTINGS.get("scanner_result_limit", 15))
MIN_PRICE_FILTER = 5.00
MIN_AVG_VOLUME_FILTER = 500000
MAX_UNIVERSE_SCAN_TICKERS = int(BOT_SETTINGS.get("broad_scan_limit", 500))
FULL_SCAN_RESULT_LIMIT = 15
CUSTOM_SCAN_RESULT_LIMIT = 15
MARKET_TIMEZONE = "America/Los_Angeles"
EOD_SUMMARY_HOUR, EOD_SUMMARY_MINUTE = [
    int(part) for part in BOT_SETTINGS.get("eod_summary_time", "13:30").split(":")
]
EOD_SUMMARY_CHANNEL = "trade-journal"
MORNING_BRIEFING_CHANNEL = "morning-briefing"
MARKET_BRIEFING_CHANNEL = "market-briefing"
MORNING_BRIEFING_HOUR, MORNING_BRIEFING_MINUTE = [
    int(part) for part in BOT_SETTINGS.get("morning_briefing_time", "06:30").split(":")
]
WSB_SUBREDDIT = "wallstreetbets"
WSB_CHECK_INTERVAL_MINUTES = 10
WSB_POST_LIMIT = 50
WSB_MENTION_LOOKBACK_MINUTES = 60
WSB_MIN_MENTIONS_TO_TRACK = 2
WSB_MAX_TRACKED_TICKERS = 25
WSB_MENTIONS_FILE = data_path("wsb_mentions.json")
WSB_TRACKING_FILE = data_path("wsb_tracking.json")
EARNINGS_LOOKAHEAD_DAYS = int(BOT_SETTINGS.get("earnings_lookahead_days", 14))
EARNINGS_WEEKLY_SUMMARY_DAY = 0
EARNINGS_WEEKLY_SUMMARY_HOUR = 8
EARNINGS_WEEKLY_SUMMARY_MINUTE = 0
EARNINGS_TIMEZONE = "America/Los_Angeles"
WATCHLIST_EARNINGS_CHANNEL = "watchlist-earnings"
PROMISING_EARNINGS_CHANNEL = "promising-earnings"
EARNINGS_ALERTS_CHANNEL = "earnings-alerts"
EARNINGS_CACHE_FILE = data_path("earnings_cache.json")
PROMISING_EARNINGS_FILE = data_path("promising_earnings.json")
PROMISING_MIN_TARGET_UPSIDE_PERCENT = 10
PROMISING_MAX_RECOMMENDATION_MEAN = 2.5
PROMISING_MIN_ANALYST_COUNT = 5
PROMISING_MAX_RSI = 75
PROMISING_MIN_PRICE = 5
MAX_PROMISING_EARNINGS_SCAN_TICKERS = int(BOT_SETTINGS.get("earnings_scan_limit", 50))
EARNINGS_CACHE_MAX_AGE_HOURS = 24
EARNINGS_RATE_LIMIT_RETRY_HOURS = 6
PAULS_TRACKERS_CATEGORY = "Paul's Trackers"
DIVIDEND_HIGHS_CHANNEL = "dividend-highs"
FIVE_DAY_RUNNERS_CHANNEL = "five-day-runners"
PAULS_TRACKER_SCAN_INTERVAL_MINUTES = 60
PAULS_TRACKER_RESULT_LIMIT = 20
DIVIDEND_YIELD_MIN_PERCENT = float(BOT_SETTINGS.get("pauls_dividend_min_percent", 5.0))
NEAR_ALL_TIME_HIGH_PERCENT = float(BOT_SETTINGS.get("pauls_ath_threshold_percent", 5.0))
FIVE_DAY_GAIN_MIN_PERCENT = float(BOT_SETTINGS.get("pauls_5day_gain_percent", 10.0))
PAULS_TRACKERS_AUTO_ENABLED = bool(BOT_SETTINGS.get("pauls_trackers_auto_enabled", False))
PAULS_TRACKER_SCAN_LIMIT = int(BOT_SETTINGS.get("pauls_tracker_scan_limit", 50))
PAULS_TRACKER_FILE = data_path("pauls_tracker_results.json")
SIGNAL_LOG_FILE = data_path("signal_log.json")
WATCHLIST_GROUPS_FILE = data_path("watchlist_groups.json")
BEST_SETUPS_HOUR, BEST_SETUPS_MINUTE = [
    int(part) for part in BOT_SETTINGS.get("best_setups_time", "07:00").split(":")
]
BEST_SETUPS_RESULT_LIMIT = int(BOT_SETTINGS.get("best_setups_result_limit", 10))
BEST_SETUPS_MIN_SCORE = float(BOT_SETTINGS.get("best_setups_min_score", 7.0))
DO_NOT_CHASE_RSI = float(BOT_SETTINGS.get("do_not_chase_rsi", 75))
DO_NOT_CHASE_5D_GAIN = float(BOT_SETTINGS.get("do_not_chase_5d_gain", 15.0))
DO_NOT_CHASE_DISTANCE_ABOVE_20MA = float(BOT_SETTINGS.get("do_not_chase_distance_above_20ma", 7.0))
LEVELS_ALERT_DISTANCE_PERCENT = float(BOT_SETTINGS.get("levels_alert_distance_percent", 1.0))
OPTIONS_MIN_VOLUME = int(BOT_SETTINGS.get("options_min_volume", 10))
OPTIONS_MIN_OPEN_INTEREST = int(BOT_SETTINGS.get("options_min_open_interest", 100))
OPTIONS_MAX_SPREAD_PERCENT = float(BOT_SETTINGS.get("options_max_spread_percent", 20))
OPTIONS_MIN_DTE = int(BOT_SETTINGS.get("options_min_dte", 14))
OPTIONS_MAX_DTE = int(BOT_SETTINGS.get("options_max_dte", 60))
ENABLE_DAILY_SCANNER = False
ALERT_INTERVAL_MINUTES = int(BOT_SETTINGS.get("alert_frequency_minutes", 5))
QUIET_MODE = bool(BOT_SETTINGS.get("quiet_mode", False))
MARKET_HOURS_ONLY = bool(BOT_SETTINGS.get("market_hours_only", False))
MIN_ALERT_SEVERITY = BOT_SETTINGS.get("min_alert_severity", "low")
NEAR_DAILY_HIGH_THRESHOLD = 0.995
NEAR_DAILY_LOW_THRESHOLD = 1.005
NEAR_52W_HIGH_THRESHOLD = 0.99
NEAR_52W_LOW_THRESHOLD = 1.01
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
VOLUME_SPIKE_MULTIPLE = 1.8
CHANNEL_NAMES = [
    channel_name
    for channel_names in CHANNEL_CATEGORIES.values()
    for channel_name in channel_names
]

sent_alerts = set()
last_scan_times = {}
broad_scan_times = {}
current_universe_scan_limit = MAX_UNIVERSE_SCAN_TICKERS
last_eod_summary_date = None
last_morning_briefing_date = None
last_best_setups_date = None
last_do_not_chase_date = None
last_signal_performance_summary_date = None
last_earnings_weekly_summary_date = None
last_earnings_alert_dates = {}
earnings_calendar_failures = {}
pauls_tracker_scan_times = {}
guide_pin_permission_warning_printed = False
logged_no_fundamentals_tickers = set()
KNOWN_ETF_OR_FUND_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "XLK", "XLF", "XLE", "XLY",
    "XLP", "XLU", "XLV", "XLI", "XLB", "SMH", "SOXX", "ARKK", "TQQQ", "SQQQ",
}
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
EARNINGS_DISCLAIMER = "Earnings and analyst data are for research only and are not financial advice."
EMOJI_UP = "🟢"
EMOJI_DOWN = "🔴"
EMOJI_FLAT = "⚪"
EMOJI_ARROW_UP = "📈"
EMOJI_ARROW_DOWN = "📉"
EMOJI_NEUTRAL = "➖"
EMOJI_VOLUME = "🔥"
EMOJI_WARNING = "⚠️"
EMOJI_OVERSOLD = "🧊"
EMOJI_BREAKOUT = "🚀"
EMOJI_EARNINGS = "📅"
EMOJI_RESEARCH = "🔎"
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


def is_probable_etf_or_fund(ticker: str) -> bool:
    """Return True for common ETFs/funds where stock fundamentals are often unavailable."""
    ticker = normalize_ticker(ticker)
    if ticker in KNOWN_ETF_OR_FUND_TICKERS:
        return True

    return False


def safe_get_ticker_info(ticker: str) -> dict:
    """Quietly fetch yfinance fundamentals, caching no-fundamentals failures."""
    ticker = normalize_ticker(ticker)
    if not ticker:
        return {}

    cached = get_cached_value("analyst", f"{ticker}:info", max_age_minutes=1440)
    if cached is not None:
        return cached if isinstance(cached, dict) else {}

    if is_recently_rate_limited("analyst", f"{ticker}:info", retry_after_minutes=1440):
        return {}

    if is_probable_etf_or_fund(ticker):
        set_cached_value("analyst", f"{ticker}:info", {}, status="no_fundamentals")
        return {}

    try:
        # yfinance sometimes writes raw HTTP errors to stdout/stderr before raising.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            info = yf.Ticker(ticker).info or {}
    except Exception as error:
        error_text = str(error)
        if "No fundamentals data found" in error_text or "404" in error_text:
            if ticker not in logged_no_fundamentals_tickers:
                print(f"No fundamentals data available for {ticker}; skipping fundamentals.")
                logged_no_fundamentals_tickers.add(ticker)
            set_cached_value("analyst", f"{ticker}:info", {}, status="no_fundamentals")
            return {}

        status = "rate_limited" if "Too Many Requests" in error_text or "rate limit" in error_text.lower() else "error"
        if ticker not in logged_no_fundamentals_tickers:
            print(f"Could not read fundamentals for {ticker}: {status}.")
            logged_no_fundamentals_tickers.add(ticker)
        set_cached_value("analyst", f"{ticker}:info", {}, status=status)
        return {}

    if not isinstance(info, dict):
        info = {}

    if str(info.get("quoteType", "")).upper() in {"ETF", "MUTUALFUND", "FUND"}:
        set_cached_value("analyst", f"{ticker}:info", {}, status="no_fundamentals")
        return {}

    set_cached_value("analyst", f"{ticker}:info", info)
    return info


def save_watchlist(tickers):
    """Save uppercase, sorted, unique tickers to watchlist.json."""
    clean_tickers = sorted(
        {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    )

    write_json(WATCHLIST_FILE, {"tickers": clean_tickers})

    return clean_tickers


def load_watchlist():
    """Load watchlist.json, creating or repairing it when needed."""
    if not os.path.exists(WATCHLIST_FILE):
        print("Recreated watchlist.json with default watchlist.")
        return save_watchlist(DEFAULT_WATCHLIST)

    try:
        data = read_json(WATCHLIST_FILE, {"tickers": DEFAULT_WATCHLIST}, recreate_on_error=True)
    except Exception as error:
        print(f"Could not read watchlist.json: {error}")
        print("Recreated watchlist.json with default watchlist.")
        return save_watchlist(DEFAULT_WATCHLIST)

    tickers = data.get("tickers", [])
    if not isinstance(tickers, list):
        print("Recreated watchlist.json with default watchlist.")
        return save_watchlist(DEFAULT_WATCHLIST)

    clean_tickers = sorted({normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)})
    if not clean_tickers:
        print("Recreated watchlist.json with default watchlist.")
        return save_watchlist(DEFAULT_WATCHLIST)
    return clean_tickers


def save_scanner_universe(tickers):
    """Save uppercase, sorted, unique tickers to scanner_universe.json."""
    clean_tickers = sorted(
        {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
    )

    write_json(SCANNER_UNIVERSE_FILE, {"tickers": clean_tickers})

    return clean_tickers


def load_scanner_universe():
    """Load scanner_universe.json, creating or repairing it when needed."""
    if not os.path.exists(SCANNER_UNIVERSE_FILE):
        print("scanner_universe.json not found. Creating it with the default universe.")
        return save_scanner_universe(DEFAULT_SCANNER_UNIVERSE)

    try:
        data = read_json(SCANNER_UNIVERSE_FILE, {"tickers": DEFAULT_SCANNER_UNIVERSE}, recreate_on_error=True)
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

    write_json(US_STOCK_UNIVERSE_FILE, data)

    return clean_tickers


def load_us_stock_universe_data():
    """Load broad US universe metadata from disk."""
    if not os.path.exists(US_STOCK_UNIVERSE_FILE):
        print("us_stock_universe.json is missing. Run !refreshuniverse first.")
        return {"tickers": [], "last_updated": None, "source": "nasdaqtrader"}

    try:
        data = read_json(US_STOCK_UNIVERSE_FILE, {"tickers": [], "last_updated": None, "source": "nasdaqtrader"}, recreate_on_error=True)
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


def analyze_stock_price_only(ticker):
    """Fetch price/history data only. This function never calls fundamentals endpoints."""
    ticker = normalize_ticker(ticker)
    if not ticker:
        return None

    cached = get_cached_value("stock_data", ticker, max_age_minutes=5)
    if cached:
        return cached

    if is_recently_rate_limited("stock_data", ticker, retry_after_minutes=360):
        print(f"Skipping {ticker}; yfinance recently rate-limited this ticker.")
        return None
    if is_recently_rate_limited("stock_data", f"{ticker}:no_data", retry_after_minutes=1440):
        return None

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            intraday_download = yf.download(
                ticker,
                period="5d",
                interval="5m",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            daily_download = yf.download(
                ticker,
                period="1y",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        intraday = get_download_frame(intraday_download, ticker)
        daily = get_download_frame(daily_download, ticker)
    except Exception as error:
        print(f"Could not fetch data for {ticker}: {error}")
        status = "rate_limited" if "Too Many Requests" in str(error) or "rate limit" in str(error).lower() else "error"
        set_cached_value("stock_data", ticker, None, status=status)
        return None

    if intraday.empty or daily.empty:
        set_cached_value("stock_data", f"{ticker}:no_data", True, status="rate_limited")
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

    previous_close = None
    day_change = None
    day_change_percent = None
    if len(close_prices) >= 2:
        previous_close = _to_float(close_prices.iloc[-2])
        if previous_close is not None and previous_close > 0:
            day_change = latest_price - previous_close
            day_change_percent = (day_change / previous_close) * 100

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

    result = {
        "ticker": ticker,
        "price": latest_price,
        "latest_price": latest_price,
        "previous_close": previous_close,
        "day_change": day_change,
        "day_change_percent": day_change_percent,
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
    set_cached_value("stock_data", ticker, result)
    return result


def analyze_stock(ticker, include_fundamentals=False, include_earnings=False):
    """Return stock analysis. Fundamentals/earnings are opt-in and disabled by default."""
    data = analyze_stock_price_only(ticker)
    if data is None:
        return None

    if include_fundamentals:
        data["analyst_snapshot"] = get_analyst_snapshot(ticker)

    if include_earnings:
        data["earnings_date"] = get_earnings_date(ticker)

    return data


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


def get_price_direction_emoji(change_value):
    if change_value is None or change_value == 0:
        return f"{EMOJI_FLAT} {EMOJI_NEUTRAL}"
    if change_value > 0:
        return f"{EMOJI_UP} {EMOJI_ARROW_UP}"
    return f"{EMOJI_DOWN} {EMOJI_ARROW_DOWN}"


def format_price_change(stock_data):
    change_value = stock_data.get("day_change") if stock_data else None
    change_percent = stock_data.get("day_change_percent") if stock_data else None
    emoji = get_price_direction_emoji(change_value)

    if change_value is None or change_percent is None:
        return f"{emoji} $0.00 (0.00%)"

    if change_value > 0:
        money_change = f"+${change_value:,.2f}"
        percent_change = f"+{change_percent:,.2f}%"
    elif change_value < 0:
        money_change = f"-${abs(change_value):,.2f}"
        percent_change = f"{change_percent:,.2f}%"
    else:
        money_change = "$0.00"
        percent_change = "0.00%"
    return f"{emoji} {money_change} ({percent_change})"


def format_change_percent_only(stock_data):
    change_percent = stock_data.get("day_change_percent") if stock_data else None
    if change_percent is None:
        return "0.00%"
    sign = "+" if change_percent > 0 else ""
    return f"{sign}{change_percent:,.2f}%"


def get_rsi_emoji(rsi):
    if rsi is None:
        return EMOJI_NEUTRAL
    if rsi >= 70:
        return EMOJI_WARNING
    if rsi <= 30:
        return EMOJI_OVERSOLD
    return EMOJI_NEUTRAL


def get_trend_emoji(trend):
    if trend == "Bullish":
        return EMOJI_UP
    if trend == "Bearish":
        return EMOJI_DOWN
    if trend == "Mixed":
        return EMOJI_FLAT
    if trend == "High-risk":
        return EMOJI_WARNING
    return EMOJI_NEUTRAL


def get_signal_emoji(signal_type):
    signal_map = {
        "Near Daily High": f"{EMOJI_UP} {EMOJI_ARROW_UP}",
        "Near Daily Low": f"{EMOJI_DOWN} {EMOJI_ARROW_DOWN}",
        "Near 52-Week High": EMOJI_BREAKOUT,
        "Near 52-Week Low": EMOJI_WARNING,
        "RSI Overbought": EMOJI_WARNING,
        "RSI Oversold": EMOJI_OVERSOLD,
        "Volume Spike": EMOJI_VOLUME,
        "Earnings": EMOJI_EARNINGS,
    }
    return signal_map.get(signal_type, EMOJI_RESEARCH)


def format_relative_volume(value):
    prefix = f"{EMOJI_VOLUME} " if value is not None and value >= 1.5 else ""
    return f"{prefix}{format_multiple(value)}"


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
    direction = get_price_direction_emoji(analysis.get("day_change"))
    risk_flags = calculate_risk_flags(analysis)
    signal_score = calculate_signal_score(analysis)
    levels = calculate_support_resistance(ticker, analysis)
    return (
        f"{direction} {ticker} Stock Check\n"
        f"Price: {format_money(analysis['price'])}\n"
        f"Change: {format_price_change(analysis)}\n"
        f"Day High: {format_money(analysis['day_high'])}\n"
        f"Day Low: {format_money(analysis['day_low'])}\n"
        f"52W High: {format_money(analysis['fifty_two_week_high'])}\n"
        f"52W Low: {format_money(analysis['fifty_two_week_low'])}\n"
        f"RSI: {get_rsi_emoji(analysis['rsi'])} {format_number(analysis['rsi'])}\n"
        f"20MA: {format_money(analysis['moving_average_20'])}\n"
        f"50MA: {format_money(analysis['moving_average_50'])}\n"
        f"200MA: {format_money(analysis['moving_average_200'])}\n"
        f"Trend: {get_trend_emoji(analysis['trend'])} {analysis['trend']}\n"
        f"Nearest Support: {format_money(levels.get('nearest_support'))}\n"
        f"Nearest Resistance: {format_money(levels.get('nearest_resistance'))}\n"
        f"{format_signal_score(signal_score)}\n"
        f"{format_risk_flags(risk_flags)}\n\n"
        "Stock checks are for research only and are not financial advice."
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
            stock_data = analyze_stock_price_only(ticker)
            if stock_data is None:
                continue

            score = calculate_score(stock_data, scan_type)
            if score <= 0:
                continue

            stock_data["scanner_score"] = score
            stock_data["scanner_signal"] = scanner_signal(stock_data, scan_type)
            stock_data["signal_score"] = calculate_signal_score(stock_data)
            stock_data["risk_flags"] = calculate_risk_flags(stock_data)
            log_signal(ticker, scan_type, "scanner", stock_data, stock_data["signal_score"], "low", stock_data["risk_flags"], [stock_data["scanner_signal"]])
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
                f"{index}. {get_price_direction_emoji(result.get('day_change'))} {result['ticker']}",
                f"   Price: {format_money(result['price'])}",
                f"   Change: {format_change_percent_only(result)}",
                f"   RSI: {get_rsi_emoji(result['rsi'])} {format_number(result['rsi'])}",
                f"   Trend: {get_trend_emoji(result['trend'])} {result['trend']}",
                f"   Rel Volume: {format_relative_volume(result['relative_volume'])}",
                f"   5D Change: {format_percent(result['five_day_change'])}",
                f"   Signal: {result['scanner_signal']}",
                f"   Score: {result['scanner_score']}",
                f"   {format_signal_score(result.get('signal_score'))}",
                f"   {format_risk_flags(result.get('risk_flags'))}",
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
            stock_data = analyze_stock_price_only(ticker)
            if stock_data is None:
                continue

            matches, reasons = stock_matches_custom_filters(stock_data, filters)
            if not matches:
                continue

            stock_data["custom_score"] = score_custom_match(stock_data, filters)
            stock_data["match_reasons"] = reasons
            stock_data["signal_score"] = calculate_signal_score(stock_data)
            stock_data["risk_flags"] = calculate_risk_flags(stock_data)
            log_signal(ticker, "custom", "scanner", stock_data, stock_data["signal_score"], "low", stock_data["risk_flags"], reasons)
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
                f"{index}. {get_price_direction_emoji(result.get('day_change'))} {result['ticker']}",
                f"   Price: {format_money(result['latest_price'])}",
                f"   Change: {format_change_percent_only(result)}",
                f"   RSI: {get_rsi_emoji(result['rsi'])} {format_number(result['rsi'])}",
                f"   Trend: {get_trend_emoji(result['trend'])} {result['trend']}",
                f"   Rel Volume: {format_relative_volume(result['relative_volume'])}",
                f"   Volume: {format_integer(result['current_volume'])}",
                f"   5D Change: {format_percent(result['change_5d_percent'])}",
                f"   Match Reasons: {'; '.join(result['match_reasons'])}",
                f"   Score: {result['custom_score']}",
                f"   {format_signal_score(result.get('signal_score'))}",
                f"   {format_risk_flags(result.get('risk_flags'))}",
            ]
        )

    lines.extend(["", SCANNER_DISCLAIMER])
    return "\n".join(lines)


PAULS_TRACKER_DISCLAIMER = "Paul's Tracker results are for research only and are not financial advice."


def get_dividend_yield_percent(ticker):
    """Read dividend yield from yfinance and normalize to percent."""
    ticker = normalize_ticker(ticker)
    if is_probable_etf_or_fund(ticker):
        set_cached_value("dividend", f"{ticker}:yield", None, status="etf")
        return None

    cached = get_cached_value("dividend", f"{ticker}:yield", max_age_minutes=1440)
    if cached is not None:
        return cached
    if is_recently_rate_limited("dividend", f"{ticker}:yield", retry_after_minutes=360):
        print(f"Skipping dividend yield for {ticker}; recently rate limited.")
        return None

    info = safe_get_ticker_info(ticker)
    if not info:
        set_cached_value("dividend", f"{ticker}:yield", None, status="missing")
        return None

    if str(info.get("quoteType", "")).upper() in {"ETF", "MUTUALFUND", "FUND"}:
        set_cached_value("dividend", f"{ticker}:yield", None, status="etf")
        return None

    for key in ["dividendYield", "trailingAnnualDividendYield", "fiveYearAvgDividendYield"]:
        value = _to_float(info.get(key))
        if value is None:
            continue
        if value <= 1:
            value = value * 100
        set_cached_value("dividend", f"{ticker}:yield", value)
        return value

    set_cached_value("dividend", f"{ticker}:yield", None, status="missing")
    return None


def get_all_time_high(ticker):
    """Return all-time high from max-period yfinance history."""
    ticker = normalize_ticker(ticker)
    cached = get_cached_value("dividend", f"{ticker}:ath", max_age_minutes=1440)
    if cached is not None:
        return cached
    if is_recently_rate_limited("dividend", f"{ticker}:ath", retry_after_minutes=360):
        print(f"Skipping all-time high for {ticker}; recently rate limited.")
        return None

    try:
        history = yf.Ticker(ticker).history(period="max", interval="1d", auto_adjust=False)
    except Exception as error:
        print(f"Could not read all-time high for {ticker}: {error}")
        status = "rate_limited" if "Too Many Requests" in str(error) or "rate limit" in str(error).lower() else "error"
        set_cached_value("dividend", f"{ticker}:ath", None, status=status)
        return None

    if history.empty or "High" not in history:
        set_cached_value("dividend", f"{ticker}:ath", None, status="missing")
        return None

    high = _to_float(history["High"].max())
    set_cached_value("dividend", f"{ticker}:ath", high)
    return high


def is_near_all_time_high(latest_price, all_time_high, percent_threshold):
    if latest_price is None or all_time_high is None or all_time_high <= 0:
        return False
    percent_below_high = ((all_time_high - latest_price) / all_time_high) * 100
    return percent_below_high <= percent_threshold


def get_pauls_tracker_universe():
    source = "none"
    tickers = load_us_stock_universe()
    if tickers:
        source = "us_stock_universe.json"
    else:
        tickers = load_scanner_universe()
        if tickers:
            source = "scanner_universe.json"
        else:
            tickers = load_watchlist()
            if tickers:
                source = "watchlist.json"

    if not tickers:
        print("Paul's Trackers could not find a ticker universe.")
        return [], source

    common_one_letter_tickers = {"A", "F", "T", "V"}
    clean_tickers = []
    for ticker in tickers:
        ticker = normalize_ticker(ticker)
        if not ticker:
            continue
        if len(ticker) == 1 and ticker not in common_one_letter_tickers:
            continue
        if any(character in ticker for character in ["^", "/", " "]):
            continue
        clean_tickers.append(ticker)

    return clean_tickers[:PAULS_TRACKER_SCAN_LIMIT], source


def scan_dividend_highs(tickers):
    results = []
    for index, ticker in enumerate(tickers, start=1):
        print(f"Paul's dividend tracker {index}/{len(tickers)}: {ticker}")
        time.sleep(0.35)
        try:
            if is_probable_etf_or_fund(ticker):
                continue

            analysis = analyze_stock_price_only(ticker)
            if analysis is None:
                continue

            avg_volume = analysis.get("avg_volume_20")
            if avg_volume is not None and avg_volume < MIN_AVG_VOLUME_FILTER:
                continue

            dividend_yield = get_dividend_yield_percent(ticker)
            if dividend_yield is None or dividend_yield < DIVIDEND_YIELD_MIN_PERCENT:
                continue

            all_time_high = get_all_time_high(ticker)
            latest_price = analysis.get("latest_price")
            if not is_near_all_time_high(latest_price, all_time_high, NEAR_ALL_TIME_HIGH_PERCENT):
                continue

            percent_below_ath = ((all_time_high - latest_price) / all_time_high) * 100
            results.append(
                {
                    "ticker": ticker,
                    "latest_price": latest_price,
                    "all_time_high": all_time_high,
                    "percent_below_ath": percent_below_ath,
                    "dividend_yield_percent": dividend_yield,
                    "rsi": analysis.get("rsi"),
                    "trend": analysis.get("trend"),
                    "relative_volume": analysis.get("relative_volume"),
                    "change_5d_percent": analysis.get("change_5d_percent"),
                    "day_change": analysis.get("day_change"),
                    "day_change_percent": analysis.get("day_change_percent"),
                    "signal_score": calculate_signal_score(analysis),
                    "risk_flags": calculate_risk_flags(analysis),
                }
            )
            log_signal(ticker, "dividend_high", "pauls_tracker", analysis, calculate_signal_score(analysis), "medium", calculate_risk_flags(analysis), ["Dividend yield near all-time high criteria"])
        except Exception as error:
            print(f"Paul's dividend tracker skipped {ticker}: {error}")
            continue

    results.sort(key=lambda item: (-item["dividend_yield_percent"], item["percent_below_ath"]))
    return results[:PAULS_TRACKER_RESULT_LIMIT]


def scan_five_day_runners(tickers):
    results = []
    for index, ticker in enumerate(tickers, start=1):
        print(f"Paul's 5-day tracker {index}/{len(tickers)}: {ticker}")
        time.sleep(0.25)
        try:
            analysis = analyze_stock_price_only(ticker)
            if analysis is None:
                continue

            change_5d = analysis.get("change_5d_percent")
            if change_5d is None or change_5d < FIVE_DAY_GAIN_MIN_PERCENT:
                continue

            results.append(
                {
                    "ticker": ticker,
                    "latest_price": analysis.get("latest_price"),
                    "change_5d_percent": change_5d,
                    "rsi": analysis.get("rsi"),
                    "trend": analysis.get("trend"),
                    "relative_volume": analysis.get("relative_volume"),
                    "current_volume": analysis.get("current_volume"),
                    "year_high": analysis.get("year_high"),
                    "percent_below_52w_high": analysis.get("percent_below_52w_high"),
                    "day_change": analysis.get("day_change"),
                    "day_change_percent": analysis.get("day_change_percent"),
                    "signal_score": calculate_signal_score(analysis),
                    "risk_flags": calculate_risk_flags(analysis),
                }
            )
            log_signal(ticker, "five_day_runner", "pauls_tracker", analysis, calculate_signal_score(analysis), "medium", calculate_risk_flags(analysis), ["5-day runner criteria"])
        except Exception as error:
            print(f"Paul's 5-day tracker skipped {ticker}: {error}")
            continue

    results.sort(key=lambda item: item["change_5d_percent"], reverse=True)
    return results[:PAULS_TRACKER_RESULT_LIMIT]


def build_dividend_highs_message(results):
    lines = [
        "Paul's Tracker — Dividend Highs",
        "",
        "Criteria:",
        f"- Dividend Yield: >= {DIVIDEND_YIELD_MIN_PERCENT}% per year",
        f"- Price: within {NEAR_ALL_TIME_HIGH_PERCENT}% of all-time high",
    ]

    if not results:
        lines.extend(["", "No tracker results matched the current dividend highs criteria."])
    else:
        for index, item in enumerate(results, start=1):
            lines.extend(
                [
                    "",
                    f"{index}. {get_price_direction_emoji(item.get('day_change'))} {item['ticker']}",
                    f"   Price: {format_money(item['latest_price'])}",
                    f"   All-Time High: {format_money(item['all_time_high'])}",
                    f"   Below ATH: {format_percent(item['percent_below_ath'])}",
                    f"   Dividend Yield: {format_percent(item['dividend_yield_percent'])}",
                    f"   RSI: {get_rsi_emoji(item['rsi'])} {format_number(item['rsi'])}",
                    f"   Trend: {get_trend_emoji(item['trend'])} {item['trend']}",
                    f"   Rel Volume: {format_relative_volume(item['relative_volume'])}",
                    f"   5D Change: {format_percent(item['change_5d_percent'])}",
                    f"   {format_signal_score(item.get('signal_score'))}",
                    f"   {format_risk_flags(item.get('risk_flags'))}",
                ]
            )

    lines.extend(["", PAULS_TRACKER_DISCLAIMER])
    return "\n".join(lines)


def build_five_day_runners_message(results):
    lines = [
        "Paul's Tracker — 5-Day Runners",
        "",
        "Criteria:",
        f"- Stock is up at least {FIVE_DAY_GAIN_MIN_PERCENT}% over the past 5 trading days",
    ]

    if not results:
        lines.extend(["", "No tracker results matched the current 5-day runner criteria."])
    else:
        for index, item in enumerate(results, start=1):
            lines.extend(
                [
                    "",
                    f"{index}. {get_price_direction_emoji(item.get('day_change'))} {item['ticker']}",
                    f"   Price: {format_money(item['latest_price'])}",
                    f"   5D Change: {format_percent(item['change_5d_percent'])}",
                    f"   RSI: {get_rsi_emoji(item['rsi'])} {format_number(item['rsi'])}",
                    f"   Trend: {get_trend_emoji(item['trend'])} {item['trend']}",
                    f"   Rel Volume: {format_relative_volume(item['relative_volume'])}",
                    f"   Volume: {format_integer(item['current_volume'])}",
                    f"   52W High: {format_money(item['year_high'])}",
                    f"   Below 52W High: {format_percent(item['percent_below_52w_high'])}",
                    f"   {format_signal_score(item.get('signal_score'))}",
                    f"   {format_risk_flags(item.get('risk_flags'))}",
                ]
            )

    lines.extend(["", PAULS_TRACKER_DISCLAIMER])
    return "\n".join(lines)


def run_pauls_tracker_scans():
    tickers, source = get_pauls_tracker_universe()
    dividend_results = scan_dividend_highs(tickers)
    runner_results = scan_five_day_runners(tickers)
    data = {
        "timestamp": utc_timestamp(),
        "source": source,
        "dividend_highs": dividend_results,
        "five_day_runners": runner_results,
    }
    write_json_file(PAULS_TRACKER_FILE, data)
    return data


def market_now():
    return datetime.now(ZoneInfo(MARKET_TIMEZONE))


def is_weekday_market_day_now():
    """Return True Monday through Friday in Pacific time."""
    return market_now().weekday() < 5


def is_regular_market_hours_now():
    """Return True during regular US market hours in Pacific time."""
    if not is_weekday_market_day_now():
        return False
    now = market_now().time()
    return datetime_time(6, 30) <= now <= datetime_time(13, 0)


def should_run_eod_summary():
    """Return True when the EOD summary should run for the day."""
    if not is_weekday_market_day_now():
        return False

    now = market_now()
    scheduled_time = datetime_time(EOD_SUMMARY_HOUR, EOD_SUMMARY_MINUTE)
    if now.time() < scheduled_time:
        return False

    return last_eod_summary_date != now.date().isoformat()


def should_run_morning_briefing():
    """Return True when the weekday morning briefing should run."""
    if not is_weekday_market_day_now():
        return False

    now = market_now()
    scheduled_time = datetime_time(MORNING_BRIEFING_HOUR, MORNING_BRIEFING_MINUTE)
    if now.time() < scheduled_time:
        return False

    return last_morning_briefing_date != now.date().isoformat()


def should_run_best_setups():
    if QUIET_MODE or not is_weekday_market_day_now():
        return False
    now = market_now()
    if now.time() < datetime_time(BEST_SETUPS_HOUR, BEST_SETUPS_MINUTE):
        return False
    return last_best_setups_date != now.date().isoformat()


def should_run_do_not_chase():
    if QUIET_MODE or not is_weekday_market_day_now():
        return False
    now = market_now()
    if now.time() < datetime_time(7, 5):
        return False
    return last_do_not_chase_date != now.date().isoformat()


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
    """Build the post-market watchlist recap text."""
    tickers = set(load_watchlist())
    groups = load_watchlist_groups()
    for group_name, enabled in groups.get("alerts_enabled", {}).items():
        if enabled:
            tickers.update(groups.get("groups", {}).get(group_name, []))
    tickers = sorted({normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)})
    today = market_now().date().isoformat()
    analyses = []

    for ticker in tickers:
        analysis = analyze_stock_price_only(ticker)
        if analysis is None:
            analyses.append({"ticker": ticker, "error": True})
            continue
        analyses.append(analysis)

    valid = [item for item in analyses if not item.get("error")]
    green_count = sum(1 for item in valid if (item.get("day_change") or 0) > 0)
    red_count = sum(1 for item in valid if (item.get("day_change") or 0) < 0)
    flat_count = sum(1 for item in valid if item.get("day_change") in (None, 0))
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
        f"Post-Market Watchlist Recap \u2014 {today}",
        "",
        "Overview:",
        f"- Watchlist count: {len(tickers)}",
        f"- Green stocks: {green_count}",
        f"- Red stocks: {red_count}",
        f"- Flat stocks: {flat_count}",
        f"- Bullish trend count: {bullish_count}",
        f"- Bearish trend count: {bearish_count}",
        f"- Mixed trend count: {mixed_count}",
        f"- Volume spike count: {volume_spike_count}",
        f"- Overbought RSI count: {overbought_count}",
        f"- Oversold RSI count: {oversold_count}",
    ]

    def add_ranked_section(title, items, value_key, formatter, reverse=True):
        lines.extend(["", f"{title}:"])
        ranked = [item for item in items if item.get(value_key) is not None]
        ranked.sort(key=lambda item: item.get(value_key), reverse=reverse)
        if not ranked:
            lines.append("- None")
            return
        for item in ranked[:5]:
            lines.append(f"- {item['ticker']}: {formatter(item.get(value_key))}")

    add_ranked_section("Top Gainers", valid, "day_change_percent", format_percent, True)
    add_ranked_section("Top Losers", valid, "day_change_percent", format_percent, False)
    add_ranked_section("Highest Relative Volume", valid, "relative_volume", format_multiple, True)
    add_ranked_section("Most Overbought", valid, "rsi", format_number, True)
    add_ranked_section("Most Oversold", valid, "rsi", format_number, False)

    near_high = [item for item in valid if item.get("percent_below_52w_high") is not None and item["percent_below_52w_high"] <= 3]
    near_low = [item for item in valid if item.get("percent_above_52w_low") is not None and item["percent_above_52w_low"] <= 5]
    lines.extend(["", "Near 52-Week High:"])
    lines.extend([f"- {item['ticker']}: {format_percent(item['percent_below_52w_high'])} below high" for item in near_high[:5]] or ["- None"])
    lines.extend(["", "Near 52-Week Low:"])
    lines.extend([f"- {item['ticker']}: {format_percent(item['percent_above_52w_low'])} above low" for item in near_low[:5]] or ["- None"])

    chase_items = []
    for item in valid:
        flags = calculate_do_not_chase_flags(item)
        if flags:
            chase_items.append((item, flags))
    lines.extend(["", "Do Not Chase Watch:"])
    if chase_items:
        for item, flags in chase_items[:5]:
            lines.append(f"- {item['ticker']}: {', '.join(flag['message'] for flag in flags)}")
    else:
        lines.append("- None")

    lines.extend(["", "Individual Watchlist Summary:"])

    for item in analyses:
        ticker = item["ticker"]
        if item.get("error"):
            lines.extend(["", f"{ticker}", "Data unavailable for this summary."])
            continue

        flags = calculate_risk_flags(item)
        score_data = calculate_signal_score(item)
        levels = calculate_support_resistance(ticker, item)
        lines.extend(
            [
                "",
                f"{get_price_direction_emoji(item.get('day_change'))} {ticker}",
                f"Close/Latest: {format_money(item['latest_price'])}",
                f"Day Change: {format_price_change(item)}",
                f"Day High: {format_money(item['day_high'])}",
                f"Day Low: {format_money(item['day_low'])}",
                f"Day Range: {format_percent(day_range_percent(item))}",
                f"RSI: {get_rsi_emoji(item['rsi'])} {format_number(item['rsi'])}",
                f"Rel Volume: {format_relative_volume(item['relative_volume'])}",
                f"5D Change: {format_percent(item['change_5d_percent'])}",
                f"Trend: {get_trend_emoji(item['trend'])} {item['trend']}",
                f"Support: {format_money(levels.get('nearest_support'))}",
                f"Resistance: {format_money(levels.get('nearest_resistance'))}",
                format_risk_flags(flags),
                format_signal_score(score_data),
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


def build_morning_briefing():
    """Build a weekday morning briefing from current bot data."""
    today = market_now().date().isoformat()
    tickers = load_watchlist()
    analyses = []
    for ticker in tickers:
        analysis = analyze_stock_price_only(ticker)
        if analysis:
            analyses.append(analysis)

    bullish = sum(1 for item in analyses if item.get("trend") == "Bullish")
    bearish = sum(1 for item in analyses if item.get("trend") == "Bearish")
    mixed = sum(1 for item in analyses if item.get("trend") == "Mixed")
    highest_rsi = max(analyses, key=lambda item: item.get("rsi") or -1, default=None)
    lowest_rsi = min(analyses, key=lambda item: item.get("rsi") if item.get("rsi") is not None else 999, default=None)
    highest_rel_volume = max(analyses, key=lambda item: item.get("relative_volume") or -1, default=None)

    earnings_rows = []
    for ticker in tickers:
        earnings_date = get_earnings_date(ticker)
        days_until = days_until_date(earnings_date) if earnings_date else None
        if days_until is not None and 0 <= days_until <= 7:
            earnings_rows.append((ticker, earnings_date, days_until))

    breakouts = [
        item for item in analyses
        if item.get("percent_below_52w_high") is not None
        and item["percent_below_52w_high"] <= 3
        and item.get("trend") == "Bullish"
    ]
    high_rel_volume = [
        item for item in analyses
        if item.get("trend") == "Bullish"
        and item.get("relative_volume") is not None
        and item["relative_volume"] >= 1.2
    ]

    risk_rows = []
    for item in analyses:
        flags = calculate_risk_flags(item)
        if flags:
            risk_rows.append((item["ticker"], flags))

    scanner_ideas = build_scanner_results("momentum", broad=False)[:3]

    lines = [
        f"Market Briefing \u2014 {today}",
        "",
        "1. Watchlist Overview",
        f"- Number of tracked stocks: {len(tickers)}",
        f"- Bullish / bearish / mixed: {bullish} / {bearish} / {mixed}",
        f"- Highest RSI: {(highest_rsi or {}).get('ticker', 'N/A')} {format_number((highest_rsi or {}).get('rsi'))}",
        f"- Lowest RSI: {(lowest_rsi or {}).get('ticker', 'N/A')} {format_number((lowest_rsi or {}).get('rsi'))}",
        f"- Highest relative volume: {(highest_rel_volume or {}).get('ticker', 'N/A')} {format_multiple((highest_rel_volume or {}).get('relative_volume'))}",
        "",
        "2. Watchlist Earnings",
    ]

    if earnings_rows:
        for ticker, earnings_date, days_until in earnings_rows[:10]:
            timing = "today" if days_until == 0 else "tomorrow" if days_until == 1 else f"in {days_until} days"
            lines.append(f"- {ticker}: {earnings_date} ({timing})")
    else:
        lines.append("- No watchlist earnings found in the next 7 days.")

    lines.extend(["", "3. Possible Breakouts"])
    breakout_lines = [f"- {item['ticker']}: {format_percent(item.get('percent_below_52w_high'))} below 52W high" for item in breakouts[:5]]
    volume_lines = [f"- {item['ticker']}: bullish trend, rel volume {format_multiple(item.get('relative_volume'))}" for item in high_rel_volume[:5]]
    lines.extend(breakout_lines + volume_lines or ["- No breakout-style watchlist candidates found right now."])

    lines.extend(["", "4. Risk Flags"])
    if risk_rows:
        for ticker, flags in risk_rows[:10]:
            lines.append(f"- {ticker}: {format_risk_flags(flags).replace('Risk Flags: ', '')}")
    else:
        lines.append("- No major risk flags found in the watchlist snapshot.")

    lines.extend(["", "5. Scanner Ideas"])
    if scanner_ideas:
        for item in scanner_ideas:
            lines.append(
                f"- {item['ticker']}: {format_signal_score(calculate_signal_score(item))}, "
                f"RSI {format_number(item.get('rsi'))}, rel volume {format_multiple(item.get('relative_volume'))}"
            )
    else:
        lines.append("- No scanner ideas available right now.")

    lines.extend(["", "6. WSB Attention"])
    if reddit_credentials_configured():
        mentions = load_wsb_mentions().get("mentions", [])[:5]
        if mentions:
            for mention in mentions:
                lines.append(f"- {mention['ticker']}: {mention.get('mention_count', 0)} mentions")
        else:
            lines.append("- No current WSB mention data found.")
    else:
        lines.append("- WSB tracker not configured.")

    lines.extend(["", "Morning briefings are for research only and are not financial advice."])
    return "\n".join(lines)


def earnings_now():
    return datetime.now(ZoneInfo(EARNINGS_TIMEZONE))


def is_empty_like(value):
    """Safely identify empty scalar and array-like values."""
    if value is None:
        return True

    if isinstance(value, str):
        return value.strip() == ""

    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0

    if isinstance(value, (pd.Series, pd.DataFrame, pd.Index)):
        return value.empty

    size = getattr(value, "size", None)
    if size is not None:
        try:
            return size == 0
        except Exception:
            return False

    return False


def is_null_scalar(value):
    if is_empty_like(value):
        return True

    try:
        result = pd.isna(value)
    except Exception:
        return False

    if isinstance(result, bool):
        return result

    return False


def first_valid_item(value):
    """Return the first non-empty, non-null item from scalar or array-like input."""
    if is_empty_like(value):
        return None

    if isinstance(value, pd.DataFrame):
        try:
            iterable = value.to_numpy().ravel().tolist()
        except Exception:
            return None
    elif isinstance(value, pd.Series):
        iterable = value.tolist()
    elif isinstance(value, pd.Index):
        iterable = value.tolist()
    elif isinstance(value, dict):
        iterable = value.values()
    elif isinstance(value, (list, tuple, set)):
        iterable = value
    elif hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        try:
            converted = value.tolist()
            iterable = converted if isinstance(converted, list) else [converted]
        except Exception:
            iterable = [value]
    else:
        return None if is_null_scalar(value) else value

    for item in iterable:
        candidate = first_valid_item(item)
        if candidate is not None and not is_null_scalar(candidate):
            return candidate

    return None


def normalize_earnings_date(value):
    value = first_valid_item(value)
    if value is None:
        return None

    try:
        timestamp = pd.to_datetime(value)
    except Exception:
        return None

    if is_null_scalar(timestamp):
        return None

    try:
        return timestamp.date().isoformat()
    except Exception:
        return None


def date_within_lookahead(date_text):
    if not date_text:
        return False

    try:
        earnings_date = datetime.fromisoformat(date_text).date()
    except ValueError:
        return False

    today = earnings_now().date()
    return today <= earnings_date <= today + timedelta(days=EARNINGS_LOOKAHEAD_DAYS)


def days_until_date(date_text):
    try:
        return (datetime.fromisoformat(date_text).date() - earnings_now().date()).days
    except ValueError:
        return None


def load_earnings_cache():
    data = read_json_file(EARNINGS_CACHE_FILE, {})
    if not isinstance(data, dict):
        return {}
    return data


def save_earnings_cache(cache):
    write_json_file(EARNINGS_CACHE_FILE, cache)


def parse_cache_timestamp(timestamp_text):
    if not timestamp_text:
        return None
    try:
        return datetime.fromisoformat(timestamp_text)
    except ValueError:
        return None


def get_cached_earnings_record(ticker):
    cache = load_earnings_cache()
    return cache.get(normalize_ticker(ticker), {})


def get_cached_earnings_date(ticker, max_age_hours=EARNINGS_CACHE_MAX_AGE_HOURS):
    record = get_cached_earnings_record(ticker)
    last_checked = parse_cache_timestamp(record.get("last_checked"))
    if last_checked is None:
        return None

    age = datetime.now() - last_checked
    if age > timedelta(hours=max_age_hours):
        return None

    return record.get("earnings_date")


def recently_rate_limited(ticker):
    record = get_cached_earnings_record(ticker)
    if record.get("status") != "rate_limited":
        return False

    last_checked = parse_cache_timestamp(record.get("last_checked"))
    if last_checked is None:
        return False

    return datetime.now() - last_checked < timedelta(hours=EARNINGS_RATE_LIMIT_RETRY_HOURS)


def set_cached_earnings_date(ticker, earnings_date, status="ok"):
    cache = load_earnings_cache()
    cache[normalize_ticker(ticker)] = {
        "earnings_date": earnings_date,
        "last_checked": datetime.now().isoformat(timespec="seconds"),
        "status": status,
    }
    save_earnings_cache(cache)


def clear_earnings_cache():
    save_earnings_cache({})


def get_earnings_date(ticker):
    """Try to retrieve the next quarterly report date through yfinance."""
    ticker = normalize_ticker(ticker)
    if is_probable_etf_or_fund(ticker):
        set_cached_earnings_date(ticker, None, status="etf")
        return None

    cached = get_cached_earnings_date(ticker)
    if cached is not None:
        return cached

    if recently_rate_limited(ticker):
        print(f"Skipping earnings calendar for {ticker}; recently rate limited.")
        return get_cached_earnings_record(ticker).get("earnings_date")

    if ticker in earnings_calendar_failures:
        failure_time = earnings_calendar_failures[ticker]
        if datetime.now() - failure_time < timedelta(hours=EARNINGS_RATE_LIMIT_RETRY_HOURS):
            print(f"Skipping earnings calendar for {ticker}; failed recently this session.")
            return get_cached_earnings_record(ticker).get("earnings_date")

    try:
        stock = yf.Ticker(ticker)
    except Exception as error:
        print(f"Could not create yfinance ticker for {ticker}: {error}")
        set_cached_earnings_date(ticker, None, status="error")
        return None

    calendar_candidates = []
    try:
        calendar_candidates.append(stock.calendar)
    except Exception as error:
        status = "rate_limited" if "too many requests" in str(error).lower() or "rate limited" in str(error).lower() else "error"
        print(f"Could not read stock.calendar for {ticker}: {status}.")
        earnings_calendar_failures[ticker] = datetime.now()
        set_cached_earnings_date(ticker, get_cached_earnings_record(ticker).get("earnings_date"), status=status)
        return get_cached_earnings_record(ticker).get("earnings_date")

    try:
        if hasattr(stock, "get_calendar"):
            calendar_candidates.append(stock.get_calendar())
    except Exception as error:
        status = "rate_limited" if "too many requests" in str(error).lower() or "rate limited" in str(error).lower() else "error"
        print(f"Could not read stock.get_calendar() for {ticker}: {status}.")
        earnings_calendar_failures[ticker] = datetime.now()
        set_cached_earnings_date(ticker, get_cached_earnings_record(ticker).get("earnings_date"), status=status)
        return get_cached_earnings_record(ticker).get("earnings_date")

    for calendar in calendar_candidates:
        if calendar is None:
            continue

        if isinstance(calendar, dict):
            for key, value in calendar.items():
                key_text = str(key).lower()
                if "earnings date" in key_text or "earnings average" in key_text:
                    normalized = normalize_earnings_date(value)
                    if normalized:
                        set_cached_earnings_date(ticker, normalized, status="ok")
                        return normalized

        if isinstance(calendar, pd.DataFrame):
            for label in list(calendar.index) + list(calendar.columns):
                label_text = str(label).lower()
                if "earnings date" not in label_text and "earnings average" not in label_text:
                    continue

                try:
                    value = calendar.loc[label].iloc[0] if label in calendar.index else calendar[label].iloc[0]
                except Exception:
                    continue

                normalized = normalize_earnings_date(value)
                if normalized:
                    set_cached_earnings_date(ticker, normalized, status="ok")
                    return normalized

        if isinstance(calendar, pd.Series):
            for key, value in calendar.items():
                key_text = str(key).lower()
                if "earnings date" in key_text or "earnings average" in key_text:
                    normalized = normalize_earnings_date(value)
                    if normalized:
                        set_cached_earnings_date(ticker, normalized, status="ok")
                        return normalized

    set_cached_earnings_date(ticker, None, status="missing")
    return None


def get_analyst_snapshot(ticker):
    """Return analyst snapshot values from yfinance when available."""
    ticker = normalize_ticker(ticker)
    empty_snapshot = {
        "recommendation_mean": None,
        "recommendation_key": None,
        "analyst_count": None,
        "target_mean_price": None,
        "current_price": None,
        "target_upside_percent": None,
    }

    if is_probable_etf_or_fund(ticker):
        set_cached_value("analyst", ticker, empty_snapshot, status="etf")
        return empty_snapshot

    cached = get_cached_value("analyst", ticker, max_age_minutes=1440)
    if cached:
        return cached
    if is_recently_rate_limited("analyst", ticker, retry_after_minutes=360):
        print(f"Skipping analyst snapshot for {ticker}; recently rate limited.")
        return empty_snapshot

    snapshot = dict(empty_snapshot)
    info = safe_get_ticker_info(ticker)
    if not info:
        set_cached_value("analyst", ticker, snapshot, status="missing")
        return snapshot

    snapshot["recommendation_mean"] = _to_float(info.get("recommendationMean"))
    snapshot["recommendation_key"] = info.get("recommendationKey")
    snapshot["analyst_count"] = info.get("numberOfAnalystOpinions")
    snapshot["target_mean_price"] = _to_float(info.get("targetMeanPrice"))
    snapshot["current_price"] = _to_float(info.get("currentPrice") or info.get("regularMarketPrice"))

    current_price = snapshot["current_price"]
    target_price = snapshot["target_mean_price"]
    if current_price is not None and target_price is not None and current_price > 0:
        snapshot["target_upside_percent"] = ((target_price - current_price) / current_price) * 100

    set_cached_value("analyst", ticker, snapshot)
    return snapshot


def build_watchlist_earnings_summary():
    """Build upcoming quarterly report summary for watchlist tickers."""
    rows = []
    for ticker in load_watchlist():
        earnings_date = get_earnings_date(ticker)
        if not date_within_lookahead(earnings_date):
            continue

        analysis = analyze_stock_price_only(ticker)
        rows.append(
            {
                "ticker": ticker,
                "earnings_date": earnings_date,
                "analysis": analysis,
            }
        )

    rows.sort(key=lambda item: item["earnings_date"])
    lines = [f"Watchlist Quarterly Reports — Next {EARNINGS_LOOKAHEAD_DAYS} Days"]
    if not rows:
        lines.extend(["", f"No upcoming watchlist earnings found in the next {EARNINGS_LOOKAHEAD_DAYS} days."])
    else:
        for index, row in enumerate(rows, start=1):
            analysis = row["analysis"] or {}
            lines.extend(
                [
                    "",
                    f"{index}. {EMOJI_EARNINGS} {get_price_direction_emoji(analysis.get('day_change'))} {row['ticker']}",
                    f"   {EMOJI_EARNINGS} Earnings Date: {row['earnings_date']}",
                    f"   Price: {format_money(analysis.get('latest_price'))}",
                    f"   Change: {format_price_change(analysis)}",
                    f"   RSI: {get_rsi_emoji(analysis.get('rsi'))} {format_number(analysis.get('rsi'))}",
                    f"   Trend: {get_trend_emoji(analysis.get('trend'))} {analysis.get('trend', 'N/A')}",
                    f"   5D Change: {format_percent(analysis.get('change_5d_percent'))}",
                ]
            )

    lines.extend(["", EARNINGS_DISCLAIMER])
    return "\n".join(lines)


def score_promising_earnings_candidate(stock_data, analyst_data):
    score = 0
    upside = analyst_data.get("target_upside_percent")
    recommendation_mean = analyst_data.get("recommendation_mean")
    analyst_count = analyst_data.get("analyst_count")
    price = stock_data.get("latest_price")

    if upside is not None and upside >= 20:
        score += 3
    elif upside is not None and upside >= 10:
        score += 2
    if recommendation_mean is not None and recommendation_mean <= 2.0:
        score += 2
    elif recommendation_mean is not None and recommendation_mean <= 2.5:
        score += 1
    if analyst_count is not None and analyst_count >= 10:
        score += 2
    elif analyst_count is not None and analyst_count >= 5:
        score += 1
    if stock_data.get("trend") == "Bullish":
        score += 2
    elif stock_data.get("trend") == "Mixed":
        score += 1
    if stock_data.get("rsi") is not None and 40 <= stock_data["rsi"] <= 70:
        score += 1
    if stock_data.get("relative_volume") is not None and stock_data["relative_volume"] >= 1.2:
        score += 1
    if price is not None and stock_data.get("ma50") is not None and price > stock_data["ma50"]:
        score += 1
    if price is not None and stock_data.get("ma200") is not None and price > stock_data["ma200"]:
        score += 1

    return score


def promising_candidate_reasons(stock_data, analyst_data):
    reasons = []
    if analyst_data.get("target_upside_percent") is not None:
        reasons.append(f"target upside {format_percent(analyst_data['target_upside_percent'])}")
    if analyst_data.get("recommendation_mean") is not None:
        reasons.append(f"recommendation mean {format_number(analyst_data['recommendation_mean'])}")
    if analyst_data.get("analyst_count") is not None:
        reasons.append(f"{analyst_data['analyst_count']} analyst opinions")
    if stock_data.get("trend") in {"Bullish", "Mixed"}:
        reasons.append(f"{stock_data['trend']} trend")
    if stock_data.get("rsi") is not None:
        reasons.append(f"RSI {format_number(stock_data['rsi'])}")
    if stock_data.get("relative_volume") is not None and stock_data["relative_volume"] >= 1.0:
        reasons.append(f"relative volume {format_multiple(stock_data['relative_volume'])}")
    return "; ".join(reasons) if reasons else "matched several earnings watch criteria"


def earnings_candidate_universe():
    tickers = list(load_scanner_universe())
    if not tickers:
        tickers = load_us_stock_universe()[:MAX_PROMISING_EARNINGS_SCAN_TICKERS]
    return [
        ticker for ticker in tickers
        if not is_probable_etf_or_fund(ticker)
    ][:MAX_PROMISING_EARNINGS_SCAN_TICKERS]


def find_promising_earnings_candidates():
    """Find analyst-supported earnings research candidates."""
    candidates = []
    tickers = earnings_candidate_universe()
    total = len(tickers)
    for index, ticker in enumerate(tickers, start=1):
        print(f"Checking earnings candidate {index}/{total}: {ticker}")
        time.sleep(0.5)
        if is_probable_etf_or_fund(ticker):
            continue
        earnings_date = get_earnings_date(ticker)
        if not date_within_lookahead(earnings_date):
            continue

        analyst_data = get_analyst_snapshot(ticker)
        stock_data = analyze_stock_price_only(ticker)
        if stock_data is None:
            continue

        price = stock_data.get("latest_price")
        passes_core = [
            analyst_data.get("recommendation_mean") is not None
            and analyst_data["recommendation_mean"] <= PROMISING_MAX_RECOMMENDATION_MEAN,
            analyst_data.get("analyst_count") is not None
            and analyst_data["analyst_count"] >= PROMISING_MIN_ANALYST_COUNT,
            analyst_data.get("target_upside_percent") is not None
            and analyst_data["target_upside_percent"] >= PROMISING_MIN_TARGET_UPSIDE_PERCENT,
            price is not None and price >= PROMISING_MIN_PRICE,
            stock_data.get("rsi") is not None and stock_data["rsi"] < PROMISING_MAX_RSI,
            stock_data.get("trend") in {"Bullish", "Mixed"},
            stock_data.get("relative_volume") is not None and stock_data["relative_volume"] >= 1.0,
            price is not None
            and (
                (stock_data.get("ma50") is not None and price > stock_data["ma50"])
                or (stock_data.get("ma200") is not None and price > stock_data["ma200"])
            ),
        ]

        if sum(1 for passed in passes_core if passed) < 4:
            continue

        score = score_promising_earnings_candidate(stock_data, analyst_data)
        days_until = days_until_date(earnings_date)
        candidates.append(
            {
                "ticker": ticker,
                "earnings_date": earnings_date,
                "stock_data": stock_data,
                "analyst_data": analyst_data,
                "score": score,
                "signal_score": calculate_signal_score(
                    stock_data,
                    analyst_data=analyst_data,
                    earnings_data={"earnings_date": earnings_date, "days_until": days_until},
                ),
                "risk_flags": calculate_risk_flags(
                    stock_data,
                    earnings_data={"earnings_date": earnings_date, "days_until": days_until},
                    analyst_data=analyst_data,
                ),
                "why_flagged": promising_candidate_reasons(stock_data, analyst_data),
            }
        )
        log_signal(ticker, "earnings_candidate", "earnings", stock_data, calculate_signal_score(stock_data, analyst_data=analyst_data), "medium", calculate_risk_flags(stock_data), ["Analyst-supported earnings candidate"])

    candidates.sort(key=lambda item: (item["score"], item["earnings_date"]), reverse=True)
    write_json_file(
        PROMISING_EARNINGS_FILE,
        {"timestamp": utc_timestamp(), "candidates": candidates[:15]},
    )
    return candidates[:15]


def build_promising_earnings_summary():
    try:
        candidates = find_promising_earnings_candidates()
    except Exception as error:
        print(f"Promising earnings scan failed: {error}")
        return (
            "Promising earnings scan could not complete because the data provider "
            "rate-limited requests. Try again later or reduce the scan size.\n\n"
            f"{EARNINGS_DISCLAIMER}"
        )

    lines = [f"Analyst-Supported Earnings Watch — Next {EARNINGS_LOOKAHEAD_DAYS} Days"]

    if not candidates:
        lines.extend(["", "No analyst-supported earnings research candidates found right now."])
    else:
        for index, candidate in enumerate(candidates, start=1):
            stock_data = candidate["stock_data"]
            analyst_data = candidate["analyst_data"]
            lines.extend(
                [
                    "",
                    f"{index}. {EMOJI_EARNINGS} {get_price_direction_emoji(stock_data.get('day_change'))} {candidate['ticker']}",
                    f"   {EMOJI_EARNINGS} Earnings Date: {candidate['earnings_date']}",
                    f"   Price: {format_money(stock_data.get('latest_price'))}",
                    f"   Change: {format_price_change(stock_data)}",
                    f"   RSI: {get_rsi_emoji(stock_data.get('rsi'))} {format_number(stock_data.get('rsi'))}",
                    f"   Trend: {get_trend_emoji(stock_data.get('trend'))} {stock_data.get('trend', 'N/A')}",
                    f"   Rel Volume: {format_relative_volume(stock_data.get('relative_volume'))}",
                    f"   Analyst Rating: {analyst_data.get('recommendation_key') or 'N/A'}",
                    f"   Analyst Count: {analyst_data.get('analyst_count') or 'N/A'}",
                    f"   Target Upside: {format_percent(analyst_data.get('target_upside_percent'))}",
                    f"   Score: {candidate['score']}",
                    f"   {format_signal_score(candidate.get('signal_score'))}",
                    f"   {format_risk_flags(candidate.get('risk_flags'))}",
                    f"   Why Flagged: {candidate['why_flagged']}",
                ]
            )

    lines.extend(["", EARNINGS_DISCLAIMER])
    return "\n".join(lines)


def earnings_channel(guild, preferred_channel_name):
    channel = get_channel_by_name(guild, preferred_channel_name)
    if channel:
        return channel

    fallback = get_channel_by_name(guild, EARNINGS_ALERTS_CHANNEL)
    if fallback:
        print(f"Warning: #{preferred_channel_name} is missing. Falling back to #{EARNINGS_ALERTS_CHANNEL}.")
        return fallback

    fallback = get_channel_by_name(guild, "stock-alerts")
    if fallback:
        print(f"Warning: #{preferred_channel_name} and #{EARNINGS_ALERTS_CHANNEL} are missing. Falling back to #stock-alerts.")
        return fallback

    print(f"Warning: #{preferred_channel_name}, #{EARNINGS_ALERTS_CHANNEL}, and #stock-alerts are missing.")
    return None


def should_run_earnings_weekly_summary():
    global last_earnings_weekly_summary_date

    now = earnings_now()
    if now.weekday() != EARNINGS_WEEKLY_SUMMARY_DAY:
        return False

    scheduled_time = datetime_time(EARNINGS_WEEKLY_SUMMARY_HOUR, EARNINGS_WEEKLY_SUMMARY_MINUTE)
    if now.time() < scheduled_time:
        return False

    return last_earnings_weekly_summary_date != now.date().isoformat()


def watchlist_earnings_alerts():
    alerts = []
    today = earnings_now().date()
    for ticker in load_watchlist():
        earnings_date_text = get_earnings_date(ticker)
        if not earnings_date_text:
            continue

        try:
            earnings_date = datetime.fromisoformat(earnings_date_text).date()
        except ValueError:
            continue

        days_until = (earnings_date - today).days
        if days_until in {0, 1, 2, 3}:
            alerts.append((ticker, earnings_date_text, days_until))
    return alerts


def utc_timestamp():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json_file(path, default_data):
    return read_json(path, default_data)


def write_json_file(path, data):
    write_json(path, data)


def load_signal_log():
    data = read_json_file(SIGNAL_LOG_FILE, {"signals": []})
    if not isinstance(data, dict) or not isinstance(data.get("signals"), list):
        data = {"signals": []}
        write_json_file(SIGNAL_LOG_FILE, data)
    return data


def save_signal_log(data):
    write_json_file(SIGNAL_LOG_FILE, data if isinstance(data, dict) else {"signals": []})


def risk_flag_messages(flags):
    messages = []
    for flag in flags or []:
        if isinstance(flag, dict):
            messages.append(flag.get("message", ""))
        else:
            messages.append(str(flag))
    return [message for message in messages if message]


def log_signal(ticker, signal_type, source, stock_data, signal_score=None, severity=None, risk_flags=None, notes=None):
    ticker = normalize_ticker(ticker)
    if not ticker or not stock_data:
        return None

    data = load_signal_log()
    today = market_now().date().isoformat()
    severity = severity or "low"
    for signal in data["signals"]:
        if (
            signal.get("ticker") == ticker
            and signal.get("source") == source
            and signal.get("signal_type") == signal_type
            and str(signal.get("timestamp", "")).startswith(today)
            and severity not in {"high", "critical"}
        ):
            return signal

    score_value = signal_score.get("score") if isinstance(signal_score, dict) else signal_score
    signal = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "signal_type": signal_type,
        "source": source,
        "price_at_signal": stock_data.get("latest_price") or stock_data.get("price"),
        "signal_score": score_value,
        "severity": severity,
        "risk_flags": risk_flag_messages(risk_flags),
        "notes": notes or [],
        "performance": {"1d": None, "5d": None, "10d": None},
    }
    data["signals"].append(signal)
    save_signal_log(data)
    return signal


def update_signal_performance():
    data = load_signal_log()
    changed = False
    now = datetime.now()
    for signal in data.get("signals", []):
        entry_price = signal.get("price_at_signal")
        if not entry_price:
            continue
        try:
            signal_time = datetime.fromisoformat(str(signal.get("timestamp")).replace("Z", ""))
        except (TypeError, ValueError):
            continue

        age_days = (now - signal_time).days
        for key, needed_days in [("1d", 1), ("5d", 5), ("10d", 10)]:
            if signal.get("performance", {}).get(key) is not None or age_days < needed_days:
                continue
            analysis = analyze_stock_price_only(signal.get("ticker"))
            latest_price = (analysis or {}).get("latest_price")
            if latest_price is None:
                continue
            signal.setdefault("performance", {})[key] = ((latest_price - entry_price) / entry_price) * 100
            changed = True

    if changed:
        save_signal_log(data)
    return data


def calculate_signal_performance_summary(days=30):
    cutoff = datetime.now() - timedelta(days=days)
    signals = []
    for signal in load_signal_log().get("signals", []):
        try:
            if datetime.fromisoformat(str(signal.get("timestamp")).replace("Z", "")) >= cutoff:
                signals.append(signal)
        except (TypeError, ValueError):
            continue

    by_type = {}
    for signal in signals:
        by_type.setdefault(signal.get("signal_type", "unknown"), []).append(signal)
    return {"days": days, "signals": signals, "by_type": by_type}


def average(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def format_signal_performance_summary(days=30):
    update_signal_performance()
    summary = calculate_signal_performance_summary(days)
    lines = [f"Signal Performance — Last {days} Days"]
    if not summary["signals"]:
        lines.append("No logged signals found for this period.")
    for signal_type, items in sorted(summary["by_type"].items()):
        one_day = average([item.get("performance", {}).get("1d") for item in items])
        five_day_values = [item.get("performance", {}).get("5d") for item in items if item.get("performance", {}).get("5d") is not None]
        five_day = average(five_day_values)
        win_rate = (sum(1 for value in five_day_values if value > 0) / len(five_day_values) * 100) if five_day_values else None
        lines.extend([
            "",
            f"{signal_type.title()} Signals:",
            f"- Count: {len(items)}",
            f"- Avg 1D Return: {format_percent(one_day)}",
            f"- Avg 5D Return: {format_percent(five_day)}",
            f"- Win Rate 5D: {format_percent(win_rate)}",
        ])

    ranked = [item for item in summary["signals"] if item.get("performance", {}).get("5d") is not None]
    ranked.sort(key=lambda item: item["performance"]["5d"], reverse=True)
    lines.extend(["", "Highest Performing Signals:"])
    lines.extend([f"{idx}. {item['ticker']} — {item['signal_type']} — {format_percent(item['performance']['5d'])}" for idx, item in enumerate(ranked[:5], start=1)] or ["None yet."])
    lines.extend(["", "Worst Performing Signals:"])
    lines.extend([f"{idx}. {item['ticker']} — {item['signal_type']} — {format_percent(item['performance']['5d'])}" for idx, item in enumerate(reversed(ranked[-5:]), start=1)] or ["None yet."])
    lines.extend(["", "Signal performance is historical tracking only and is not financial advice."])
    return "\n".join(lines)


def calculate_support_resistance(ticker, stock_data=None):
    ticker = normalize_ticker(ticker)
    stock_data = stock_data or analyze_stock_price_only(ticker) or {}
    price = stock_data.get("latest_price")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            history = yf.download(ticker, period="3mo", interval="1d", auto_adjust=False, progress=False, threads=False)
        data = get_download_frame(history, ticker)
    except Exception as error:
        print(f"Could not calculate levels for {ticker}: {error}")
        data = pd.DataFrame()

    support_levels = []
    resistance_levels = []
    if not data.empty and "Low" in data and "High" in data:
        lows = data["Low"].dropna()
        highs = data["High"].dropna()
        if len(lows) >= 2:
            support_levels.append(("Previous Day Low", _to_float(lows.iloc[-2])))
        if len(lows) >= 20:
            support_levels.append(("20-Day Low", _to_float(lows.tail(20).min())))
        if len(lows) >= 50:
            support_levels.append(("50-Day Low", _to_float(lows.tail(50).min())))
        if len(highs) >= 2:
            resistance_levels.append(("Previous Day High", _to_float(highs.iloc[-2])))
        if len(highs) >= 20:
            resistance_levels.append(("20-Day High", _to_float(highs.tail(20).max())))
        if len(highs) >= 50:
            resistance_levels.append(("50-Day High", _to_float(highs.tail(50).max())))
    if stock_data.get("fifty_two_week_high") is not None:
        resistance_levels.append(("52-Week High", stock_data.get("fifty_two_week_high")))

    support_values = [value for _label, value in support_levels if value is not None and (price is None or value <= price)]
    resistance_values = [value for _label, value in resistance_levels if value is not None and (price is None or value >= price)]
    nearest_support = max(support_values) if support_values else None
    nearest_resistance = min(resistance_values) if resistance_values else None
    return {
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "distance_to_support_percent": ((price - nearest_support) / price * 100) if price and nearest_support else None,
        "distance_to_resistance_percent": ((nearest_resistance - price) / price * 100) if price and nearest_resistance else None,
    }


def format_levels_message(ticker):
    stock_data = analyze_stock_price_only(ticker)
    if not stock_data:
        return f"Could not find price data for {normalize_ticker(ticker)}.\n\nResearch only — not financial advice."
    levels = calculate_support_resistance(ticker, stock_data)
    lines = [
        f"{stock_data['ticker']} Support / Resistance",
        "",
        f"Price: {format_money(stock_data.get('latest_price'))}",
        "",
        f"Nearest Support: {format_money(levels['nearest_support'])}",
        f"Distance to Support: {format_percent(levels['distance_to_support_percent'])}",
        "",
        f"Nearest Resistance: {format_money(levels['nearest_resistance'])}",
        f"Distance to Resistance: {format_percent(levels['distance_to_resistance_percent'])}",
        "",
        "Support Levels:",
    ]
    lines.extend([f"- {label}: {format_money(value)}" for label, value in levels["support_levels"]] or ["- N/A"])
    lines.append("")
    lines.append("Resistance Levels:")
    lines.extend([f"- {label}: {format_money(value)}" for label, value in levels["resistance_levels"]] or ["- N/A"])
    lines.extend(["", "Research only — not financial advice."])
    return "\n".join(lines)


def calculate_do_not_chase_flags(stock_data, earnings_data=None, reddit_data=None):
    flags = []
    rsi = stock_data.get("rsi")
    change_5d = stock_data.get("change_5d_percent")
    price = stock_data.get("latest_price")
    ma20 = stock_data.get("ma20")
    rel_volume = stock_data.get("relative_volume")
    if rsi is not None and rsi >= 80:
        flags.append({"severity": "high", "message": "RSI extremely overbought"})
    elif rsi is not None and rsi >= DO_NOT_CHASE_RSI:
        flags.append({"severity": "medium", "message": "RSI overbought"})
    if change_5d is not None and change_5d >= 20:
        flags.append({"severity": "high", "message": f"Up {format_percent(change_5d)} in 5 trading days"})
    elif change_5d is not None and change_5d >= DO_NOT_CHASE_5D_GAIN:
        flags.append({"severity": "medium", "message": f"Up {format_percent(change_5d)} in 5 trading days"})
    distance = ((price - ma20) / ma20 * 100) if price and ma20 else None
    if distance is not None and distance >= 10:
        flags.append({"severity": "high", "message": f"Price {format_percent(distance)} above 20MA"})
    elif distance is not None and distance >= DO_NOT_CHASE_DISTANCE_ABOVE_20MA:
        flags.append({"severity": "medium", "message": f"Price {format_percent(distance)} above 20MA"})
    if rel_volume is not None and rel_volume >= 3:
        flags.append({"severity": "high", "message": f"Relative volume {format_multiple(rel_volume)}"})
    return flags


def is_do_not_chase_candidate(stock_data, earnings_data=None, reddit_data=None):
    return bool(calculate_do_not_chase_flags(stock_data, earnings_data, reddit_data))


def build_do_not_chase_results():
    tickers = sorted(set(load_watchlist() + load_scanner_universe()))
    results = []
    for ticker in tickers[:150]:
        stock_data = analyze_stock_price_only(ticker)
        if not stock_data:
            continue
        flags = calculate_do_not_chase_flags(stock_data)
        if flags:
            stock_data["chase_flags"] = flags
            results.append(stock_data)
    results.sort(key=lambda item: (len(item["chase_flags"]), item.get("change_5d_percent") or 0), reverse=True)
    return results[:25]


def format_do_not_chase_message(results):
    lines = ["Do Not Chase Watch — Research Only"]
    if not results:
        lines.append("No extended setups matched the current do-not-chase thresholds.")
    for index, item in enumerate(results, start=1):
        distance = ((item["latest_price"] - item["ma20"]) / item["ma20"] * 100) if item.get("latest_price") and item.get("ma20") else None
        lines.extend([
            "",
            f"{index}. {EMOJI_WARNING} {item['ticker']}",
            f"   Price: {format_money(item.get('latest_price'))}",
            f"   5D Change: {format_percent(item.get('change_5d_percent'))}",
            f"   RSI: {format_number(item.get('rsi'))}",
            f"   Distance Above 20MA: {format_percent(distance)}",
            f"   Rel Volume: {format_multiple(item.get('relative_volume'))}",
            "   Why Caution:",
        ])
        lines.extend([f"   - {flag['message']}" for flag in item.get("chase_flags", [])])
        lines.append("   This may still go higher, but the setup is extended.")
    lines.extend(["", "Do Not Chase warnings are risk reminders only and are not financial advice."])
    return "\n".join(lines)


def build_best_setups_of_day():
    tickers = set(load_watchlist())
    tickers.update(load_scanner_universe())
    tickers.update(item.get("ticker") for item in load_wsb_tracking().get("tracked", []))
    for item in read_json_file(PAULS_TRACKER_FILE, {}).get("five_day_runners", []):
        tickers.add(item.get("ticker"))
    for item in read_json_file(PROMISING_EARNINGS_FILE, {}).get("candidates", []):
        tickers.add(item.get("ticker"))

    results = []
    for ticker in sorted({normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)})[:200]:
        stock_data = analyze_stock_price_only(ticker)
        if not stock_data:
            continue
        score_data = calculate_signal_score(stock_data)
        risk_flags = calculate_risk_flags(stock_data)
        if any(flag.get("severity") == "critical" for flag in risk_flags):
            continue
        if score_data["score"] < BEST_SETUPS_MIN_SCORE:
            continue
        price = stock_data.get("latest_price")
        if stock_data.get("trend") not in {"Bullish", "Mixed"}:
            continue
        if stock_data.get("rsi") is not None and not (40 <= stock_data["rsi"] <= 75):
            continue
        levels = calculate_support_resistance(ticker, stock_data)
        reasons = score_data.get("notes", [])[:4] or ["Matched signal score and trend criteria"]
        stock_data.update({"signal_score": score_data, "risk_flags": risk_flags, "levels": levels, "why_flagged": reasons})
        results.append(stock_data)
    results.sort(key=lambda item: item["signal_score"]["score"], reverse=True)
    results = results[:BEST_SETUPS_RESULT_LIMIT]
    for item in results:
        log_signal(item["ticker"], "best_setup", "best_setups", item, item.get("signal_score"), "medium", item.get("risk_flags"), item.get("why_flagged"))
    return results


def format_best_setups_message(results):
    lines = ["Best Setups of the Day — Research Only"]
    if not results:
        lines.append("No best setup research candidates matched the current filters.")
    for index, item in enumerate(results, start=1):
        levels = item.get("levels", {})
        lines.extend([
            "",
            f"{index}. {get_price_direction_emoji(item.get('day_change'))} {item['ticker']} — {format_signal_score(item.get('signal_score')).replace('Signal Score: ', '')}",
            f"   Price: {format_money(item.get('latest_price'))}",
            f"   Change: {format_change_percent_only(item)}",
            f"   RSI: {format_number(item.get('rsi'))}",
            f"   Trend: {get_trend_emoji(item.get('trend'))} {item.get('trend')}",
            f"   Rel Volume: {format_multiple(item.get('relative_volume'))}",
            f"   Support: {format_money(levels.get('nearest_support'))}",
            f"   Resistance: {format_money(levels.get('nearest_resistance'))}",
            "   Why Flagged:",
        ])
        lines.extend([f"   - {reason}" for reason in item.get("why_flagged", [])])
        lines.append(f"   {format_risk_flags(item.get('risk_flags'))}")
    lines.extend(["", "Best Setups are research candidates only and are not financial advice."])
    return "\n".join(lines)


def default_watchlist_groups():
    return {
        "groups": {
            "default": load_watchlist(),
            "semiconductors": ["NVDA", "AMD", "AVGO", "QCOM", "MU", "SMH"],
            "big-tech": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA"],
            "etfs": ["SPY", "QQQ", "IWM", "DIA", "SMH"],
        },
        "alerts_enabled": {"default": True},
    }


def load_watchlist_groups():
    data = read_json_file(WATCHLIST_GROUPS_FILE, default_watchlist_groups())
    if not isinstance(data, dict) or not isinstance(data.get("groups"), dict):
        data = default_watchlist_groups()
        write_json_file(WATCHLIST_GROUPS_FILE, data)
    data.setdefault("alerts_enabled", {"default": True})
    data["groups"]["default"] = load_watchlist()
    return data


def save_watchlist_groups(data):
    data.setdefault("groups", {})
    data.setdefault("alerts_enabled", {})
    write_json_file(WATCHLIST_GROUPS_FILE, data)


def get_group_tickers(group_name):
    data = load_watchlist_groups()
    return sorted({normalize_ticker(ticker) for ticker in data.get("groups", {}).get(group_name, []) if normalize_ticker(ticker)})


def group_scan_results(tickers, scan_type="momentum", filters=None):
    results = []
    for ticker in tickers:
        stock_data = analyze_stock_price_only(ticker)
        if not stock_data:
            continue
        if filters:
            matched, reasons = stock_matches_custom_filters(stock_data, filters)
            if not matched:
                continue
            stock_data["custom_score"] = score_custom_match(stock_data, filters)
            stock_data["match_reasons"] = reasons
            stock_data["signal_score"] = calculate_signal_score(stock_data)
            stock_data["risk_flags"] = calculate_risk_flags(stock_data)
            log_signal(ticker, "custom_group_scan", "group_scanner", stock_data, stock_data["signal_score"], "low", stock_data["risk_flags"], reasons)
            results.append(stock_data)
        else:
            score = calculate_score(stock_data, scan_type)
            if score <= 0:
                continue
            stock_data["scanner_score"] = score
            stock_data["scanner_signal"] = scanner_signal(stock_data, scan_type)
            stock_data["signal_score"] = calculate_signal_score(stock_data)
            stock_data["risk_flags"] = calculate_risk_flags(stock_data)
            log_signal(ticker, scan_type, "group_scanner", stock_data, stock_data["signal_score"], "low", stock_data["risk_flags"], [stock_data["scanner_signal"]])
            results.append(stock_data)
    key = "custom_score" if filters else "scanner_score"
    results.sort(key=lambda item: item.get(key, 0), reverse=True)
    return results[:SCAN_RESULT_LIMIT]


def get_options_expirations(ticker):
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return list(yf.Ticker(normalize_ticker(ticker)).options or [])
    except Exception as error:
        print(f"Could not fetch options expirations for {ticker}: {error}")
        return []


def get_options_chain(ticker, expiration):
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return yf.Ticker(normalize_ticker(ticker)).option_chain(expiration)
    except Exception as error:
        print(f"Could not fetch options chain for {ticker} {expiration}: {error}")
        return None


def option_mid_price(row):
    bid = _to_float(row.get("bid"))
    ask = _to_float(row.get("ask"))
    last = _to_float(row.get("lastPrice"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return last


def analyze_options_chain(ticker, expiration=None):
    ticker = normalize_ticker(ticker)
    stock_data = analyze_stock_price_only(ticker)
    price = (stock_data or {}).get("latest_price")
    expirations = get_options_expirations(ticker)
    if not expirations:
        return {"ticker": ticker, "stock_price": price, "expirations": [], "error": "No options expirations found."}

    today = market_now().date()
    selected = expiration
    if selected is None:
        for exp in expirations:
            try:
                dte = (datetime.fromisoformat(exp).date() - today).days
            except ValueError:
                continue
            if OPTIONS_MIN_DTE <= dte <= OPTIONS_MAX_DTE:
                selected = exp
                break
        selected = selected or expirations[0]

    chain = get_options_chain(ticker, selected)
    if chain is None:
        return {"ticker": ticker, "stock_price": price, "expirations": expirations, "expiration": selected, "error": "Could not load options chain."}

    try:
        dte = (datetime.fromisoformat(selected).date() - today).days
    except ValueError:
        dte = None

    def parse_contracts(frame, side):
        rows = []
        for _idx, row in frame.iterrows():
            bid = _to_float(row.get("bid"))
            ask = _to_float(row.get("ask"))
            if (bid is None or bid == 0) and (ask is None or ask == 0):
                continue
            mid = option_mid_price(row)
            if mid is None or mid <= 0:
                continue
            spread = ((ask - bid) / mid * 100) if bid is not None and ask is not None and mid else None
            volume = row.get("volume") if pd.notna(row.get("volume")) else 0
            open_interest = row.get("openInterest") if pd.notna(row.get("openInterest")) else 0
            if volume < OPTIONS_MIN_VOLUME or open_interest < OPTIONS_MIN_OPEN_INTEREST:
                continue
            if spread is not None and spread > OPTIONS_MAX_SPREAD_PERCENT:
                continue
            strike = _to_float(row.get("strike"))
            break_even = (strike + mid) if side == "call" and strike is not None else (strike - mid) if strike is not None else None
            rows.append({
                "contractSymbol": row.get("contractSymbol"),
                "strike": strike,
                "lastPrice": _to_float(row.get("lastPrice")),
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "volume": int(volume or 0),
                "openInterest": int(open_interest or 0),
                "impliedVolatility": _to_float(row.get("impliedVolatility")),
                "spread_percent": spread,
                "moneyness": ((strike - price) / price * 100) if price and strike else None,
                "days_to_expiration": dte,
                "break_even": break_even,
                "volume_oi_ratio": (volume / open_interest) if open_interest else None,
                "side": side,
            })
        rows.sort(key=lambda item: (item["volume"], item["openInterest"]), reverse=True)
        return rows

    calls = parse_contracts(chain.calls, "call")
    puts = parse_contracts(chain.puts, "put")
    return {"ticker": ticker, "stock_price": price, "expirations": expirations, "expiration": selected, "days_to_expiration": dte, "calls": calls, "puts": puts}


def format_options_contracts(title, contracts):
    lines = [title]
    if not contracts:
        lines.append("- No contracts matched the liquidity/spread filters.")
        return lines
    for index, item in enumerate(contracts[:5], start=1):
        lines.extend([
            f"{index}. Strike {format_money(item.get('strike'))}",
            f"   Mid: {format_money(item.get('mid'))}",
            f"   Bid/Ask: {format_money(item.get('bid'))} / {format_money(item.get('ask'))}",
            f"   Volume: {format_integer(item.get('volume'))}",
            f"   Open Interest: {format_integer(item.get('openInterest'))}",
            f"   IV: {format_percent((item.get('impliedVolatility') or 0) * 100)}",
            f"   Break-even: {format_money(item.get('break_even'))}",
            f"   Spread: {format_percent(item.get('spread_percent'))}",
        ])
    return lines


def format_options_message(data):
    if data.get("error"):
        return f"Options Check — {data.get('ticker')}\n{data['error']}\n\nOptions data is for research only and is not financial advice."
    lines = [
        f"Options Check — {data['ticker']}",
        "",
        f"Stock Price: {format_money(data.get('stock_price'))}",
        f"Expiration: {data.get('expiration')}",
        f"Days to Expiration: {data.get('days_to_expiration', 'N/A')}",
        "",
    ]
    lines.extend(format_options_contracts("Top Liquid Calls:", data.get("calls", [])))
    lines.append("")
    lines.extend(format_options_contracts("Top Liquid Puts:", data.get("puts", [])))
    lines.extend([
        "",
        "Risk Notes:",
        "- Wide bid/ask spreads can make entries/exits difficult",
        "- Earnings before expiration may cause IV crush",
        "- Options can lose 100% of premium",
        "",
        "Options data is for research only and is not financial advice.",
    ])
    return "\n".join(lines)


def format_unusual_options_message(ticker):
    data = analyze_options_chain(ticker)
    if data.get("error"):
        return format_options_message(data)
    unusual = [
        item for item in (data.get("calls", []) + data.get("puts", []))
        if item.get("volume_oi_ratio") is not None and item["volume_oi_ratio"] > 1.0 and item.get("volume", 0) > 100
    ]
    unusual.sort(key=lambda item: (item.get("volume_oi_ratio") or 0, item.get("volume") or 0), reverse=True)
    lines = [f"Unusual Options — {data['ticker']}", "", f"Stock Price: {format_money(data.get('stock_price'))}", f"Expiration: {data.get('expiration')}"]
    if not unusual:
        lines.append("No unusual options contracts matched the current filters.")
    for index, item in enumerate(unusual[:10], start=1):
        lines.extend([
            "",
            f"{index}. {item['side'].title()} {format_money(item.get('strike'))}",
            f"   Mid: {format_money(item.get('mid'))}",
            f"   Volume/OI: {format_multiple(item.get('volume_oi_ratio'))}",
            f"   Volume: {format_integer(item.get('volume'))}",
            f"   Open Interest: {format_integer(item.get('openInterest'))}",
            f"   Spread: {format_percent(item.get('spread_percent'))}",
        ])
    lines.extend(["", "Options data is for research only and is not financial advice."])
    return "\n".join(lines)


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
    if praw is None:
        print("PRAW is not installed. Install requirements.txt to enable the WSB tracker.")
        return None

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
        analysis = analyze_stock_price_only(ticker)
        if analysis is None:
            continue

        tracked.append(
            {
                "timestamp": utc_timestamp(),
                "ticker": ticker,
                "price": analysis.get("latest_price"),
                "day_change": analysis.get("day_change"),
                "day_change_percent": analysis.get("day_change_percent"),
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
        activity_emoji = EMOJI_VOLUME if mention.get("mention_count", 0) >= 10 else EMOJI_RESEARCH
        lines.extend(
            [
                "",
                f"{index}. {activity_emoji} {mention['ticker']}",
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
        activity_emoji = EMOJI_VOLUME if item.get("mention_count", 0) >= 10 or (item.get("relative_volume") or 0) >= 2 else EMOJI_RESEARCH
        lines.extend(
            [
                "",
                f"{index}. {activity_emoji} {get_price_direction_emoji(item.get('day_change'))} {item['ticker']}",
                f"   Price: {format_money(item['price'])}",
                f"   Change: {format_change_percent_only(item)}",
                f"   RSI: {get_rsi_emoji(item['rsi'])} {format_number(item['rsi'])}",
                f"   Rel Volume: {format_relative_volume(item['relative_volume'])}",
                f"   5D Change: {format_percent(item['change_5d_percent'])}",
                f"   Trend: {get_trend_emoji(item['trend'])} {item['trend']}",
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
    activity_emoji = EMOJI_VOLUME if item.get("mention_count", 0) >= 10 or (item.get("relative_volume") or 0) >= 2 else EMOJI_RESEARCH
    return (
        f"{activity_emoji} {get_price_direction_emoji(item.get('day_change'))} WSB Attention Alert — {item['ticker']}\n\n"
        "Reason:\n"
        + "\n".join(f"- {reason}" for reason in reasons)
        + "\n\nMarket data:\n"
        f"Price: {format_money(item['price'])}\n"
        f"Change: {format_change_percent_only(item)}\n"
        f"RSI: {get_rsi_emoji(item['rsi'])} {format_number(item['rsi'])}\n"
        f"Rel Volume: {format_relative_volume(item['relative_volume'])}\n"
        f"5D Change: {format_percent(item['change_5d_percent'])}\n"
        f"Trend: {get_trend_emoji(item['trend'])} {item['trend']}\n\n"
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

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None, case_insensitive=True)


def build_guide_messages():
    """Return the automatic guide messages keyed by channel name."""
    return {
        "how-to-read-alerts": """[STOCK_BOT_GUIDE_MESSAGE]
How to Read Stock Bot Alerts

Alerts are research signals, not buy/sell instructions. The bot watches stocks for movement, RSI, volume, earnings, Reddit attention, and custom scanner criteria.

A stock can be flagged for being strong, weak, risky, or unusual. Always read the reason for the alert before acting. Alerts may be delayed or affected by data-provider limits. The bot does not place real trades.

Example:

🚨 High Alert — NVDA
Reason: Near 52-week high + relative volume spike
Price: $___
Change: 🟢 📈 +2.4%
RSI: ⚠️ 72.1
Signal Score: 8.0/10 — Strong
Risk Flags:
- RSI overbought
- Earnings within 7 days

How to read it:
- The alert title tells you the main signal.
- Price/change shows current direction.
- RSI shows whether the stock may be stretched or oversold.
- Signal Score summarizes strength.
- Risk Flags show reasons to be cautious.

All bot alerts are for research only and are not financial advice.""",
        "emoji-legend": """[STOCK_BOT_GUIDE_MESSAGE]
Emoji Legend

🟢 📈 = Stock is up / green day
🔴 📉 = Stock is down / red day
⚪ ➖ = Flat or neutral
🔥 = Unusual volume or unusual attention
⚠️ = Caution, risk, or overbought condition
🧊 = Oversold condition
🚀 = Near breakout, strong move, or near 52-week high
📅 = Earnings / quarterly report
🔎 = Research signal
🟡 = Medium priority
🔴 = High priority
🚨 = Critical alert

Important:
- Green does not automatically mean buy.
- Red does not automatically mean sell.
- Fire means unusual activity, not guaranteed opportunity.
- Warning means slow down and check the details.

Emojis are visual shortcuts, not trading instructions.""",
        "scanner-guide": """[STOCK_BOT_GUIDE_MESSAGE]
How to Read Scanner Results

Scanner results are stocks that matched a set of filters. Preset scans include momentum, breakouts, oversold, pullbacks, and volume. Custom scans let users search with filters like `rsi>70`, `price>10`, and `relvol>1.5`.

Broad scans search a bigger universe and may take longer. Results are ranked by how closely they match the scan criteria.

Example:

Scanner Results — Momentum

1. 🟢 📈 AMD
   Price: $___
   Change: +3.2%
   RSI: 64.5
   Trend: 🟢 Bullish
   Rel Volume: 🔥 1.8x
   Signal Score: 7.5/10
   Match Reasons:
   - Price above 20MA, 50MA, and 200MA
   - RSI between 45 and 70
   - Relative volume above average

How to read it:
- Match Reasons show why the stock appeared.
- Signal Score summarizes the setup.
- Risk Flags should still be checked.
- Scanner results are not buy recommendations.

More research tools:
- Best Setups ranks possible setups by signal score, trend, volume, momentum, risk flags, and support/resistance context.
- Do Not Chase warnings flag stocks that may be extended by RSI, 5-day move, distance above the 20MA, or unusual activity.
- Support/resistance levels show nearby price areas from recent highs and lows. They are context, not instructions.
- Options checks explain liquidity, spread, open interest, implied volatility, days to expiration, and break-even.
- Watchlist groups let you organize tickers by theme and run checks or scans on one group at a time.

Examples:
`!scan momentum`
`!scan oversold`
`!scan custom rsi>70 price>10`
`!scan custom rsi=45-65 above50ma=true above200ma=true`
`!scan all custom relvol>1.5 change5d>3`
`!bestsetups`
`!donotchase`
`!levels AAPL`
`!options AAPL`
`!groups`

Scanner results are for research only and are not financial advice.""",
        "risk-and-score-guide": """[STOCK_BOT_GUIDE_MESSAGE]
Signal Score and Risk Flags Guide

Signal Score:
- 0-3 = Weak
- 4-6 = Moderate
- 7-8 = Strong
- 9-10 = Very Strong

The score combines trend, volume, momentum, RSI, analyst data, earnings risk, and Reddit attention when available.

Risk Flags:
Risk flags are caution notes. They do not always mean avoid a stock. They mean the setup has something worth checking before making a decision.

Risk flag examples:
- Earnings today/tomorrow
- RSI extremely overbought
- Stock extended after a large 5-day move
- Below 200-day moving average
- Low volume
- Very high Reddit attention
- Data incomplete

Support and resistance:
- Support is a nearby area where price recently found buyers.
- Resistance is a nearby area where price recently stalled.
- A move near support or resistance can be useful context, but it is not a trade instruction.

Do Not Chase warnings:
- These appear when a stock may be exciting but extended.
- Extended stocks can still move higher, but the risk/reward may be less forgiving.

Example:

Signal Score: 8.0/10 — Strong
Risk Flags:
⚠️ RSI overbought
📅 Earnings within 3 days
🔥 Relative volume above 2.0x

How to read it:
- Strong signal score with high risk flags means the stock may be interesting but risky.
- Low signal score with many risk flags means the setup is weaker.
- Missing data should always be treated carefully.

Signal scores and risk flags are research tools, not financial advice.""",
        "paper-trading-guide": """[STOCK_BOT_GUIDE_MESSAGE]
Paper Trading Guide

Paper trading is simulated trading only. It does not buy or sell real stocks. Use it to test ideas from alerts and scanners. Paper trades are saved in the bot journal.

Commands:
`!paperbuy AAPL 10 195.20 breakout setup`
`!papersell AAPL 10 205.00 taking profit`
`!paperportfolio`
`!paperpnl`
`!paperjournal`
`!paperclose AAPL 210.00 closing test trade`
`!paperclear CONFIRM`

How to read the command:
- Quantity is simulated share count.
- Price is the simulated entry/exit price.
- Reason is optional but useful for learning.
- Use paper trading to see whether the bot's signals are actually helpful over time.

Paper trading is simulated only and does not place real trades.""",
    }


async def post_or_update_guide_messages(guild):
    """Post or update one pinned guide message in each guide channel."""
    global guide_pin_permission_warning_printed

    if guild is None:
        return

    for channel_name, guide_message in build_guide_messages().items():
        channel = get_channel_by_name(guild, channel_name)
        if channel is None:
            print(f"Guide channel #{channel_name} is missing.")
            continue

        existing_message = None
        try:
            for message in await channel.pins():
                if message.author == bot.user and GUIDE_MESSAGE_MARKER in (message.content or ""):
                    existing_message = message
                    break
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"Could not read pinned guide messages in #{channel_name}: {error}")

        try:
            async for message in channel.history(limit=50):
                if existing_message:
                    break
                if message.author == bot.user and GUIDE_MESSAGE_MARKER in (message.content or ""):
                    existing_message = message
                    break
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"Could not search guide history in #{channel_name}: {error}")

        try:
            if existing_message:
                await existing_message.edit(content=guide_message)
                guide_post = existing_message
                print(f"Updated guide message in #{channel_name}.")
            else:
                guide_post = await channel.send(guide_message)
                print(f"Posted guide message in #{channel_name}.")
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"Could not post guide message in #{channel_name}: {error}")
            continue

        if not guide_post.pinned:
            try:
                await guide_post.pin(reason="Stock bot guide message")
                print(f"Pinned guide message in #{channel_name}.")
            except discord.Forbidden:
                if not guide_pin_permission_warning_printed:
                    print("Guide message posted but could not be pinned. Give the bot Manage Messages permission to allow pinning.")
                    guide_pin_permission_warning_printed = True
            except discord.HTTPException as error:
                print(f"Guide message posted but could not be pinned: {error}")


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


async def send_stock_alert(guild, channel_name, message, severity="low"):
    if QUIET_MODE and severity != "critical":
        print(f"Quiet mode suppressed {severity} alert for #{channel_name}.")
        return
    if not severity_allows(severity, MIN_ALERT_SEVERITY):
        print(f"Suppressed {severity} alert below minimum severity {MIN_ALERT_SEVERITY}.")
        return

    channel = get_channel_by_name(guild, channel_name)
    if channel is None and channel_name != "stock-alerts":
        channel = get_channel_by_name(guild, "stock-alerts")

    if channel is None:
        print(f"Missing alert channel #{channel_name}, and #stock-alerts was not found.")
        return

    if "financial advice" not in message.lower():
        message = f"{message}\n\nAlerts are for research only and are not financial advice."

    await channel.send(f"{SEVERITY_EMOJI.get(severity, '')} {severity.title()} Alert\n{message}")


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
    signal_score = calculate_signal_score(analysis)
    risk_flags = calculate_risk_flags(analysis)

    if should_send_alert(ticker, "summary"):
        log_signal(ticker, "summary", "stock_alert", analysis, signal_score, "low", risk_flags, ["Watchlist summary alert"])
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
        log_signal(ticker, "near_daily_high", "stock_alert", analysis, signal_score, "medium", risk_flags, ["Near daily high"])
        await send_stock_alert(
            guild,
            "daily-highs",
            f"{get_signal_emoji('Near Daily High')} {ticker} — Near Daily High\nPrice: {format_money(price)}, Day High: {format_money(analysis['day_high'])}",
        )

    if (
        analysis["day_low"] is not None
        and price <= analysis["day_low"] * NEAR_DAILY_LOW_THRESHOLD
        and should_send_alert(ticker, "daily_low")
    ):
        log_signal(ticker, "near_daily_low", "stock_alert", analysis, signal_score, "medium", risk_flags, ["Near daily low"])
        await send_stock_alert(
            guild,
            "daily-lows",
            f"{get_signal_emoji('Near Daily Low')} {ticker} — Near Daily Low\nPrice: {format_money(price)}, Day Low: {format_money(analysis['day_low'])}",
        )

    if (
        analysis["fifty_two_week_high"] is not None
        and price >= analysis["fifty_two_week_high"] * NEAR_52W_HIGH_THRESHOLD
        and should_send_alert(ticker, "52w_high")
    ):
        log_signal(ticker, "near_52_week_high", "stock_alert", analysis, signal_score, "medium", risk_flags, ["Near 52-week high"])
        await send_stock_alert(
            guild,
            "fifty-two-week-highs",
            f"{get_signal_emoji('Near 52-Week High')} {ticker} — Near 52-Week High\nPrice: {format_money(price)}, 52W High: {format_money(analysis['fifty_two_week_high'])}",
        )

    if (
        analysis["fifty_two_week_low"] is not None
        and price <= analysis["fifty_two_week_low"] * NEAR_52W_LOW_THRESHOLD
        and should_send_alert(ticker, "52w_low")
    ):
        log_signal(ticker, "near_52_week_low", "stock_alert", analysis, signal_score, "medium", risk_flags, ["Near 52-week low"])
        await send_stock_alert(
            guild,
            "fifty-two-week-lows",
            f"{get_signal_emoji('Near 52-Week Low')} {ticker} — Near 52-Week Low\nPrice: {format_money(price)}, 52W Low: {format_money(analysis['fifty_two_week_low'])}",
        )

    if analysis["rsi"] is not None and analysis["rsi"] >= RSI_OVERBOUGHT:
        if should_send_alert(ticker, "rsi_overbought"):
            log_signal(ticker, "rsi_overbought", "stock_alert", analysis, signal_score, "medium", risk_flags, ["RSI overbought"])
            await send_stock_alert(
                guild,
                "rsi-alerts",
                f"{get_signal_emoji('RSI Overbought')} {ticker} — RSI Overbought\nRSI: {format_number(analysis['rsi'])}",
            )

    if analysis["rsi"] is not None and analysis["rsi"] <= RSI_OVERSOLD:
        if should_send_alert(ticker, "rsi_oversold"):
            log_signal(ticker, "rsi_oversold", "stock_alert", analysis, signal_score, "medium", risk_flags, ["RSI oversold"])
            await send_stock_alert(
                guild,
                "rsi-alerts",
                f"{get_signal_emoji('RSI Oversold')} {ticker} — RSI Oversold\nRSI: {format_number(analysis['rsi'])}",
            )

    if analysis["volume_spike"] and should_send_alert(ticker, "volume_spike"):
        log_signal(ticker, "volume_spike", "stock_alert", analysis, signal_score, "high", risk_flags, ["Volume spike"])
        await send_stock_alert(
            guild,
            "volume-spikes",
            f"{get_signal_emoji('Volume Spike')} {ticker} — Volume Spike\nLatest 5-minute volume: {format_number(analysis['latest_volume'])}, Average: {format_number(analysis['average_volume'])}",
        )


async def process_levels_alerts(guild, analysis):
    ticker = analysis.get("ticker")
    levels = await asyncio.to_thread(calculate_support_resistance, ticker, analysis)
    distance_resistance = levels.get("distance_to_resistance_percent")
    distance_support = levels.get("distance_to_support_percent")
    if distance_resistance is not None and distance_resistance <= LEVELS_ALERT_DISTANCE_PERCENT and analysis.get("trend") == "Bullish":
        if should_send_alert(ticker, "near_resistance"):
            score_data = calculate_signal_score(analysis)
            risk_flags = calculate_risk_flags(analysis)
            log_signal(ticker, "near_resistance", "levels_alert", analysis, score_data, "medium", risk_flags, ["Price near resistance"])
            await send_stock_alert(
                guild,
                "levels-alerts",
                f"{ticker} near resistance\nPrice: {format_money(analysis.get('latest_price'))}\nResistance: {format_money(levels.get('nearest_resistance'))}\nDistance: {format_percent(distance_resistance)}",
                "medium",
            )
    if distance_support is not None and distance_support <= LEVELS_ALERT_DISTANCE_PERCENT:
        if should_send_alert(ticker, "near_support"):
            score_data = calculate_signal_score(analysis)
            risk_flags = calculate_risk_flags(analysis)
            log_signal(ticker, "near_support", "levels_alert", analysis, score_data, "medium", risk_flags, ["Price near support"])
            await send_stock_alert(
                guild,
                "levels-alerts",
                f"{ticker} near support\nPrice: {format_money(analysis.get('latest_price'))}\nSupport: {format_money(levels.get('nearest_support'))}\nDistance: {format_percent(distance_support)}",
                "medium",
            )


@tasks.loop(hours=24)
async def daily_scanner_loop():
    try:
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
    except Exception as error:
        print(f"Daily scanner loop failed safely: {error}")


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
    if MARKET_HOURS_ONLY and not is_regular_market_hours_now():
        print("Stock alert loop skipped outside regular market hours.")
        return

    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("Stock alert loop could not find the configured Discord server.")
        return

    tickers = set(load_watchlist())
    groups = load_watchlist_groups()
    for group_name, enabled in groups.get("alerts_enabled", {}).items():
        if enabled:
            tickers.update(groups.get("groups", {}).get(group_name, []))
    tickers = sorted({normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)})
    if not tickers:
        print("Stock alert loop skipped because the watchlist is empty.")
        return

    print(f"Running stock alert check for {len(tickers)} ticker(s).")

    for ticker in tickers:
        try:
            analysis = await asyncio.to_thread(analyze_stock_price_only, ticker)
            if analysis is None:
                print(f"Skipping {ticker}; no valid stock data was returned.")
                continue

            await process_stock_alerts(guild, analysis)
            await process_levels_alerts(guild, analysis)
        except Exception as error:
            print(f"Stock alert check failed for {ticker}: {error}")


@tasks.loop(minutes=5)
async def eod_summary_loop():
    global last_eod_summary_date

    try:
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
    except Exception as error:
        print(f"EOD summary loop failed safely: {error}")


@tasks.loop(minutes=5)
async def morning_briefing_loop():
    global last_morning_briefing_date

    try:
        if not should_run_morning_briefing():
            return

        guild_id = get_guild_id()
        if guild_id is None:
            return

        guild = bot.get_guild(guild_id)
        if guild is None:
            print("Morning briefing loop could not find the configured Discord server.")
            return

        channel = get_channel_by_name(guild, MORNING_BRIEFING_CHANNEL) or get_channel_by_name(guild, MARKET_BRIEFING_CHANNEL)
        if channel is None:
            print("Morning briefing loop could not find #morning-briefing or #market-briefing.")
            return

        summary = await asyncio.to_thread(build_morning_briefing)
        await send_long_message(channel, summary)
        last_morning_briefing_date = market_now().date().isoformat()
        print(f"Morning briefing sent for {last_morning_briefing_date}.")
    except Exception as error:
        print(f"Morning briefing loop failed safely: {error}")


@tasks.loop(minutes=10)
async def best_setups_loop():
    global last_best_setups_date
    try:
        if not should_run_best_setups():
            return
        guild_id = get_guild_id()
        guild = bot.get_guild(guild_id) if guild_id else None
        if guild is None:
            return
        channel = get_channel_by_name(guild, "best-setups")
        if channel is None:
            print("Best setups loop could not find #best-setups.")
            return
        results = await asyncio.to_thread(build_best_setups_of_day)
        await send_long_message(channel, format_best_setups_message(results))
        last_best_setups_date = market_now().date().isoformat()
        print(f"Best setups posted for {last_best_setups_date}.")
    except Exception as error:
        print(f"Best setups loop failed safely: {error}")


@tasks.loop(minutes=10)
async def do_not_chase_loop():
    global last_do_not_chase_date
    try:
        if not should_run_do_not_chase():
            return
        guild_id = get_guild_id()
        guild = bot.get_guild(guild_id) if guild_id else None
        if guild is None:
            return
        channel = get_channel_by_name(guild, "do-not-chase")
        if channel is None:
            print("Do-not-chase loop could not find #do-not-chase.")
            return
        results = await asyncio.to_thread(build_do_not_chase_results)
        await send_long_message(channel, format_do_not_chase_message(results))
        last_do_not_chase_date = market_now().date().isoformat()
        print(f"Do-not-chase watch posted for {last_do_not_chase_date}.")
    except Exception as error:
        print(f"Do-not-chase loop failed safely: {error}")


@tasks.loop(hours=6)
async def signal_performance_loop():
    global last_signal_performance_summary_date
    try:
        await asyncio.to_thread(update_signal_performance)
        today = market_now().date().isoformat()
        if last_signal_performance_summary_date == today:
            return
        guild_id = get_guild_id()
        guild = bot.get_guild(guild_id) if guild_id else None
        channel = get_channel_by_name(guild, "signal-performance") if guild else None
        if channel is None:
            return
        message = await asyncio.to_thread(format_signal_performance_summary, 30)
        if "No logged signals" not in message:
            await send_long_message(channel, message)
            last_signal_performance_summary_date = today
    except Exception as error:
        print(f"Signal performance loop failed safely: {error}")


@tasks.loop(minutes=10)
async def earnings_weekly_loop():
    global last_earnings_weekly_summary_date

    if not should_run_earnings_weekly_summary():
        return

    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("Earnings weekly loop could not find the configured Discord server.")
        return

    watchlist_channel = earnings_channel(guild, WATCHLIST_EARNINGS_CHANNEL)
    promising_channel = earnings_channel(guild, PROMISING_EARNINGS_CHANNEL)

    watchlist_summary = None
    promising_summary = None

    try:
        watchlist_summary = await asyncio.to_thread(build_watchlist_earnings_summary)
    except Exception as error:
        print(f"Watchlist earnings summary failed: {error}")

    try:
        promising_summary = await asyncio.to_thread(build_promising_earnings_summary)
    except Exception as error:
        print(f"Promising earnings summary failed: {error}")

    try:
        if watchlist_channel and watchlist_summary:
            await send_long_message(watchlist_channel, watchlist_summary)
        if promising_channel and promising_summary:
            await send_long_message(promising_channel, promising_summary)

        last_earnings_weekly_summary_date = earnings_now().date().isoformat()
        print(f"Earnings weekly summaries sent for {last_earnings_weekly_summary_date}.")
    except Exception as error:
        print(f"Earnings weekly loop failed safely while sending: {error}")


@tasks.loop(hours=24)
async def earnings_alert_loop():
    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("Earnings alert loop could not find the configured Discord server.")
        return

    channel = earnings_channel(guild, EARNINGS_ALERTS_CHANNEL)
    if channel is None:
        return

    today_text = earnings_now().date().isoformat()
    try:
        alert_rows = await asyncio.to_thread(watchlist_earnings_alerts)
    except Exception as error:
        print(f"Earnings alert loop failed safely: {error}")
        return

    for ticker, earnings_date, days_until in alert_rows:
        alert_key = f"{ticker}:{earnings_date}"
        if last_earnings_alert_dates.get(alert_key) == today_text:
            continue

        if days_until == 0:
            timing = "today"
        elif days_until == 1:
            timing = "tomorrow"
        else:
            timing = f"within {days_until} days"

        await channel.send(
            f"{get_signal_emoji('Earnings')} Earnings Alert — {ticker}\n"
            f"{ticker} has an upcoming quarterly report {timing}.\n"
            f"{EMOJI_EARNINGS} Earnings Date: {earnings_date}\n\n"
            f"{EARNINGS_DISCLAIMER}"
        )
        last_earnings_alert_dates[alert_key] = today_text


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
        try:
            await send_long_message(mentions_channel, format_wsb_mentions(mentions))
        except Exception as error:
            print(f"WSB mentions send failed safely: {error}")
    else:
        print("Warning: #wsb-mentions is missing.")

    if tracking_channel:
        try:
            await send_long_message(tracking_channel, format_wsb_tracking(tracked))
        except Exception as error:
            print(f"WSB tracking send failed safely: {error}")
    else:
        print("Warning: #wsb-tracking is missing.")

    if alerts_channel:
        for item in tracked:
            reasons = wsb_alert_reasons(item)
            if reasons:
                try:
                    log_signal(
                        item.get("ticker"),
                        "wsb_attention",
                        "wsb",
                        {"latest_price": item.get("price"), "ticker": item.get("ticker"), "rsi": item.get("rsi"), "relative_volume": item.get("relative_volume"), "change_5d_percent": item.get("change_5d_percent"), "trend": item.get("trend")},
                        None,
                        "high",
                        [],
                        reasons,
                    )
                    await send_long_message(alerts_channel, format_wsb_alert(item, reasons))
                except Exception as error:
                    print(f"WSB alert send failed safely: {error}")
    else:
        print("Warning: #wsb-alerts is missing.")


@tasks.loop(minutes=PAULS_TRACKER_SCAN_INTERVAL_MINUTES)
async def pauls_tracker_loop():
    guild_id = get_guild_id()
    if guild_id is None:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        print("Paul's Trackers loop could not find the configured Discord server.")
        return

    try:
        data = await asyncio.to_thread(run_pauls_tracker_scans)
    except Exception as error:
        print(f"Paul's Trackers loop failed: {error}")
        return

    dividend_channel = get_channel_by_name(guild, DIVIDEND_HIGHS_CHANNEL)
    runners_channel = get_channel_by_name(guild, FIVE_DAY_RUNNERS_CHANNEL)

    if dividend_channel:
        try:
            await send_long_message(dividend_channel, build_dividend_highs_message(data["dividend_highs"]))
        except Exception as error:
            print(f"Paul's dividend tracker send failed safely: {error}")
    else:
        print(f"Warning: #{DIVIDEND_HIGHS_CHANNEL} is missing.")

    if runners_channel:
        try:
            await send_long_message(runners_channel, build_five_day_runners_message(data["five_day_runners"]))
        except Exception as error:
            print(f"Paul's 5-day tracker send failed safely: {error}")
    else:
        print(f"Warning: #{FIVE_DAY_RUNNERS_CHANNEL} is missing.")


async def setup_channels(guild):
    """Create and organize stock bot categories and channels."""
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

    created_categories = []
    existing_categories = []
    created_channels = []
    existing_channels = []
    moved_channels = []

    for category_name, channel_names in CHANNEL_CATEGORIES.items():
        category = discord.utils.get(guild.categories, name=category_name)

        if category:
            print(f'Category already exists: "{category_name}"')
            existing_categories.append(category)
        else:
            category = await guild.create_category(category_name)
            print(f'Created category: "{category_name}"')
            created_categories.append(category)

        for channel_name in channel_names:
            channel = discord.utils.get(guild.text_channels, name=channel_name)

            if channel:
                existing_channels.append(channel)
                if channel.category_id != category.id:
                    old_category_name = channel.category.name if channel.category else "No Category"
                    await channel.edit(category=category)
                    print(
                        f'Moved channel #{channel_name} from "{old_category_name}" '
                        f'to "{category_name}"'
                    )
                    moved_channels.append(channel)
                else:
                    print(f'Channel already exists in "{category_name}": #{channel_name}')
                continue

            channel = await guild.create_text_channel(channel_name, category=category)
            print(f'Created channel #{channel_name} in "{category_name}"')
            created_channels.append(channel)

    return {
        "created_categories": created_categories,
        "existing_categories": existing_categories,
        "created_channels": created_channels,
        "existing_channels": existing_channels,
        "moved_channels": moved_channels,
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
        await setup_channels(guild)
        await post_or_update_guide_messages(guild)
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

    if not morning_briefing_loop.is_running():
        morning_briefing_loop.start()
        print(
            f"Morning briefing loop started. Scheduled for "
            f"{MORNING_BRIEFING_HOUR:02d}:{MORNING_BRIEFING_MINUTE:02d} Pacific."
        )

    if not best_setups_loop.is_running():
        best_setups_loop.start()
        print(f"Best setups loop started. Scheduled for {BEST_SETUPS_HOUR:02d}:{BEST_SETUPS_MINUTE:02d} Pacific.")

    if not do_not_chase_loop.is_running():
        do_not_chase_loop.start()
        print("Do-not-chase loop started. Scheduled for 07:05 Pacific.")

    if not signal_performance_loop.is_running():
        signal_performance_loop.start()
        print("Signal performance loop started. Updating every 6 hours.")

    if not earnings_weekly_loop.is_running():
        earnings_weekly_loop.start()
        print(
            f"Earnings weekly loop started. Scheduled for Monday at "
            f"{EARNINGS_WEEKLY_SUMMARY_HOUR:02d}:{EARNINGS_WEEKLY_SUMMARY_MINUTE:02d} Pacific."
        )

    if not earnings_alert_loop.is_running():
        earnings_alert_loop.start()
        print("Earnings alert loop started.")

    if reddit_credentials_configured() and praw is not None and not wsb_tracker_loop.is_running():
        wsb_tracker_loop.start()
        print(f"WSB tracker loop started. Checking every {WSB_CHECK_INTERVAL_MINUTES} minutes.")
    elif not reddit_credentials_configured():
        print("Skipped optional WSB tracker because Reddit credentials are not configured.")
    elif praw is None:
        print("Skipped optional WSB tracker because PRAW is not installed.")

    if PAULS_TRACKERS_AUTO_ENABLED and not pauls_tracker_loop.is_running():
        pauls_tracker_loop.start()
        print(f"Paul's Trackers loop started. Checking every {PAULS_TRACKER_SCAN_INTERVAL_MINUTES} minutes.")
    elif not PAULS_TRACKERS_AUTO_ENABLED:
        print("Paul's Trackers automatic loop disabled by settings.")

    if ENABLE_DAILY_SCANNER and not daily_scanner_loop.is_running():
        daily_scanner_loop.start()
        print("Daily scanner loop started.")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send("Please wait before running this command again.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("That command is missing a required value. Use `!help` or `!commands` for examples.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("I could not read one of those values. Use `!help` or `!commands` for examples.")
        return
    raise error


@bot.command(name="setup")
@commands.guild_only()
async def setup_command(ctx):
    """Manually run the stock bot channel setup."""
    try:
        result = await setup_channels(ctx.guild)
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

    await post_or_update_guide_messages(ctx.guild)
    await ctx.send(
        "Server organization complete. Stock bot channels are now grouped by Core, "
        "Watchlist, Scanner, Journal, Briefings, Quarterly Reports, WallStreetBets, "
        "Paul's Trackers, Paper Trading, and Guide."
    )


@bot.command(name="channels")
@commands.guild_only()
async def channels_command(ctx):
    """List the stock bot channels."""
    lines = ["Stock Bot Channels"]
    found_any = False

    for category_name, channel_names in CHANNEL_CATEGORIES.items():
        lines.extend(["", category_name])
        for channel_name in channel_names:
            channel = get_channel_by_name(ctx.guild, channel_name)
            if channel:
                lines.append(channel.mention)
                found_any = True
            else:
                lines.append(f"#{channel_name} (missing)")

    if not found_any:
        await ctx.send("No stock bot channels were found. Run `!setup` first.")
        return

    await send_long_message(ctx, "\n".join(lines))


@bot.command(name="postguides")
@commands.guild_only()
async def postguides_command(ctx):
    """Manually post or update automatic guide messages."""
    await post_or_update_guide_messages(ctx.guild)
    await ctx.send("Guide messages updated in the Stock Bot — Guide section.")


@bot.command(name="guide")
@commands.guild_only()
async def guide_command(ctx):
    """Show guide channels."""
    lines = ["Stock Bot Guide:"]
    for channel_name in GUIDE_CHANNELS:
        channel = get_channel_by_name(ctx.guild, channel_name)
        lines.append(channel.mention if channel else f"#{channel_name} (missing)")
    await ctx.send("\n".join(lines))


@bot.command(name="ping")
async def ping_command(ctx):
    """Confirm that the bot is online."""
    await ctx.send("Stock bot is online.")


def command_help_text():
    return (
        "Stock bot commands:\n"
        "Channels are organized by Core, Watchlist, Scanner, Journal, Briefings, Quarterly Reports, Paul's Trackers, Paper Trading, and WallStreetBets.\n"
        "!setup - Create the stock bot channel structure\n"
        "!channels - List stock bot channels\n"
        "!guide - Show guide channels\n"
        "!postguides - Repost/update automatic guide messages\n"
        "!quickstart - Show first steps for using the bot\n"
        "!examples - Show example commands\n"
        "!ping - Check whether the bot is online\n"
        "!add AAPL - Add a ticker to the watchlist\n"
        "!remove AAPL - Remove a ticker from the watchlist\n"
        "!watchlist - Show tracked tickers\n"
        "!check AAPL - Check one ticker\n"
        "!alerts - Show alert loop status\n"
        "!settings - Show persistent bot settings\n"
        "!quietmode on/off - Suppress non-critical alerts\n"
        "!markethoursonly on/off - Restrict stock alerts to regular market hours\n"
        "!alertfrequency 5 - Save alert loop frequency\n"
        "!setalertseverity high - Set minimum alert severity\n"
        "Best Setups:\n"
        "!bestsetups - Show best research candidates of the day\n"
        "!bestsetupsstatus - Show best setups loop status\n"
        "!setbestsetupstime 7 0 - Set best setups schedule\n"
        "!setbestsetupscore 7 - Set minimum best setups score\n"
        "!setbestsetuplimit 10 - Set best setups result limit\n"
        "Do Not Chase:\n"
        "!donotchase - Show extended setups to be cautious with\n"
        "!donotchasestatus - Show do-not-chase thresholds\n"
        "!setchaseRSI 75 - Set do-not-chase RSI threshold\n"
        "!setchase5day 15 - Set do-not-chase 5-day gain threshold\n"
        "!setchase20ma 7 - Set do-not-chase 20MA distance threshold\n"
        "Signal Performance:\n"
        "!signals - Show recent logged signals\n"
        "!signalperformance 30 - Show historical signal tracking\n"
        "!signalreview AAPL - Review one ticker's signals\n"
        "!clearsignals CONFIRM - Clear signal tracking history\n"
        "Support/Resistance:\n"
        "!levels AAPL - Show support/resistance levels\n"
        "!watchlevels - Show levels for the watchlist\n"
        "!setleveldistance 1 - Set levels alert distance\n"
        "Options:\n"
        "!options AAPL - Show options overview\n"
        "!unusualoptions AAPL - Scan unusual options activity\n"
        "!optionshelp - Explain options fields\n"
        "!optionssettings - Show options filters\n"
        "!setoptionsvolume 10 - Set options minimum volume\n"
        "!setoptionsoi 100 - Set options minimum open interest\n"
        "!setoptionsspread 20 - Set options max spread\n"
        "!setoptionsdte 14 60 - Set options DTE range\n"
        "Watchlist Groups:\n"
        "!groups - Show watchlist groups\n"
        "!groupcreate semiconductors - Create a group\n"
        "!groupdelete semiconductors CONFIRM - Delete a group\n"
        "!groupadd semiconductors NVDA - Add ticker to group\n"
        "!groupremove semiconductors NVDA - Remove ticker from group\n"
        "!groupshow semiconductors - Show group tickers\n"
        "!groupcheck semiconductors - Check all tickers in a group\n"
        "!groupscan semiconductors momentum - Scan a group\n"
        "!groupalerts semiconductors on/off - Toggle group background alerts\n"
        "!scan momentum - Run a preset scanner\n"
        "!scan custom rsi>70 - Run a custom scanner\n"
        "!scan all momentum - Run a broad scanner\n"
        "!scanhelp - Show custom scan filters\n"
        "!eodsummary - Manually post the end-of-day watchlist summary\n"
        "!eodstatus - Show EOD summary schedule/status\n"
        "!seteodtime 13 30 - Set EOD summary time\n"
        "Morning Briefing:\n"
        "!morningbriefing - Manually post the morning briefing\n"
        "!morningstatus - Show morning briefing schedule/status\n"
        "!setmorningtime 6 30 - Set morning briefing time\n"
        "Quarterly Reports:\n"
        "!earnings - Show upcoming earnings for watchlist stocks\n"
        "!promisingearnings - Show analyst-supported earnings candidates\n"
        "!earningsstatus - Show earnings tracker status\n"
        "!earningssettings - Show earnings filter settings\n"
        "!setearningslookahead 30 - Change earnings lookahead window\n"
        "!earningsscanlimit 50 - Change promising earnings scan size\n"
        "!clearearningscache - Clear cached earnings dates\n"
        "Paul's Trackers:\n"
        "!paulstrackers - Run both Paul's Tracker scans\n"
        "!dividendhighs - Find stocks near all-time highs with at least 5% dividend yield\n"
        "!fivedayrunners - Find stocks up at least 10% over the past 5 trading days\n"
        "!paulssettings - Show Paul's Tracker settings\n"
        "!paulsauto on/off - Turn automatic Paul's Trackers loop on or off\n"
        "!setpaulslimit 50 - Set Paul's Tracker scan limit\n"
        "!setpaulsdividend 5 - Set dividend yield threshold\n"
        "!setpaulsath 5 - Set near all-time-high threshold\n"
        "!setpauls5day 10 - Set 5-day gain threshold\n"
        "!wsbstatus - Show WallStreetBets tracker status\n"
        "!wsbmentions - Show current WSB ticker mentions\n"
        "!wsbtrack - Show WSB-tracked tickers with market measurements\n"
        "!wsbrefresh - Manually refresh WSB mentions and tracking\n"
        "!wsbsettings - Show WSB tracker settings\n"
        "!wsbclear - Clear WSB tracker JSON files\n"
        "Paper Trading:\n"
        "!paperbuy AAPL 10 195.20 reason - Add simulated buy\n"
        "!papersell AAPL 10 205.00 reason - Add simulated sell\n"
        "!paperportfolio - Show simulated open positions\n"
        "!paperpnl - Show simulated P/L\n"
        "!paperjournal - Show recent simulated trades\n"
        "!paperclose AAPL 210.00 reason - Close a simulated position\n"
        "!paperclear CONFIRM - Clear simulated trades\n\n"
        "Signal/Risk: Signal scores rate possible setups from 0-10 for research. Risk flags highlight caution items such as earnings, RSI extremes, low volume, or 200MA weakness.\n\n"
        "Emoji Legend:\n"
        f"{EMOJI_UP} {EMOJI_ARROW_UP} Up / green day\n"
        f"{EMOJI_DOWN} {EMOJI_ARROW_DOWN} Down / red day\n"
        f"{EMOJI_FLAT} {EMOJI_NEUTRAL} Flat / neutral\n"
        f"{EMOJI_VOLUME} Unusual volume or activity\n"
        f"{EMOJI_WARNING} Caution / overbought / risk\n"
        f"{EMOJI_OVERSOLD} Oversold\n"
        f"{EMOJI_BREAKOUT} Near breakout or 52-week high\n"
        f"{EMOJI_EARNINGS} Earnings / quarterly report\n"
        "🚨 Critical alert"
    )


@bot.command(name="help")
async def help_command(ctx):
    """Show bot commands."""
    await ctx.send(command_help_text())


@bot.command(name="commands")
async def commands_command(ctx):
    """Show bot commands."""
    await ctx.send(command_help_text())


@bot.command(name="quickstart")
async def quickstart_command(ctx):
    """Show first steps for using the bot."""
    await ctx.send(
        "Quickstart:\n"
        "1. Run `!setup` first to organize all stock bot channels.\n"
        "2. Add tracked stocks with `!add AAPL`.\n"
        "3. View tracked stocks with `!watchlist`.\n"
        "4. Check a ticker with `!check AAPL`.\n"
        "5. Run scanners with `!scan momentum` or `!scan custom rsi>70`.\n"
        "6. Check upcoming quarterly reports with `!earnings`.\n"
        "7. Review analyst-supported earnings candidates with `!promisingearnings`.\n"
        "8. Run Paul's Trackers with `!paulstrackers`.\n"
        "9. Review settings with `!settings`.\n"
        "10. Track simulated trades with `!paperbuy AAPL 10 195.20 reason`.\n"
        "11. Review levels with `!levels AAPL`.\n"
        "12. Review groups with `!groups`.\n"
        "13. Use `!help` or `!commands` anytime."
    )


@bot.command(name="examples")
async def examples_command(ctx):
    """Show example bot commands."""
    await ctx.send(
        "Example commands:\n"
        "`!setup`\n"
        "`!add AAPL`\n"
        "`!remove TSLA`\n"
        "`!check NVDA`\n"
        "`!scan momentum`\n"
        "`!scan custom rsi=45-65 above50ma=true`\n"
        "`!eodsummary`\n"
        "`!morningbriefing`\n"
        "`!settings`\n"
        "`!levels AAPL`\n"
        "`!bestsetups`\n"
        "`!donotchase`\n"
        "`!options AAPL`\n"
        "`!groups`\n"
        "`!groupadd ai NVDA`\n"
        "`!paperbuy AAPL 10 195.20 breakout setup`\n"
        "`!paperportfolio`\n"
        "`!paulstrackers`\n"
        "`!wsbstatus`"
    )


@bot.command(name="about")
async def about_command(ctx):
    """Show a short description of the bot."""
    await ctx.send(
        "This is one Discord stock research bot with watchlists, scanners, custom scans, "
        "morning briefings, post-market recaps, paper trade journaling, signal scores, "
        "risk flags, and WallStreetBets mention tracking.\n\n"
        "It also includes a quarterly report tracker for watchlist earnings and "
        "analyst-supported research candidates, plus Paul's Trackers for dividend highs "
        "and 5-day runners. The bot does not place trades.\n\n"
        "Emoji Legend:\n"
        f"{EMOJI_UP} {EMOJI_ARROW_UP} Up / green day\n"
        f"{EMOJI_DOWN} {EMOJI_ARROW_DOWN} Down / red day\n"
        f"{EMOJI_FLAT} {EMOJI_NEUTRAL} Flat / neutral\n"
        f"{EMOJI_VOLUME} Unusual volume or activity\n"
        f"{EMOJI_WARNING} Caution / overbought / risk\n"
        f"{EMOJI_OVERSOLD} Oversold\n"
        f"{EMOJI_BREAKOUT} Near breakout or 52-week high\n"
        f"{EMOJI_EARNINGS} Earnings / quarterly report\n"
        "🚨 Critical alert\n\n"
        f"{SCANNER_DISCLAIMER}\n{EARNINGS_DISCLAIMER}\n{PAULS_TRACKER_DISCLAIMER}\n{WSB_DISCLAIMER}\n{PAPER_TRADING_DISCLAIMER}"
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
    analysis = await asyncio.to_thread(analyze_stock_price_only, ticker)

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
        f"Check interval: {ALERT_INTERVAL_MINUTES} minutes\n"
        f"Quiet mode: {'on' if QUIET_MODE else 'off'}\n"
        f"Market-hours-only: {'on' if MARKET_HOURS_ONLY else 'off'}\n"
        f"Minimum alert severity: {MIN_ALERT_SEVERITY}"
    )


@bot.command(name="settings")
@commands.guild_only()
async def settings_command(ctx):
    """Show persistent bot settings."""
    settings = load_bot_settings()
    await ctx.send(
        "Bot settings:\n"
        f"EOD summary time: {settings.get('eod_summary_time')} Pacific\n"
        f"Morning briefing time: {settings.get('morning_briefing_time')} Pacific\n"
        f"Earnings lookahead: {settings.get('earnings_lookahead_days')} days\n"
        f"Broad scan limit: {settings.get('broad_scan_limit')} tickers\n"
        f"Quiet mode: {settings.get('quiet_mode')}\n"
        f"Market-hours-only mode: {settings.get('market_hours_only')}\n"
        f"Alert frequency: {settings.get('alert_frequency_minutes')} minutes\n"
        f"Minimum alert severity: {settings.get('min_alert_severity')}\n"
        f"Paul's dividend minimum: {settings.get('pauls_dividend_min_percent')}%\n"
        f"Paul's near-ATH threshold: {settings.get('pauls_ath_threshold_percent')}%\n"
        f"Paul's 5-day gain threshold: {settings.get('pauls_5day_gain_percent')}%\n"
        f"Paul's Trackers auto enabled: {settings.get('pauls_trackers_auto_enabled')}\n"
        f"Paul's Tracker scan limit: {settings.get('pauls_tracker_scan_limit')}\n"
        f"Best setups time: {settings.get('best_setups_time')}\n"
        f"Best setups min score: {settings.get('best_setups_min_score')}\n"
        f"Levels alert distance: {settings.get('levels_alert_distance_percent')}%\n"
        f"Options filters: volume {settings.get('options_min_volume')}, OI {settings.get('options_min_open_interest')}, spread {settings.get('options_max_spread_percent')}%"
    )


@bot.command(name="resetsettings")
@commands.guild_only()
async def resetsettings_command(ctx):
    """Reset persistent settings to defaults."""
    reset_bot_settings()
    await ctx.send("Bot settings reset to defaults. Restart the bot to reload all loop intervals cleanly.")


@bot.command(name="quietmode")
@commands.guild_only()
async def quietmode_command(ctx, mode=None):
    """Turn quiet mode on or off."""
    global QUIET_MODE
    if mode not in {"on", "off"}:
        await ctx.send("Use `!quietmode on` or `!quietmode off`.")
        return
    QUIET_MODE = mode == "on"
    set_setting("quiet_mode", QUIET_MODE)
    await ctx.send(f"Quiet mode is now {'on' if QUIET_MODE else 'off'}.")


@bot.command(name="markethoursonly")
@commands.guild_only()
async def markethoursonly_command(ctx, mode=None):
    """Turn market-hours-only alerts on or off."""
    global MARKET_HOURS_ONLY
    if mode not in {"on", "off"}:
        await ctx.send("Use `!markethoursonly on` or `!markethoursonly off`.")
        return
    MARKET_HOURS_ONLY = mode == "on"
    set_setting("market_hours_only", MARKET_HOURS_ONLY)
    await ctx.send(f"Market-hours-only alerts are now {'on' if MARKET_HOURS_ONLY else 'off'}.")


@bot.command(name="alertfrequency")
@commands.guild_only()
async def alertfrequency_command(ctx, minutes=None):
    """Set alert frequency for future bot sessions."""
    global ALERT_INTERVAL_MINUTES
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        await ctx.send("Please provide a whole number from 1 to 60, like `!alertfrequency 5`.")
        return
    if value < 1 or value > 60:
        await ctx.send("Alert frequency must be between 1 and 60 minutes.")
        return
    ALERT_INTERVAL_MINUTES = value
    set_setting("alert_frequency_minutes", value)
    await ctx.send("Alert frequency saved. Restart the bot for the background loop interval to fully reload.")


@bot.command(name="setalertseverity")
@commands.guild_only()
async def setalertseverity_command(ctx, severity=None):
    """Set the minimum alert severity."""
    global MIN_ALERT_SEVERITY
    if severity not in {"low", "medium", "high", "critical"}:
        await ctx.send("Use one of: `low`, `medium`, `high`, or `critical`.")
        return
    MIN_ALERT_SEVERITY = severity
    set_setting("min_alert_severity", severity)
    await ctx.send(f"Minimum alert severity set to {severity} and saved.")


@bot.command(name="morningbriefing")
@commands.guild_only()
async def morningbriefing_command(ctx):
    """Manually generate the morning briefing."""
    await ctx.send("Building the morning briefing. This may take a moment...")
    summary = await asyncio.to_thread(build_morning_briefing)
    await send_long_message(ctx, summary)


@bot.command(name="morningstatus")
@commands.guild_only()
async def morningstatus_command(ctx):
    """Show morning briefing status."""
    status = "running" if morning_briefing_loop.is_running() else "stopped"
    await ctx.send(
        "Morning briefing status:\n"
        f"Loop: {status}\n"
        f"Scheduled time: {MORNING_BRIEFING_HOUR:02d}:{MORNING_BRIEFING_MINUTE:02d} Pacific\n"
        f"Target channel: #{MORNING_BRIEFING_CHANNEL}\n"
        f"Last run date: {last_morning_briefing_date or 'Never'}"
    )


@bot.command(name="setmorningtime")
@commands.guild_only()
async def setmorningtime_command(ctx, hour=None, minute=None):
    """Set the morning briefing time."""
    global MORNING_BRIEFING_HOUR, MORNING_BRIEFING_MINUTE
    try:
        new_hour = int(hour)
        new_minute = int(minute)
    except (TypeError, ValueError):
        await ctx.send("Please provide a valid Pacific time, like `!setmorningtime 6 30`.")
        return
    if not 0 <= new_hour <= 23 or not 0 <= new_minute <= 59:
        await ctx.send("Hour must be 0-23 and minute must be 0-59.")
        return
    MORNING_BRIEFING_HOUR = new_hour
    MORNING_BRIEFING_MINUTE = new_minute
    set_setting("morning_briefing_time", f"{new_hour:02d}:{new_minute:02d}")
    await ctx.send(f"Morning briefing time set to {new_hour:02d}:{new_minute:02d} Pacific and saved.")


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
    set_setting("eod_summary_time", f"{new_hour:02d}:{new_minute:02d}")
    await ctx.send(
        f"EOD summary time set to {EOD_SUMMARY_HOUR:02d}:{EOD_SUMMARY_MINUTE:02d} Pacific "
        "and saved to bot_settings.json."
    )


@bot.command(name="earnings")
@commands.guild_only()
async def earnings_command(ctx):
    """Show upcoming quarterly reports for watchlist stocks."""
    await ctx.send("Building watchlist quarterly reports summary. This may take a moment...")
    summary = await asyncio.to_thread(build_watchlist_earnings_summary)
    await send_long_message(ctx, summary)


@bot.command(name="earningswatch")
@commands.guild_only()
async def earningswatch_command(ctx):
    """Show upcoming quarterly reports for watchlist stocks."""
    await ctx.send("Building watchlist quarterly reports summary. This may take a moment...")
    summary = await asyncio.to_thread(build_watchlist_earnings_summary)
    await send_long_message(ctx, summary)


@bot.command(name="promisingearnings")
@commands.guild_only()
async def promisingearnings_command(ctx):
    """Show analyst-supported earnings research candidates."""
    await ctx.send("Building analyst-supported earnings watch. This may take a few minutes...")
    summary = await asyncio.to_thread(build_promising_earnings_summary)
    await send_long_message(ctx, summary)


@bot.command(name="earningsstatus")
@commands.guild_only()
async def earningsstatus_command(ctx):
    """Show earnings tracker status."""
    tickers = load_watchlist()
    weekly_status = "running" if earnings_weekly_loop.is_running() else "stopped"
    alert_status = "running" if earnings_alert_loop.is_running() else "stopped"
    await ctx.send(
        "Quarterly reports tracker status:\n"
        f"Weekly loop: {weekly_status}\n"
        f"Daily alert loop: {alert_status}\n"
        f"Weekly schedule: Monday {EARNINGS_WEEKLY_SUMMARY_HOUR:02d}:{EARNINGS_WEEKLY_SUMMARY_MINUTE:02d} Pacific\n"
        f"Lookahead days: {EARNINGS_LOOKAHEAD_DAYS}\n"
        f"Target channels: #{WATCHLIST_EARNINGS_CHANNEL}, #{PROMISING_EARNINGS_CHANNEL}, #{EARNINGS_ALERTS_CHANNEL}\n"
        f"Watchlist tickers: {len(tickers)}\n\n"
        f"{EARNINGS_DISCLAIMER}"
    )


@bot.command(name="earningssettings")
@commands.guild_only()
async def earningssettings_command(ctx):
    """Show earnings tracker settings."""
    await ctx.send(
        "Quarterly reports settings:\n"
        f"Lookahead days: {EARNINGS_LOOKAHEAD_DAYS}\n"
        f"Promising earnings scan limit: {MAX_PROMISING_EARNINGS_SCAN_TICKERS}\n"
        f"Cache file: {EARNINGS_CACHE_FILE}\n"
        f"Cache max age: {EARNINGS_CACHE_MAX_AGE_HOURS} hours\n"
        f"Rate-limit retry delay: {EARNINGS_RATE_LIMIT_RETRY_HOURS} hours\n"
        f"Analyst count minimum: {PROMISING_MIN_ANALYST_COUNT}\n"
        f"Target upside minimum: {PROMISING_MIN_TARGET_UPSIDE_PERCENT}%\n"
        f"Max recommendation mean: {PROMISING_MAX_RECOMMENDATION_MEAN}\n"
        f"Max RSI: {PROMISING_MAX_RSI}\n"
        f"Minimum price: {format_money(PROMISING_MIN_PRICE)}\n\n"
        f"{EARNINGS_DISCLAIMER}"
    )


@bot.command(name="setearningslookahead")
@commands.guild_only()
async def setearningslookahead_command(ctx, days=None):
    """Set earnings lookahead days for this bot session."""
    global EARNINGS_LOOKAHEAD_DAYS

    try:
        new_days = int(days)
    except (TypeError, ValueError):
        await ctx.send("Please provide a whole number from 1 to 90, like `!setearningslookahead 30`.")
        return

    if new_days < 1 or new_days > 90:
        await ctx.send("Earnings lookahead days must be between 1 and 90.")
        return

    EARNINGS_LOOKAHEAD_DAYS = new_days
    set_setting("earnings_lookahead_days", new_days)
    await ctx.send(
        f"Earnings lookahead window set to {EARNINGS_LOOKAHEAD_DAYS} days and saved.\n\n"
        f"{EARNINGS_DISCLAIMER}"
    )


@bot.command(name="earningsscanlimit")
@commands.guild_only()
async def earningsscanlimit_command(ctx, number=None):
    """Set promising earnings scan limit for this bot session."""
    global MAX_PROMISING_EARNINGS_SCAN_TICKERS

    try:
        new_limit = int(number)
    except (TypeError, ValueError):
        await ctx.send("Please provide a whole number from 5 to 200, like `!earningsscanlimit 50`.")
        return

    if new_limit < 5 or new_limit > 200:
        await ctx.send("Promising earnings scan limit must be between 5 and 200.")
        return

    MAX_PROMISING_EARNINGS_SCAN_TICKERS = new_limit
    set_setting("earnings_scan_limit", new_limit)
    await ctx.send(f"Promising earnings scan limit set to {MAX_PROMISING_EARNINGS_SCAN_TICKERS} tickers.")


@bot.command(name="clearearningscache")
@commands.guild_only()
async def clearearningscache_command(ctx):
    """Clear the earnings date cache."""
    clear_earnings_cache()
    earnings_calendar_failures.clear()
    await ctx.send("Earnings cache cleared.")


@bot.command(name="clear")
@commands.guild_only()
async def clear_command(ctx, target=None):
    """Clear supported bot caches."""
    if target == "earningscache":
        clear_earnings_cache()
        earnings_calendar_failures.clear()
        await ctx.send("Earnings cache cleared.")
        return

    await ctx.send("Unknown clear target. Try `!clear earningscache`.")


@bot.command(name="paulstrackers")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def paulstrackers_command(ctx):
    """Run both Paul's Tracker scans."""
    await ctx.send("Running Paul's Trackers. This may take a few minutes...")
    data = await asyncio.to_thread(run_pauls_tracker_scans)
    await send_long_message(ctx, build_dividend_highs_message(data["dividend_highs"]))
    await send_long_message(ctx, build_five_day_runners_message(data["five_day_runners"]))


@bot.command(name="dividendhighs")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def dividendhighs_command(ctx):
    """Run Paul's dividend highs tracker."""
    await ctx.send("Running Paul's dividend highs tracker. This may take a few minutes...")
    tickers, _source = get_pauls_tracker_universe()
    results = await asyncio.to_thread(scan_dividend_highs, tickers)
    await send_long_message(ctx, build_dividend_highs_message(results))


@bot.command(name="fivedayrunners")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def fivedayrunners_command(ctx):
    """Run Paul's 5-day runners tracker."""
    await ctx.send("Running Paul's 5-day runners tracker. This may take a few minutes...")
    tickers, _source = get_pauls_tracker_universe()
    results = await asyncio.to_thread(scan_five_day_runners, tickers)
    await send_long_message(ctx, build_five_day_runners_message(results))


async def pauls_tracker_cooldown_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send("Please wait before running another Paul's Tracker scan.")
        return
    raise error


paulstrackers_command.error(pauls_tracker_cooldown_error)
dividendhighs_command.error(pauls_tracker_cooldown_error)
fivedayrunners_command.error(pauls_tracker_cooldown_error)


@bot.command(name="paulssettings")
@commands.guild_only()
async def paulssettings_command(ctx):
    """Show Paul's Tracker settings."""
    _tickers, source = get_pauls_tracker_universe()
    await ctx.send(
        "Paul's Tracker settings:\n"
        f"Dividend yield minimum: {DIVIDEND_YIELD_MIN_PERCENT}%\n"
        f"Near all-time-high threshold: {NEAR_ALL_TIME_HIGH_PERCENT}%\n"
        f"Five-day gain minimum: {FIVE_DAY_GAIN_MIN_PERCENT}%\n"
        f"Scan interval: {PAULS_TRACKER_SCAN_INTERVAL_MINUTES} minutes\n"
        f"Result limit: {PAULS_TRACKER_RESULT_LIMIT}\n"
        f"Automatic loop enabled: {PAULS_TRACKERS_AUTO_ENABLED}\n"
        f"Scan limit: {PAULS_TRACKER_SCAN_LIMIT}\n"
        f"Ticker universe source: {source}\n\n"
        f"{PAULS_TRACKER_DISCLAIMER}"
    )


@bot.command(name="paulsauto")
@commands.guild_only()
async def paulsauto_command(ctx, mode=None):
    """Turn automatic Paul's Trackers loop on or off."""
    global PAULS_TRACKERS_AUTO_ENABLED
    if mode not in {"on", "off"}:
        await ctx.send("Use `!paulsauto on` or `!paulsauto off`.")
        return

    PAULS_TRACKERS_AUTO_ENABLED = mode == "on"
    set_setting("pauls_trackers_auto_enabled", PAULS_TRACKERS_AUTO_ENABLED)

    if PAULS_TRACKERS_AUTO_ENABLED and not pauls_tracker_loop.is_running():
        pauls_tracker_loop.start()
    elif not PAULS_TRACKERS_AUTO_ENABLED and pauls_tracker_loop.is_running():
        pauls_tracker_loop.cancel()

    await ctx.send(
        f"Paul's Trackers automatic loop is now {'on' if PAULS_TRACKERS_AUTO_ENABLED else 'off'}.\n\n"
        f"{PAULS_TRACKER_DISCLAIMER}"
    )


@bot.command(name="setpaulslimit")
@commands.guild_only()
async def setpaulslimit_command(ctx, number=None):
    """Set Paul's Tracker scan limit."""
    global PAULS_TRACKER_SCAN_LIMIT
    try:
        value = int(number)
    except (TypeError, ValueError):
        await ctx.send("Please provide a whole number from 10 to 500, like `!setpaulslimit 50`.")
        return
    if value < 10 or value > 500:
        await ctx.send("Paul's Tracker scan limit must be between 10 and 500.")
        return
    PAULS_TRACKER_SCAN_LIMIT = value
    set_setting("pauls_tracker_scan_limit", value)
    await ctx.send(f"Paul's Tracker scan limit set to {PAULS_TRACKER_SCAN_LIMIT} tickers and saved.")


@bot.command(name="setpaulsdividend")
@commands.guild_only()
async def setpaulsdividend_command(ctx, percent=None):
    """Set Paul's dividend yield threshold for this session."""
    global DIVIDEND_YIELD_MIN_PERCENT
    try:
        value = float(percent)
    except (TypeError, ValueError):
        await ctx.send("Please provide a percent from 0 to 25, like `!setpaulsdividend 5`.")
        return
    if value < 0 or value > 25:
        await ctx.send("Dividend yield threshold must be between 0 and 25.")
        return
    DIVIDEND_YIELD_MIN_PERCENT = value
    set_setting("pauls_dividend_min_percent", value)
    await ctx.send(f"Paul's dividend yield threshold set to {DIVIDEND_YIELD_MIN_PERCENT}% and saved.")


@bot.command(name="setpaulsath")
@commands.guild_only()
async def setpaulsath_command(ctx, percent=None):
    """Set Paul's near-ATH threshold for this session."""
    global NEAR_ALL_TIME_HIGH_PERCENT
    try:
        value = float(percent)
    except (TypeError, ValueError):
        await ctx.send("Please provide a percent from 0.5 to 25, like `!setpaulsath 5`.")
        return
    if value < 0.5 or value > 25:
        await ctx.send("Near all-time-high threshold must be between 0.5 and 25.")
        return
    NEAR_ALL_TIME_HIGH_PERCENT = value
    set_setting("pauls_ath_threshold_percent", value)
    await ctx.send(f"Paul's near all-time-high threshold set to {NEAR_ALL_TIME_HIGH_PERCENT}% and saved.")


@bot.command(name="setpauls5day")
@commands.guild_only()
async def setpauls5day_command(ctx, percent=None):
    """Set Paul's 5-day gain threshold for this session."""
    global FIVE_DAY_GAIN_MIN_PERCENT
    try:
        value = float(percent)
    except (TypeError, ValueError):
        await ctx.send("Please provide a percent from 1 to 100, like `!setpauls5day 10`.")
        return
    if value < 1 or value > 100:
        await ctx.send("Five-day gain threshold must be between 1 and 100.")
        return
    FIVE_DAY_GAIN_MIN_PERCENT = value
    set_setting("pauls_5day_gain_percent", value)
    await ctx.send(f"Paul's 5-day gain threshold set to {FIVE_DAY_GAIN_MIN_PERCENT}% and saved.")


@bot.command(name="signals")
@commands.guild_only()
async def signals_command(ctx):
    """Show recent logged bot signals."""
    signals = list(reversed(load_signal_log().get("signals", [])))[:10]
    lines = ["Recent Logged Signals"]
    if not signals:
        lines.append("No signals logged yet.")
    for signal in signals:
        lines.append(
            f"- {signal.get('timestamp')} | {signal.get('ticker')} | "
            f"{signal.get('source')} / {signal.get('signal_type')} | "
            f"Price: {format_money(signal.get('price_at_signal'))} | Score: {signal.get('signal_score', 'N/A')}"
        )
    lines.extend(["", "Signal performance is historical tracking only and is not financial advice."])
    await send_long_message(ctx, "\n".join(lines))


@bot.command(name="signalperformance")
@commands.guild_only()
async def signalperformance_command(ctx, days=30):
    """Show signal performance summary."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 365))
    await send_long_message(ctx, await asyncio.to_thread(format_signal_performance_summary, days))


@bot.command(name="signalreview")
@commands.guild_only()
async def signalreview_command(ctx, ticker=None):
    """Review logged signals for one ticker."""
    ticker = normalize_ticker(ticker)
    if not ticker:
        await ctx.send("Use `!signalreview AAPL`.")
        return
    items = [item for item in load_signal_log().get("signals", []) if item.get("ticker") == ticker]
    lines = [f"Signal Review — {ticker}"]
    if not items:
        lines.append("No logged signals found for this ticker.")
    for item in list(reversed(items))[:15]:
        perf = item.get("performance", {})
        lines.append(
            f"- {item.get('timestamp')} | {item.get('source')} / {item.get('signal_type')} | "
            f"Price: {format_money(item.get('price_at_signal'))} | 1D {format_percent(perf.get('1d'))} | 5D {format_percent(perf.get('5d'))} | 10D {format_percent(perf.get('10d'))}"
        )
    lines.extend(["", "Signal performance is historical tracking only and is not financial advice."])
    await send_long_message(ctx, "\n".join(lines))


@bot.command(name="clearsignals")
@commands.guild_only()
async def clearsignals_command(ctx, confirm=None):
    if confirm != "CONFIRM":
        await ctx.send("Type `!clearsignals CONFIRM` to clear signal_log.json.")
        return
    save_signal_log({"signals": []})
    await ctx.send("Signal log cleared.")


@bot.command(name="bestsetups")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def bestsetups_command(ctx):
    await ctx.send("Building best setups of the day. This may take a moment...")
    results = await asyncio.to_thread(build_best_setups_of_day)
    await send_long_message(ctx, format_best_setups_message(results))


@bot.command(name="bestsetupsstatus")
@commands.guild_only()
async def bestsetupsstatus_command(ctx):
    status = "running" if best_setups_loop.is_running() else "stopped"
    await ctx.send(
        "Best setups status:\n"
        f"Loop: {status}\n"
        f"Schedule: {BEST_SETUPS_HOUR:02d}:{BEST_SETUPS_MINUTE:02d} Pacific\n"
        f"Last run: {last_best_setups_date or 'Never'}\n"
        f"Minimum score: {BEST_SETUPS_MIN_SCORE}\n"
        f"Result limit: {BEST_SETUPS_RESULT_LIMIT}"
    )


@bot.command(name="setbestsetupstime")
@commands.guild_only()
async def setbestsetupstime_command(ctx, hour=None, minute=None):
    global BEST_SETUPS_HOUR, BEST_SETUPS_MINUTE
    try:
        hour = int(hour)
        minute = int(minute)
    except (TypeError, ValueError):
        await ctx.send("Use `!setbestsetupstime 7 0`.")
        return
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        await ctx.send("Hour must be 0-23 and minute must be 0-59.")
        return
    BEST_SETUPS_HOUR = hour
    BEST_SETUPS_MINUTE = minute
    set_setting("best_setups_time", f"{hour:02d}:{minute:02d}")
    await ctx.send(f"Best setups time set to {hour:02d}:{minute:02d} Pacific.")


@bot.command(name="setbestsetupscore")
@commands.guild_only()
async def setbestsetupscore_command(ctx, score=None):
    global BEST_SETUPS_MIN_SCORE
    try:
        value = float(score)
    except (TypeError, ValueError):
        await ctx.send("Use `!setbestsetupscore 7`.")
        return
    if value < 0 or value > 10:
        await ctx.send("Score must be between 0 and 10.")
        return
    BEST_SETUPS_MIN_SCORE = value
    set_setting("best_setups_min_score", value)
    await ctx.send(f"Best setups minimum score set to {value}.")


@bot.command(name="setbestsetuplimit")
@commands.guild_only()
async def setbestsetuplimit_command(ctx, number=None):
    global BEST_SETUPS_RESULT_LIMIT
    try:
        value = int(number)
    except (TypeError, ValueError):
        await ctx.send("Use `!setbestsetuplimit 10`.")
        return
    if value < 3 or value > 25:
        await ctx.send("Limit must be between 3 and 25.")
        return
    BEST_SETUPS_RESULT_LIMIT = value
    set_setting("best_setups_result_limit", value)
    await ctx.send(f"Best setups result limit set to {value}.")


@bot.command(name="donotchase")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def donotchase_command(ctx):
    await ctx.send("Scanning for do-not-chase risk reminders...")
    results = await asyncio.to_thread(build_do_not_chase_results)
    await send_long_message(ctx, format_do_not_chase_message(results))


@bot.command(name="donotchasestatus")
@commands.guild_only()
async def donotchasestatus_command(ctx):
    status = "running" if do_not_chase_loop.is_running() else "stopped"
    await ctx.send(
        "Do-not-chase status:\n"
        f"Loop: {status}\n"
        f"RSI threshold: {DO_NOT_CHASE_RSI}\n"
        f"5D gain threshold: {DO_NOT_CHASE_5D_GAIN}%\n"
        f"Distance above 20MA threshold: {DO_NOT_CHASE_DISTANCE_ABOVE_20MA}%"
    )


@bot.command(name="setchasersi")
@commands.guild_only()
async def setchasersi_command(ctx, number=None):
    global DO_NOT_CHASE_RSI
    try:
        value = float(number)
    except (TypeError, ValueError):
        await ctx.send("Use `!setchaseRSI 75`.")
        return
    DO_NOT_CHASE_RSI = value
    set_setting("do_not_chase_rsi", value)
    await ctx.send(f"Do-not-chase RSI threshold set to {value}.")


@bot.command(name="setchase5day")
@commands.guild_only()
async def setchase5day_command(ctx, percent=None):
    global DO_NOT_CHASE_5D_GAIN
    try:
        value = float(percent)
    except (TypeError, ValueError):
        await ctx.send("Use `!setchase5day 15`.")
        return
    DO_NOT_CHASE_5D_GAIN = value
    set_setting("do_not_chase_5d_gain", value)
    await ctx.send(f"Do-not-chase 5D gain threshold set to {value}%.")


@bot.command(name="setchase20ma")
@commands.guild_only()
async def setchase20ma_command(ctx, percent=None):
    global DO_NOT_CHASE_DISTANCE_ABOVE_20MA
    try:
        value = float(percent)
    except (TypeError, ValueError):
        await ctx.send("Use `!setchase20ma 7`.")
        return
    DO_NOT_CHASE_DISTANCE_ABOVE_20MA = value
    set_setting("do_not_chase_distance_above_20ma", value)
    await ctx.send(f"Do-not-chase 20MA distance threshold set to {value}%.")


@bot.command(name="levels")
@commands.guild_only()
async def levels_command(ctx, ticker=None):
    ticker = normalize_ticker(ticker)
    if not ticker:
        await ctx.send("Use `!levels AAPL`.")
        return
    await send_long_message(ctx, await asyncio.to_thread(format_levels_message, ticker))


@bot.command(name="watchlevels")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def watchlevels_command(ctx):
    lines = ["Watchlist Support / Resistance"]
    for ticker in load_watchlist():
        stock_data = await asyncio.to_thread(analyze_stock_price_only, ticker)
        if not stock_data:
            continue
        levels = await asyncio.to_thread(calculate_support_resistance, ticker, stock_data)
        lines.extend([
            "",
            f"{ticker}",
            f"Price: {format_money(stock_data.get('latest_price'))}",
            f"Support: {format_money(levels.get('nearest_support'))} ({format_percent(levels.get('distance_to_support_percent'))})",
            f"Resistance: {format_money(levels.get('nearest_resistance'))} ({format_percent(levels.get('distance_to_resistance_percent'))})",
        ])
    lines.extend(["", "Research only — not financial advice."])
    await send_long_message(ctx, "\n".join(lines))


@bot.command(name="setleveldistance")
@commands.guild_only()
async def setleveldistance_command(ctx, percent=None):
    global LEVELS_ALERT_DISTANCE_PERCENT
    try:
        value = float(percent)
    except (TypeError, ValueError):
        await ctx.send("Use `!setleveldistance 1`.")
        return
    if value < 0.25 or value > 10:
        await ctx.send("Level distance must be between 0.25 and 10 percent.")
        return
    LEVELS_ALERT_DISTANCE_PERCENT = value
    set_setting("levels_alert_distance_percent", value)
    await ctx.send(f"Levels alert distance set to {value}%.")


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
    set_setting("broad_scan_limit", new_limit)
    await ctx.send(
        f"Broad scan limit set to {current_universe_scan_limit:,} tickers and saved.\n\n"
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


@bot.command(name="options")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def options_command(ctx, ticker=None, expiration=None):
    ticker = normalize_ticker(ticker)
    if not ticker:
        await ctx.send("Use `!options AAPL` or `!options AAPL 2026-06-19`.\n\nOptions data is for research only and is not financial advice.")
        return
    await ctx.send(f"Checking options data for {ticker}. This may take a moment...")
    data = await asyncio.to_thread(analyze_options_chain, ticker, expiration)
    await send_long_message(ctx, format_options_message(data))


@bot.command(name="unusualoptions")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def unusualoptions_command(ctx, ticker=None):
    ticker = normalize_ticker(ticker)
    if not ticker:
        await ctx.send("Use `!unusualoptions AAPL`.\n\nOptions data is for research only and is not financial advice.")
        return
    await ctx.send(f"Scanning unusual options activity for {ticker}.")
    await send_long_message(ctx, await asyncio.to_thread(format_unusual_options_message, ticker))


@bot.command(name="optionshelp")
@commands.guild_only()
async def optionshelp_command(ctx):
    await ctx.send(
        "Options help:\n"
        "- Strike: the price where an option can be exercised.\n"
        "- Expiration: the date the contract expires.\n"
        "- Bid/ask spread: the gap between buyer and seller prices; wide spreads can be hard to trade.\n"
        "- Open interest: contracts currently open.\n"
        "- Volume: contracts traded today.\n"
        "- Implied volatility: market-implied expected movement.\n"
        "- Break-even: approximate stock price needed to cover premium at expiration.\n"
        "- Theta risk: options lose time value as expiration approaches.\n"
        "- IV crush: implied volatility can fall after earnings.\n\n"
        "Options data is for research only and is not financial advice."
    )


@bot.command(name="optionssettings")
@commands.guild_only()
async def optionssettings_command(ctx):
    await ctx.send(
        "Options settings:\n"
        f"Minimum volume: {OPTIONS_MIN_VOLUME}\n"
        f"Minimum open interest: {OPTIONS_MIN_OPEN_INTEREST}\n"
        f"Maximum spread: {OPTIONS_MAX_SPREAD_PERCENT}%\n"
        f"DTE range: {OPTIONS_MIN_DTE}-{OPTIONS_MAX_DTE}\n\n"
        "Options data is for research only and is not financial advice."
    )


@bot.command(name="setoptionsvolume")
@commands.guild_only()
async def setoptionsvolume_command(ctx, number=None):
    global OPTIONS_MIN_VOLUME
    try:
        value = int(number)
    except (TypeError, ValueError):
        await ctx.send("Use `!setoptionsvolume 10`.")
        return
    OPTIONS_MIN_VOLUME = max(0, value)
    set_setting("options_min_volume", OPTIONS_MIN_VOLUME)
    await ctx.send(f"Options minimum volume set to {OPTIONS_MIN_VOLUME}.")


@bot.command(name="setoptionsoi")
@commands.guild_only()
async def setoptionsoi_command(ctx, number=None):
    global OPTIONS_MIN_OPEN_INTEREST
    try:
        value = int(number)
    except (TypeError, ValueError):
        await ctx.send("Use `!setoptionsoi 100`.")
        return
    OPTIONS_MIN_OPEN_INTEREST = max(0, value)
    set_setting("options_min_open_interest", OPTIONS_MIN_OPEN_INTEREST)
    await ctx.send(f"Options minimum open interest set to {OPTIONS_MIN_OPEN_INTEREST}.")


@bot.command(name="setoptionsspread")
@commands.guild_only()
async def setoptionsspread_command(ctx, percent=None):
    global OPTIONS_MAX_SPREAD_PERCENT
    try:
        value = float(percent)
    except (TypeError, ValueError):
        await ctx.send("Use `!setoptionsspread 20`.")
        return
    OPTIONS_MAX_SPREAD_PERCENT = max(1, value)
    set_setting("options_max_spread_percent", OPTIONS_MAX_SPREAD_PERCENT)
    await ctx.send(f"Options max spread set to {OPTIONS_MAX_SPREAD_PERCENT}%.")


@bot.command(name="setoptionsdte")
@commands.guild_only()
async def setoptionsdte_command(ctx, min_days=None, max_days=None):
    global OPTIONS_MIN_DTE, OPTIONS_MAX_DTE
    try:
        min_value = int(min_days)
        max_value = int(max_days)
    except (TypeError, ValueError):
        await ctx.send("Use `!setoptionsdte 14 60`.")
        return
    if min_value < 0 or max_value < min_value:
        await ctx.send("DTE range is invalid.")
        return
    OPTIONS_MIN_DTE = min_value
    OPTIONS_MAX_DTE = max_value
    set_setting("options_min_dte", min_value)
    set_setting("options_max_dte", max_value)
    await ctx.send(f"Options DTE range set to {OPTIONS_MIN_DTE}-{OPTIONS_MAX_DTE}.")


@bot.command(name="groups")
@commands.guild_only()
async def groups_command(ctx):
    data = load_watchlist_groups()
    lines = ["Watchlist Groups"]
    for name, tickers in sorted(data.get("groups", {}).items()):
        alerts = "on" if data.get("alerts_enabled", {}).get(name, False) else "off"
        lines.append(f"- {name}: {len(tickers)} ticker(s), alerts {alerts}")
    await send_long_message(ctx, "\n".join(lines))


@bot.command(name="groupcreate")
@commands.guild_only()
async def groupcreate_command(ctx, name=None):
    if not name:
        await ctx.send("Use `!groupcreate semiconductors`.")
        return
    data = load_watchlist_groups()
    if name in data["groups"]:
        await ctx.send(f"Group `{name}` already exists.")
        return
    data["groups"][name] = []
    data["alerts_enabled"][name] = False
    save_watchlist_groups(data)
    await ctx.send(f"Created group `{name}`.")


@bot.command(name="groupdelete")
@commands.guild_only()
async def groupdelete_command(ctx, name=None, confirm=None):
    data = load_watchlist_groups()
    if name == "default" and confirm != "CONFIRM_DEFAULT":
        await ctx.send("To delete default, type `!groupdelete default CONFIRM_DEFAULT`.")
        return
    if confirm != "CONFIRM" and name != "default":
        await ctx.send("Use `!groupdelete groupname CONFIRM`.")
        return
    if name not in data["groups"]:
        await ctx.send(f"Group `{name}` does not exist.")
        return
    data["groups"].pop(name, None)
    data["alerts_enabled"].pop(name, None)
    save_watchlist_groups(data)
    await ctx.send(f"Deleted group `{name}`.")


@bot.command(name="groupadd")
@commands.guild_only()
async def groupadd_command(ctx, group=None, ticker=None):
    ticker = normalize_ticker(ticker)
    data = load_watchlist_groups()
    if group not in data["groups"] or not ticker:
        await ctx.send("Use `!groupadd semiconductors NVDA`.")
        return
    data["groups"][group] = sorted(set(data["groups"][group] + [ticker]))
    if group == "default":
        save_watchlist(data["groups"][group])
    save_watchlist_groups(data)
    await ctx.send(f"Added {ticker} to `{group}`.")


@bot.command(name="groupremove")
@commands.guild_only()
async def groupremove_command(ctx, group=None, ticker=None):
    ticker = normalize_ticker(ticker)
    data = load_watchlist_groups()
    if group not in data["groups"] or not ticker:
        await ctx.send("Use `!groupremove semiconductors NVDA`.")
        return
    data["groups"][group] = [item for item in data["groups"][group] if normalize_ticker(item) != ticker]
    if group == "default":
        save_watchlist(data["groups"][group])
    save_watchlist_groups(data)
    await ctx.send(f"Removed {ticker} from `{group}`.")


@bot.command(name="groupshow")
@commands.guild_only()
async def groupshow_command(ctx, group=None):
    tickers = get_group_tickers(group)
    if not tickers:
        await ctx.send(f"Group `{group}` is empty or missing.")
        return
    await ctx.send(f"{group}:\n" + ", ".join(tickers))


@bot.command(name="groupcheck")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def groupcheck_command(ctx, group=None):
    tickers = get_group_tickers(group)
    if not tickers:
        await ctx.send(f"Group `{group}` is empty or missing.")
        return
    lines = [f"Group Check — {group}"]
    for ticker in tickers:
        analysis = await asyncio.to_thread(analyze_stock_price_only, ticker)
        if analysis:
            lines.extend(["", format_stock_check(analysis)])
    await send_long_message(ctx, "\n\n".join(lines))


@bot.command(name="groupscan")
@commands.guild_only()
@commands.cooldown(1, 120, commands.BucketType.guild)
async def groupscan_command(ctx, group=None, scan_type="momentum", *args):
    tickers = get_group_tickers(group)
    if not tickers:
        await ctx.send(f"Group `{group}` is empty or missing.")
        return
    if scan_type == "custom":
        parsed = parse_custom_filters(" ".join(args))
        if parsed["errors"]:
            await ctx.send(parsed["errors"][0])
            return
        results = await asyncio.to_thread(group_scan_results, tickers, "custom", parsed)
        await send_long_message(ctx, format_custom_scanner_results({"results": results, "scanned_count": len(tickers), "match_count": len(results), "filters_text": " ".join(args)}, parsed, broad=False))
        return
    if scan_type not in VALID_SCAN_TYPES:
        await ctx.send("Unknown scan type.")
        return
    results = await asyncio.to_thread(group_scan_results, tickers, scan_type, None)
    await send_long_message(ctx, format_scanner_results(results, scan_type))


@bot.command(name="groupalerts")
@commands.guild_only()
async def groupalerts_command(ctx, group=None, mode=None):
    data = load_watchlist_groups()
    if group not in data["groups"] or mode not in {"on", "off"}:
        await ctx.send("Use `!groupalerts semiconductors on` or `!groupalerts semiconductors off`.")
        return
    data["alerts_enabled"][group] = mode == "on"
    save_watchlist_groups(data)
    await ctx.send(f"Alerts for `{group}` are now {mode}.")


@bot.command(name="groupclear")
@commands.guild_only()
async def groupclear_command(ctx, group=None, confirm=None):
    data = load_watchlist_groups()
    if group not in data["groups"] or confirm != "CONFIRM":
        await ctx.send("Use `!groupclear groupname CONFIRM`.")
        return
    data["groups"][group] = []
    if group == "default":
        save_watchlist([])
    save_watchlist_groups(data)
    await ctx.send(f"Cleared group `{group}`.")


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


async def post_paper_trade_to_channel(ctx, message):
    channel = get_channel_by_name(ctx.guild, "paper-trades")
    if channel:
        await send_long_message(channel, message)


@bot.command(name="paperbuy")
@commands.guild_only()
async def paperbuy_command(ctx, ticker=None, quantity=None, price=None, *, reason=""):
    """Add a simulated paper buy."""
    ticker = normalize_ticker(ticker)
    try:
        quantity_value = float(quantity)
        price_value = float(price)
    except (TypeError, ValueError):
        await ctx.send("Use `!paperbuy AAPL 10 195.20 reason`.\n\n" + PAPER_TRADING_DISCLAIMER)
        return
    if not ticker or quantity_value <= 0 or price_value <= 0:
        await ctx.send("Ticker, quantity, and price must be valid positive values.\n\n" + PAPER_TRADING_DISCLAIMER)
        return
    trade = add_paper_trade(ticker, "buy", quantity_value, price_value, reason)
    message = (
        f"Paper buy recorded: {trade['ticker']} {trade['quantity']:g} @ {format_money(trade['price'])}\n"
        f"Reason: {trade['reason'] or 'N/A'}\n\n{PAPER_TRADING_DISCLAIMER}"
    )
    await ctx.send(message)
    await post_paper_trade_to_channel(ctx, message)


@bot.command(name="papersell")
@commands.guild_only()
async def papersell_command(ctx, ticker=None, quantity=None, price=None, *, reason=""):
    """Add a simulated paper sell."""
    ticker = normalize_ticker(ticker)
    try:
        quantity_value = float(quantity)
        price_value = float(price)
    except (TypeError, ValueError):
        await ctx.send("Use `!papersell AAPL 10 205.00 reason`.\n\n" + PAPER_TRADING_DISCLAIMER)
        return
    if not ticker or quantity_value <= 0 or price_value <= 0:
        await ctx.send("Ticker, quantity, and price must be valid positive values.\n\n" + PAPER_TRADING_DISCLAIMER)
        return
    trade = add_paper_trade(ticker, "sell", quantity_value, price_value, reason)
    message = (
        f"Paper sell recorded: {trade['ticker']} {trade['quantity']:g} @ {format_money(trade['price'])}\n"
        f"Reason: {trade['reason'] or 'N/A'}\n\n{PAPER_TRADING_DISCLAIMER}"
    )
    await ctx.send(message)
    await post_paper_trade_to_channel(ctx, message)


@bot.command(name="paperportfolio")
@commands.guild_only()
async def paperportfolio_command(ctx):
    """Show simulated open paper positions."""
    positions = open_positions()
    lines = ["Paper Portfolio"]
    if not positions:
        lines.append("No open simulated positions.")
    for position in positions:
        analysis = await asyncio.to_thread(analyze_stock_price_only, position["ticker"])
        latest_price = (analysis or {}).get("latest_price")
        average_cost = position["average_cost"]
        quantity = position["quantity"]
        pnl = None if latest_price is None else (latest_price - average_cost) * quantity
        pnl_percent = None if latest_price is None or average_cost <= 0 else ((latest_price - average_cost) / average_cost) * 100
        lines.extend(
            [
                "",
                f"{position['ticker']}",
                f"Shares: {quantity:g}",
                f"Average cost: {format_money(average_cost)}",
                f"Latest price: {format_money(latest_price)}",
                f"Unrealized P/L: {format_money(pnl)}",
                f"Unrealized P/L %: {format_percent(pnl_percent)}",
            ]
        )
    lines.extend(["", PAPER_TRADING_DISCLAIMER])
    await send_long_message(ctx, "\n".join(lines))


@bot.command(name="paperpnl")
@commands.guild_only()
async def paperpnl_command(ctx):
    """Show simulated realized and unrealized P/L."""
    realized = realized_pnl()
    unrealized = 0.0
    for position in open_positions():
        analysis = await asyncio.to_thread(analyze_stock_price_only, position["ticker"])
        latest_price = (analysis or {}).get("latest_price")
        if latest_price is not None:
            unrealized += (latest_price - position["average_cost"]) * position["quantity"]
    await ctx.send(
        "Paper P/L:\n"
        f"Realized P/L: {format_money(realized)}\n"
        f"Unrealized P/L: {format_money(unrealized)}\n\n"
        f"{PAPER_TRADING_DISCLAIMER}"
    )


@bot.command(name="paperjournal")
@commands.guild_only()
async def paperjournal_command(ctx):
    """Show recent paper trades."""
    lines = ["Recent Paper Trades"]
    trades = recent_trades()
    if not trades:
        lines.append("No simulated trades recorded.")
    for trade in trades:
        lines.append(
            f"- {trade['date']} | {trade['side'].upper()} {trade['ticker']} "
            f"{trade['quantity']:g} @ {format_money(trade['price'])} | {trade.get('reason') or 'N/A'}"
        )
    lines.extend(["", PAPER_TRADING_DISCLAIMER])
    await send_long_message(ctx, "\n".join(lines))


@bot.command(name="paperclose")
@commands.guild_only()
async def paperclose_command(ctx, ticker=None, price=None, *, reason=""):
    """Close a simulated open position."""
    ticker = normalize_ticker(ticker)
    try:
        price_value = float(price)
    except (TypeError, ValueError):
        await ctx.send("Use `!paperclose AAPL 210.00 reason`.\n\n" + PAPER_TRADING_DISCLAIMER)
        return
    trade = close_position(ticker, price_value, reason)
    if trade is None:
        await ctx.send(f"No open simulated position found for {ticker}.\n\n{PAPER_TRADING_DISCLAIMER}")
        return
    message = (
        f"Paper position closed: {trade['ticker']} {trade['quantity']:g} @ {format_money(trade['price'])}\n"
        f"Reason: {trade['reason'] or 'N/A'}\n\n{PAPER_TRADING_DISCLAIMER}"
    )
    await ctx.send(message)
    await post_paper_trade_to_channel(ctx, message)


@bot.command(name="paperclear")
@commands.guild_only()
async def paperclear_command(ctx, confirm=None):
    """Clear all simulated paper trades with explicit confirmation."""
    if confirm != "CONFIRM":
        await ctx.send("This clears all paper trades. Type `!paperclear CONFIRM` to continue.\n\n" + PAPER_TRADING_DISCLAIMER)
        return
    clear_paper_trades()
    await ctx.send("All paper trades cleared.\n\n" + PAPER_TRADING_DISCLAIMER)


print("Tip: On Windows, if `python` does not work, run this bot with `py main.py`.")
print("Price-only stock analysis enabled for alerts and scanners.")

if not DISCORD_BOT_TOKEN:
    print("Missing DISCORD_BOT_TOKEN. Create a .env file in this project folder.")
else:
    bot.run(DISCORD_BOT_TOKEN)
