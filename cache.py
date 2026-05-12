from datetime import datetime, timedelta

from storage import data_path, read_json, write_json


CACHE_FILES = {
    "stock_data": data_path("stock_data_cache.json"),
    "earnings": data_path("earnings_cache.json"),
    "analyst": data_path("analyst_cache.json"),
    "dividend": data_path("dividend_cache.json"),
}


def _now():
    return datetime.now()


def _parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None


def load_cache(cache_name):
    path = CACHE_FILES.get(cache_name, data_path(f"{cache_name}_cache.json"))
    data = read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    print(f"Loaded cache: {cache_name}")
    return data


def save_cache(cache_name, cache_data):
    path = CACHE_FILES.get(cache_name, data_path(f"{cache_name}_cache.json"))
    write_json(path, cache_data if isinstance(cache_data, dict) else {})


def get_cached_value(cache_name, key, max_age_minutes):
    cache = load_cache(cache_name)
    item = cache.get(str(key).upper())
    if not isinstance(item, dict):
        return None

    checked_at = _parse_timestamp(item.get("last_checked"))
    if checked_at is None:
        return None

    if _now() - checked_at > timedelta(minutes=max_age_minutes):
        return None

    if item.get("status") == "rate_limited":
        return None

    return item.get("value")


def set_cached_value(cache_name, key, value, status="ok"):
    cache = load_cache(cache_name)
    cache[str(key).upper()] = {
        "value": value,
        "last_checked": _now().isoformat(timespec="seconds"),
        "status": status,
    }
    save_cache(cache_name, cache)


def is_recently_rate_limited(cache_name, key, retry_after_minutes):
    cache = load_cache(cache_name)
    item = cache.get(str(key).upper())
    if not isinstance(item, dict) or item.get("status") != "rate_limited":
        return False

    checked_at = _parse_timestamp(item.get("last_checked"))
    if checked_at is None:
        return False

    return _now() - checked_at < timedelta(minutes=retry_after_minutes)
