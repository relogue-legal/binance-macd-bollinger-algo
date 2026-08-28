# ETHUSDC Bollinger + MACD Mean-Reversion Bot

A mean-reversion futures strategy for Binance, with a backtester and a live/paper-trading
runner. Trades ETHUSDC on the 1-hour timeframe using Bollinger Bands for entries and a
daily/hourly MACD filter for confirmation.

**This is shared for educational purposes. It is not financial advice, and trading
futures with leverage can lose money quickly. Use at your own risk, and test on
testnet/paper trading before running with real funds.**

## How it works

- **Entry**: price wicks through a Bollinger Band (built on hourly candles) while the
  hourly MACD signal is beyond a configurable threshold in the direction of the trade.
- **Exit**: price reverts to the dynamic SMA (mean), or a max hold period (`close_period`
  hours) is reached, whichever comes first.
- A daily MACD value is also computed and logged per trade (currently used for analysis,
  not as a hard filter) — see `daily_macd` in `backtest_macd_optimised.py`.

## Repo contents

| File | Purpose |
|---|---|
| `app.py` | Pulls historical 1-minute ETHUSDC futures klines from Binance and writes a CSV for backtesting. |
| `backtest_macd_optimised.py` | Runs the strategy against the CSV produced by `app.py` and outputs trade-by-trade and summary results. |
| `docker_prod.py` | Live trading runner: polls Binance, computes signals, manages orders, and exposes a small Flask dashboard (logs/restart). Meant to run in Docker. |
| `Dockerfile` | Builds a container for `docker_prod.py`. |

## Setup

1. Clone the repo and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your own Binance API key/secret:
   ```bash
   cp .env.example .env
   ```
3. **Never commit your `.env` file.** It's already excluded via `.gitignore`.

## Backtesting

```bash
python app.py                        # downloads klines, writes a CSV
python backtest_macd_optimised.py    # set file_path in the script to the CSV above
```

Strategy parameters (`boll_window`, `std_multiplier`, `macd_threshold`, `close_period`,
`leverage`) are set at the bottom of `backtest_macd_optimised.py`. There are commented-out
parameter ranges in the script for grid-search-style optimization — uncomment and adapt
as needed.

## Running live (Docker)

```bash
docker build -t docker_prod .
docker run -d \
  --env-file .env \
  -p 5000:5000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name algo-bot \
  docker_prod
```

**Notes:**
- The bot uses the Docker socket to find its own container (for the `/restart` endpoint
  and self-healing on API errors), so it needs `/var/run/docker.sock` mounted as shown
  above. Only do this if you understand the security implications of giving a container
  access to the host's Docker daemon.
- The Flask dashboard (`/`, `/logs`, `/restart`) is protected by basic auth **only if**
  `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` are set in `.env`. If they're unset, auth
  is disabled and a warning is logged — don't expose port 5000 publicly in that case.
  Put it behind a reverse proxy or VPN, not directly on the open internet.
- `dashboard.html` (referenced by the `/` route) isn't included here — add your own or
  remove the route if you don't need a UI.

## Disclaimer

This code is provided as-is with no warranty. Backtest results do not guarantee future
performance. You are solely responsible for any funds you trade with this bot.
