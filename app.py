from binance.client import Client
import pandas as pd
from datetime import datetime, timezone, timedelta
import concurrent.futures
import matplotlib.pyplot as plt
import time
from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
client = Client(API_KEY, API_SECRET)
systemT1 = time.time()*1000
client.get_server_time()
systemT2 = time.time()*1000
lag = systemT2 - systemT1

#--------------------------#
symbol = "ETHUSDC"
interval = Client.KLINE_INTERVAL_1MINUTE
start_dt = datetime(2025, 1, 25, 0, 0, 0, tzinfo=timezone.utc)
end_dt = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
#--------------------------#

def get_all_klines_futures(symbol, interval, start_time, end_time):
    # Convert to milliseconds
    start_time = int(start_time.timestamp() * 1000)
    end_time = int(end_time.timestamp() * 1000)
    all_klines = []
    while start_time < end_time:

        klines = client.futures_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=start_time,
            end_str=end_time,
        )
        if not klines:
            break
        api_attempts = int(client.response.headers['x-mbx-used-weight-1m'])
        if api_attempts > 1500:
            print(api_attempts, 'hala')
            time.sleep(30)
        all_klines += klines
        start_time = klines[-1][0] + 1

    return all_klines

# Split into chunks
chunks = []
current = start_dt
while current < end_dt:
    chunk_end = min(current + timedelta(minutes=1500), end_dt)
    chunks.append((current, chunk_end))
    current = chunk_end + timedelta(milliseconds=1)

# Fetch all in parallel (limit threads to 3–5 to avoid hitting rate limits)
all_klines = []
with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
    futures = [executor.submit(get_all_klines_futures, symbol, interval, start, end) for start, end in chunks]
    for future in concurrent.futures.as_completed(futures):
        all_klines.extend(future.result())

# klines = get_all_klines_futures(symbol, interval, start_ts, end_ts)
df = pd.DataFrame(all_klines)
df[0] = pd.to_datetime(df[0], unit='ms')
df[6] = pd.to_datetime(df[6], unit='ms')
df[[1,2,3,4,5,9]] = df[[1,2,3,4,5,9]].astype(float)
df = df.sort_values(by=0, ascending=True)

df.columns = [
    "Open time",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Close time",
    "Quote asset volume",
    "Number of trades",
    "Taker buy base asset volume",
    "Taker buy quote asset volume",
    "Ignore"
]

print('done scraping')
df.to_csv(f'{symbol}_{interval}_{df["Open time"].min().date()} to {df["Open time"].max().date()}.csv')
df['Ratio'] = df['Taker buy base asset volume'] / df['Volume']

