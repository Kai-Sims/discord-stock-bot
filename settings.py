from copy import deepcopy

from storage import data_path, read_json, write_json


BOT_SETTINGS_FILE = data_path("bot_settings.json")

DEFAULT_BOT_SETTINGS = {
    "eod_summary_time": "13:30",
    "morning_briefing_time": "06:30",
    "earnings_lookahead_days": 14,
    "earnings_scan_limit": 50,
    "scanner_result_limit": 15,
    "broad_scan_limit": 500,
    "pauls_dividend_min_percent": 5.0,
    "pauls_ath_threshold_percent": 5.0,
    "pauls_5day_gain_percent": 10.0,
    "quiet_mode": False,
    "market_hours_only": False,
    "alert_frequency_minutes": 5,
    "min_alert_severity": "low",
    "timezone": "America/Los_Angeles",
}


def load_bot_settings():
    settings = read_json(BOT_SETTINGS_FILE, deepcopy(DEFAULT_BOT_SETTINGS))
    if not isinstance(settings, dict):
        settings = deepcopy(DEFAULT_BOT_SETTINGS)

    changed = False
    for key, value in DEFAULT_BOT_SETTINGS.items():
        if key not in settings:
            settings[key] = value
            changed = True

    if changed or not read_json(BOT_SETTINGS_FILE, None):
        save_bot_settings(settings)

    print("Loaded bot settings.")
    return settings


def save_bot_settings(settings):
    merged = deepcopy(DEFAULT_BOT_SETTINGS)
    if isinstance(settings, dict):
        merged.update(settings)
    write_json(BOT_SETTINGS_FILE, merged)
    return merged


def get_setting(key, default=None):
    return load_bot_settings().get(key, default)


def set_setting(key, value):
    settings = load_bot_settings()
    settings[key] = value
    return save_bot_settings(settings)


def reset_bot_settings():
    save_bot_settings(deepcopy(DEFAULT_BOT_SETTINGS))
    return deepcopy(DEFAULT_BOT_SETTINGS)
