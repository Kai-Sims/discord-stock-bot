# Stock Alert Discord Bot

A beginner-friendly Discord bot that creates a stock and options alert channel structure for a stock market tracking server.

This project is for educational research and market monitoring only. It does not provide financial advice, buy signals, sell signals, or trading recommendations.

## Features

- Uses `discord.py`
- Loads secrets from a local `.env` file
- Creates a `Stock Market Bot` category
- Creates stock alert channels only when they do not already exist
- Saves tracked tickers in `watchlist.json`
- Uses `yfinance` to check recent stock data
- Runs a background alert loop every 5 minutes
- Runs on-demand stock scanners for stock ideas and watchlist candidates
- Can optionally build a broader US stock universe from Nasdaq Trader symbol files
- Includes setup, watchlist, stock check, and alert status commands

## Create a Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**.
3. Give the application a name, then click **Create**.
4. Open the **Bot** page in the left sidebar.
5. Click **Add Bot** if a bot has not already been created.
6. Under **Token**, click **Reset Token** or **View Token**.
7. Copy the token and keep it private.

Do not paste your token into public code, Discord messages, GitHub, or screenshots.

## Enable Message Content Intent

This bot uses text commands such as `!setup`, so it needs the Message Content Intent.

1. In the Discord Developer Portal, open your application.
2. Go to **Bot**.
3. Scroll to **Privileged Gateway Intents**.
4. Enable **Message Content Intent**.
5. Save your changes.

## Invite the Bot With Manage Channels Permission

1. In the Discord Developer Portal, open your application.
2. Go to **OAuth2** > **URL Generator**.
3. Under **Scopes**, select **bot**.
4. Under **Bot Permissions**, select:
   - **Manage Channels**
   - **Send Messages**
   - **Read Message History**
   - **View Channels**
5. Copy the generated URL.
6. Open the URL in your browser and invite the bot to your server.

## Get Your Server ID

1. Open Discord.
2. Go to **User Settings** > **Advanced**.
3. Enable **Developer Mode**.
4. Right-click your Discord server icon.
5. Click **Copy Server ID**.

## Create Your `.env` File

Copy `.env.example` and rename the copy to `.env`.

```env
DISCORD_BOT_TOKEN=your_real_bot_token_here
GUILD_ID=your_real_server_id_here
```

Replace the example values with your real bot token and server ID.

## Windows Quick Start

PowerShell:

```powershell
cd "$env:USERPROFILE\Desktop\stock-discord-bot"
py --version
py -m pip install -r requirements.txt
copy .env.example .env
py main.py
```

Command Prompt:

```bat
cd %USERPROFILE%\Desktop\stock-discord-bot
py --version
py -m pip install -r requirements.txt
copy .env.example .env
py main.py
```

After copying `.env.example` to `.env`, open `.env` and add your real Discord bot token and server ID before running the bot.

## Install Dependencies

On Windows, use the Python launcher:

```powershell
py -m pip install -r requirements.txt
```

This installs `discord.py`, `python-dotenv`, `yfinance`, and `pandas`.

## Run the Bot

On Windows, run:

```powershell
py main.py
```

When the bot starts, it will log in, find your server by `GUILD_ID`, and create the stock bot category and channels if they do not already exist.

## Bot Commands

```text
!setup
```

Manually runs the channel setup process.

```text
!channels
```

Lists the stock bot channels.

```text
!ping
```

Responds with:

```text
Stock bot is online.
```

```text
!add AAPL
```

Adds `AAPL` to `watchlist.json`.

```text
!remove AAPL
```

Removes `AAPL` from `watchlist.json`.

```text
!watchlist
```

Displays all currently tracked stocks.

```text
!clearwatchlist
```

Clears every ticker from the watchlist.

```text
!resetwatchlist
```

Restores the default watchlist:

```text
AAPL, AMD, NVDA, QQQ, SPY, TSLA
```

```text
!check AAPL
```

Checks recent stock data for `AAPL`, including price, day high, day low, 52-week high and low, RSI, 20-day moving average, 50-day moving average, 200-day moving average, and trend.

```text
!alerts
```

Shows whether the background stock alert loop is running, how many tickers are being tracked, and the check interval.

```text
!eodsummary
```

Manually post the end-of-day watchlist summary in the current channel.

```text
!eodstatus
```

Show the EOD summary loop status, scheduled time, last summary date, target channel, and tracked stock count.

```text
!seteodtime 13 30
```

Set the EOD summary time for the current bot session. The time uses Pacific time.

```text
!wsbstatus
!wsbmentions
!wsbtrack
!wsbrefresh
!wsbsettings
!wsbclear
```

Use the WallStreetBets tracker to monitor ticker mentions, attention, and discussion activity.

```text
!scan
```

Runs a balanced scan across `scanner_universe.json` and returns up to 10 stock ideas.

```text
!scan momentum
```

Finds possible setups with stronger uptrend characteristics.

```text
!scan breakouts
```

Finds scanner results near 52-week high areas.

```text
!scan oversold
```

Finds possible oversold bounce candidates.

```text
!scan pullbacks
```

Finds uptrend pullback watchlist candidates.

```text
!scan volume
```

Finds stocks with unusual relative volume.

```text
!scan custom rsi>70
!scan custom rsi<30 price>5 volume>1000000
!scan custom rsi=45-65 above50ma=true above200ma=true
!scan custom relvol>1.5 change5d>3
```

Runs a custom scan across `scanner_universe.json`.

```text
!scan all custom rsi<35 price>10 volume>500000
```

Runs a custom scan across the broad US stock universe after applying the broad-scan filters.

```text
!scanhelp
```

Shows all supported custom scan filters and examples.

```text
!scannerlist
```

Shows all tickers in the scanner universe.

```text
!scanadd RKLB
```

Adds `RKLB` to `scanner_universe.json`.

```text
!scanremove RKLB
```

Removes `RKLB` from `scanner_universe.json`.

```text
!scanreset
```

Resets `scanner_universe.json` to the starter scanner universe.

```text
!refreshuniverse
```

Downloads and rebuilds a broad US stock ticker list in `us_stock_universe.json`.

```text
!universecount
```

Shows the broad universe ticker count, last update time, and source.

```text
!scan all
```

Runs a balanced broad universe scan after applying price and volume filters.

```text
!scan all momentum
```

Runs a broad momentum scan across a filtered batch of US stocks.

```text
!scan all breakouts
!scan all oversold
!scan all pullbacks
!scan all volume
```

Runs broad scans for specific scanner styles.

```text
!setscanlimit 500
```

Sets the broad scan ticker limit for the current bot session. The minimum is `50` and the maximum is `1000`.

```text
!scanfilters
```

Shows the current broad-scan filters.

## Watchlist

The tracked stock symbols are stored in `watchlist.json`.

Default watchlist:

```json
{
  "tickers": ["AAPL", "TSLA", "NVDA", "AMD", "SPY", "QQQ"]
}
```

You can edit the watchlist with Discord commands or by carefully editing `watchlist.json` while the bot is stopped.

## Stock Alerts

The bot checks the current watchlist every 5 minutes. It sends alerts when tracked stocks are near daily highs or lows, near 52-week highs or lows, overbought or oversold by RSI, or showing a possible volume spike.

Alerts are sent to the matching stock bot channels when those channels exist:

- `stock-alerts`
- `daily-highs`
- `daily-lows`
- `fifty-two-week-highs`
- `fifty-two-week-lows`
- `rsi-alerts`
- `volume-spikes`

If a specific alert channel is missing, the bot falls back to `stock-alerts`. If `stock-alerts` is also missing, it prints the issue in the terminal.

To reduce spam, the bot sends each alert type for each ticker once per day. Alert history is kept in memory and resets when the bot restarts.

## End-Of-Day Summary

The bot can post an end-of-day watchlist summary to `trade-journal`.

Default schedule:

```text
1:30 PM Pacific, Monday through Friday
```

This is 30 minutes after the regular U.S. market close at 1:00 PM Pacific / 4:00 PM Eastern, giving market data time to settle. The summary does not run on Saturday or Sunday. Market holidays are not handled yet.

The summary includes:

- Watchlist count
- Bullish, bearish, and mixed trend counts
- Overbought and oversold RSI counts
- Volume spike count
- Price, day high, day low, day range, RSI, relative volume, 5-day change, trend, and notable flags for each tracked ticker

Commands:

```text
!eodsummary
```

Manually post the end-of-day watchlist summary.

```text
!eodstatus
```

Show EOD summary schedule and status.

```text
!seteodtime 13 30
```

Set the EOD summary time for this bot session only, using Pacific time.

If `trade-journal` is missing, the bot tries to send the summary to `stock-alerts`. If neither channel exists, it prints a terminal warning and does not crash.

Every EOD summary ends with:

```text
End-of-day summaries are for tracking and research only and are not financial advice.
```

## Stock Scanner

The scanner looks beyond your personal watchlist and sweeps through `scanner_universe.json` for stock ideas, watchlist candidates, scanner results, and possible setups.

Default scanner types:

- `!scan` checks a balanced mix of trend, breakout, pullback, volume, and RSI factors.
- `!scan momentum` looks for stocks above 20-day, 50-day, and 200-day moving averages with moderate RSI and positive 5-day performance.
- `!scan breakouts` looks for stocks within 3% of their 52-week high with supportive trend and volume factors.
- `!scan oversold` looks for possible oversold bounce candidates using RSI and moving-average location.
- `!scan pullbacks` looks for uptrends that have recently pulled back near key moving averages.
- `!scan volume` looks for unusual volume using relative volume.

The scanner only runs when a user types a command. It does not run every 5 minutes because scanning many tickers too often may hit data limits. A disabled-by-default daily scanner option exists in `main.py` with `ENABLE_DAILY_SCANNER = False`.

To reduce accidental overuse, `!scan` can be run once every 60 seconds per user.

Every scanner result includes:

```text
Scanner results are for research only and are not financial advice.
```

## Custom Scan Filters

Custom scans let you type scanner criteria directly in Discord. Use `!scan custom ...` for the saved scanner list or `!scan all custom ...` for the broad US universe.

Examples:

```text
!scan custom rsi>70
!scan custom rsi<30 price>5 volume>1000000
!scan custom rsi=45-65 above50ma=true above200ma=true
!scan custom relvol>1.5 change5d>3
!scan all custom rsi<35 price>10 volume>500000
!scanhelp
```

| Filter | What it means | Examples |
| --- | --- | --- |
| `rsi` | Relative Strength Index filter | `rsi>70`, `rsi<30`, `rsi=45-65` |
| `price` | Latest price filter | `price>10`, `price<200`, `price=20-50` |
| `volume` | Current daily volume filter | `volume>1000000`, `volume<5000000` |
| `relvol` | Relative volume filter | `relvol>1.5`, `relvol<1` |
| `change5d` | 5-day percent change filter | `change5d>3`, `change5d<-5`, `change5d=-2-5` |
| `near52whigh` | Price is within a percent of the 52-week high | `near52whigh<5` |
| `near52wlow` | Price is within a percent of the 52-week low | `near52wlow<10` |
| `above20ma` | Price is above the 20-day moving average | `above20ma=true` |
| `above50ma` | Price is above the 50-day moving average | `above50ma=true` |
| `above200ma` | Price is above the 200-day moving average | `above200ma=true` |
| `below20ma` | Price is below the 20-day moving average | `below20ma=true` |
| `below50ma` | Price is below the 50-day moving average | `below50ma=true` |
| `below200ma` | Price is below the 200-day moving average | `below200ma=true` |
| `trend` | Trend label from the bot's stock analysis | `trend=bullish`, `trend=bearish`, `trend=mixed`, `trend=high-risk` |

Custom scanner results are scanner results and watchlist candidates for research only. They are not buy signals, sell signals, or trading instructions.

## Broad US Stock Scanner

The broad scanner can build a larger US stock universe instead of only scanning the manually saved `scanner_universe.json` list.

Use:

```text
!refreshuniverse
```

This downloads Nasdaq Trader symbol files, filters out test issues, ETFs, and many non-common-stock securities when possible, then saves the result to `us_stock_universe.json`.

Check the saved universe:

```text
!universecount
```

Run a broad scan:

```text
!scan all momentum
```

Other broad scan types:

```text
!scan all
!scan all breakouts
!scan all oversold
!scan all pullbacks
!scan all volume
```

Broad scans can take several minutes. Before running full technical analysis, the bot filters for stocks with a latest close of at least `$5.00` and 20-day average volume of at least `500,000`. It then scans up to the current broad scan limit, which defaults to `500`.

Free data sources may rate-limit, fail, or return missing data. Broad scans have a 10-minute cooldown per server because scanning thousands of tickers can hit data-provider limits.

Adjust the current session limit:

```text
!setscanlimit 500
```

View the current broad scan filters:

```text
!scanfilters
```

Broad scanner results are stock ideas and watchlist candidates for research only. They are not financial advice, buy signals, sell signals, or trading recommendations.

## WallStreetBets Tracker

The WallStreetBets tracker monitors recent `r/wallstreetbets` posts through the Reddit API, extracts ticker mentions, and combines discussion activity with market measurements from the existing `analyze_stock()` function.

It tracks:

- Ticker mentions
- Unique post count
- Total Reddit score
- Total comments
- Example post titles
- Price, RSI, relative volume, 5-day change, and trend for tracked tickers

WSB mentions are attention signals and research signals only. They are not trade signals, buy signals, sell signals, or trading recommendations.

The bot creates these WSB channels when `!setup` runs:

- `wsb-mentions`
- `wsb-tracking`
- `wsb-alerts`

### Reddit API Setup

Reddit API access may require developer setup or approval. Free APIs may rate-limit, fail, or return incomplete data.

1. Go to Reddit and sign in.
2. Open <https://www.reddit.com/prefs/apps>.
3. Click **create another app**.
4. Choose **script**.
5. Add a name, description, and redirect URI such as `http://localhost:8080`.
6. Copy the client ID shown under the app name.
7. Copy the client secret.
8. Add both values to your `.env` file.

Required `.env` variables:

```env
REDDIT_CLIENT_ID=your_reddit_client_id_here
REDDIT_CLIENT_SECRET=your_reddit_client_secret_here
REDDIT_USER_AGENT=discord-stock-bot:v1.0 (by u/your_reddit_username)
```

Do not share Reddit credentials, Discord bot tokens, webhook URLs, or API keys.

### WSB Commands

```text
!wsbstatus
```

Shows whether Reddit credentials are configured, whether the WSB loop is running, the check interval, subreddit, and number of currently tracked WSB tickers.

```text
!wsbmentions
```

Shows the current top WSB ticker mentions from `wsb_mentions.json`.

```text
!wsbtrack
```

Shows WSB-tracked tickers with market measurements from `wsb_tracking.json`.

```text
!wsbrefresh
```

Manually refreshes WSB mentions and tracking. This command has a 2-minute cooldown to respect Reddit API limits.

```text
!wsbsettings
```

Shows the current WSB tracker settings.

```text
!wsbclear
```

Clears `wsb_mentions.json` and `wsb_tracking.json`.

Every WSB output includes:

```text
WSB tracking is for research only and is not financial advice.
```

## Created Channels

The bot creates these text channels inside the `Stock Market Bot` category:

- `stock-alerts`
- `options-alerts`
- `daily-highs`
- `daily-lows`
- `fifty-two-week-highs`
- `fifty-two-week-lows`
- `rsi-alerts`
- `volume-spikes`
- `earnings-alerts`
- `news-alerts`
- `watchlist`
- `stock-ideas`
- `bot-status`
- `trade-journal`
- `bot-commands`

## Notes

- Never hardcode your Discord bot token.
- Never commit your `.env` file to GitHub.
- If setup fails, confirm the bot has **Manage Channels** permission.
- If commands do not respond, confirm **Message Content Intent** is enabled.
- Stock data can be delayed, missing, or unavailable. Use this bot for learning and tracking, not financial decisions.
- Scanner results are research prompts only. They are not buy signals, sell signals, or trading instructions.

## Windows Troubleshooting

- If `python` does not work but `py --version` works, use `py`.
- Use `py -m pip install -r requirements.txt` instead of `pip install -r requirements.txt`.
- Use `py main.py` instead of `python main.py`.
- Keeping the project folder on your Desktop is fine.
- Run terminal commands from inside the project folder, the same folder that contains `main.py`, `requirements.txt`, and `.env`.
