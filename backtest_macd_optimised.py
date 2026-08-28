import time
import pandas as pd
import numpy as np
import warnings
from datetime import datetime, timedelta

pd.options.mode.chained_assignment = None

# Suppress numpy/math RuntimeWarnings (e.g., divide by zero)
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.simplefilter(action='ignore', category=FutureWarning)


def initialise_df(file_path):
    global df
    df = pd.read_csv(file_path)
    df = df.set_index(pd.DatetimeIndex(df['Open time']))
    df = df[['Open', 'Close', 'High', 'Low', 'Volume']]
    df['hour'] = df.index.floor('H')
    # df = df[df.index.year == 2022]
    # df = df[df.index.year.isin([2024, 2025])]


def ema(df, window):
    a = 2 / (1 + window)
    ema = df['Close'].iloc[:window].mean()
    padded_close = df['Close'].copy()
    padded_close.iloc[:window - 1] = pd.NA
    padded_close.iloc[window - 1] = ema
    return padded_close.ewm(alpha=a, adjust=False).mean()


def candles_generation(df):
    candles = df.resample('1H').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'  # optional, if you have a volume column
    })
    candles['hour'] = candles.index.floor('H')

    daily = df.resample('1D').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'  # optional, if you have a volume column
    })
    daily['day'] = daily.index.floor('D')

    # EMA
    daily[f'EMA({fast_window})'] = ema(daily, fast_window)
    daily[f'EMA({slow_window})'] = ema(daily, slow_window)

    def signal_ema(df, window):
        a = 2 / (1 + window)
        ema = df['MACD_LINE'].iloc[:window].mean()
        padded_close = df['MACD_LINE'].copy()
        padded_close.iloc[:window - 1] = pd.NA
        padded_close.iloc[window - 1] = ema
        return padded_close.ewm(alpha=a, adjust=False).mean()

    daily['MACD_LINE'] = daily[f'EMA({fast_window})'] - daily[f'EMA({slow_window})']
    daily['MACD_SIGNAL_DAILY'] = signal_ema(daily, signal_window)
    daily = daily['MACD_SIGNAL_DAILY']

    return candles, daily


def add_indicators(candles):
    candles[f'EMA({fast_window})'] = ema(candles, fast_window)
    candles[f'EMA({slow_window})'] = ema(candles, slow_window)

    def signal_ema(df, window):
        a = 2 / (1 + window)
        ema = df['MACD_LINE'].iloc[:window].mean()
        padded_close = df['MACD_LINE'].copy()
        padded_close.iloc[:window - 1] = pd.NA
        padded_close.iloc[window - 1] = ema
        return padded_close.ewm(alpha=a, adjust=False).mean()

    candles['MACD_LINE'] = candles[f'EMA({fast_window})'] - candles[f'EMA({slow_window})']
    candles['MACD_SIGNAL'] = signal_ema(candles, signal_window)

    candles['SMA'] = candles['Close'].rolling(window=boll_window - 1).mean()

    candles['close'] = pd.to_numeric(candles['Close'], downcast='float')
    candles['STD'] = [window.to_list() for window in candles['Close'].rolling(window=boll_window - 1)]

    candles['PREV_SMA'] = candles['SMA'].shift(1)
    candles['PREV_STD'] = candles['STD'].shift(1)
    candles['PREV_CLOSE'] = candles['Close'].shift(1)
    candles[f'PREV_EMA({fast_window})'] = candles[f'EMA({fast_window})'].shift(1)
    candles[f'PREV_EMA({slow_window})'] = candles[f'EMA({slow_window})'].shift(1)
    candles['PREV_MACD_SIGNAL'] = candles['MACD_SIGNAL'].shift(1)
    return candles


# Merge the *previous* SMA into the trades dataframe
def create_trades_with_candle_data(candles, df):
    df = df.reset_index()  # 'time' becomes a column again
    trades_with_candles = df.merge(
        candles[['hour', f'PREV_EMA({fast_window})', f'PREV_EMA({slow_window})', 'PREV_MACD_SIGNAL', 'PREV_SMA',
                 'PREV_STD', 'PREV_CLOSE']],
        on='hour',
        how='left'
    )
    trades_with_candles.set_index('Open time', inplace=True)  # restore index

    return trades_with_candles


def backtest_ready_dataframe(df_wc, daily):
    df_wc = df_wc.dropna()
    # DYNAMIC SMA
    df_wc['dynamic_SMA'] = ((df_wc['PREV_SMA'] * (boll_window - 1) + df_wc['Close']) / boll_window)
    df_wc['dynamic_SMA'] = ((df_wc['PREV_SMA'] * (boll_window - 1) + df_wc['Close']) / boll_window)

    # EMA
    a_fast = 2 / (fast_window + 1)
    a_slow = 2 / (slow_window + 1)
    a_signal = 2 / (signal_window + 1)
    df_wc[f'EMA({fast_window})'] = (df_wc['Close'] - df_wc[f'PREV_EMA({fast_window})']) * a_fast + df_wc[
        f'PREV_EMA({fast_window})']
    df_wc[f'EMA({slow_window})'] = (df_wc['Close'] - df_wc[f'PREV_EMA({slow_window})']) * a_slow + df_wc[
        f'PREV_EMA({slow_window})']
    df_wc['MACD_LINE'] = df_wc[f'EMA({fast_window})'] - df_wc[f'EMA({slow_window})']
    df_wc['MACD_SIGNAL'] = (df_wc['MACD_LINE'] - df_wc['PREV_MACD_SIGNAL']) * a_signal + df_wc['PREV_MACD_SIGNAL']

    # DYNAMIC STD
    df_wc['Close'] = pd.to_numeric(df_wc['Close'], downcast='float')
    std_array = np.column_stack([df_wc['PREV_STD'].tolist(), df_wc['Close']])
    df_wc['std'] = std_array.std(axis=1, ddof=0)
    df_wc['upper_band'] = df_wc['dynamic_SMA'] + std_multiplier * df_wc['std']
    df_wc['lower_band'] = df_wc['dynamic_SMA'] - std_multiplier * df_wc['std']

    df = df_wc[['Open', 'Close', 'High', 'Low', 'dynamic_SMA', 'upper_band', 'lower_band', 'MACD_SIGNAL']]
    df = df.merge(daily, left_index=True, right_index=True, how='left')
    df = df.ffill()
    return df


def singlular_backtest():
    global results
    in_position = False
    long_position = False
    short_position = False
    trades = []

    for row in df.itertuples():
        idx = row.Index

        if not in_position:
            if row.High >= row.upper_band and row.MACD_SIGNAL <= -(row.Close * macd_threshold):
                entry_price = row.upper_band
                macd = row.MACD_SIGNAL
                daily_macd = row.MACD_SIGNAL_DAILY
                bought_at = idx
                in_position = True
                short_position = True
                highest_price = None
            elif row.Low <= row.lower_band and row.MACD_SIGNAL >= (row.Close * macd_threshold):
                entry_price = row.lower_band
                macd = row.MACD_SIGNAL
                daily_macd = row.MACD_SIGNAL_DAILY
                bought_at = idx
                in_position = True
                long_position = True
                lowest_price = None

        elif long_position:

            if lowest_price is None or row.Low < lowest_price:
                lowest_price = row.Low

            if long_position and idx > bought_at and row.High >= row.dynamic_SMA:
                profit = ((row.dynamic_SMA - entry_price) * leverage) / entry_price
                drawdown_pct = ((lowest_price - entry_price) * leverage) / entry_price
                trades.append({
                    'entry_time': bought_at,
                    'exit_time': idx,
                    'entry_price': entry_price,
                    'exit_price': row.dynamic_SMA,
                    'profit': profit,
                    'macd': macd,
                    'daily_macd': daily_macd,
                    'max_drawdown_pct': drawdown_pct,
                })
                in_position = long_position = False

            elif long_position and idx > bought_at + timedelta(hours=close_period):
                profit = ((row.Close - entry_price) * leverage) / entry_price
                drawdown_pct = ((lowest_price - entry_price) * leverage) / entry_price
                trades.append({
                    'entry_time': bought_at,
                    'exit_time': idx,
                    'entry_price': entry_price,
                    'exit_price': row.Close,
                    'profit': profit,
                    'macd': macd,
                    'daily_macd': daily_macd,
                    'max_drawdown_pct': drawdown_pct,
                })
                in_position = long_position = False

        elif short_position:

            if highest_price is None or row.High > highest_price:
                highest_price = row.High

            if short_position and idx > bought_at and row.Low <= row.dynamic_SMA:
                profit = ((entry_price - row.dynamic_SMA) * leverage) / entry_price
                drawdown_pct = ((entry_price - highest_price) * leverage) / entry_price
                trades.append({
                    'entry_time': bought_at,
                    'exit_time': idx,
                    'entry_price': entry_price,
                    'exit_price': row.dynamic_SMA,
                    'profit': profit,
                    'macd': macd,
                    'daily_macd': daily_macd,
                    'max_drawdown_pct': drawdown_pct,
                })
                in_position = short_position = False

            elif short_position and idx > bought_at + timedelta(hours=close_period):
                profit = ((entry_price - row.Close) * leverage) / entry_price
                drawdown_pct = ((entry_price - highest_price) * leverage) / entry_price
                trades.append({
                    'entry_time': bought_at,
                    'exit_time': idx,
                    'entry_price': entry_price,
                    'exit_price': row.Close,
                    'profit': profit,
                    'macd': macd,
                    'daily_macd': daily_macd,
                    'max_drawdown_pct': drawdown_pct,
                })
                in_position = short_position = False

    if trades:
        trades = pd.DataFrame(trades)
        trades['time_to_next_entry'] = trades['entry_time'] - trades['exit_time'].shift(1)
        trades['trade_period'] = trades['exit_time'] - trades['entry_time']
        # Calculate cumulative return over time
        trades['equity'] = (trades['profit'] + 1).cumprod()
        trades['peak_equity'] = trades['equity'].cummax()
        trades['drawdown'] = (trades['equity'] - trades['peak_equity']) / trades['peak_equity']
        trades['max_drawdown'] = trades['drawdown'].cummin()

        print(f'return for '
              f'boll_window: {boll_window}, '
              f'std_multiplier: {std_multiplier}, '
              f'macd_threshold: {macd_threshold}, '
              f'for time period: {file_path}, '
              f'for close period: {close_period}, '
              f': {(pd.Series(trades["profit"]) + 1).prod()}, '
              f'trades executed: {trades["profit"].count()}, '
              f'max unrealized_drawdown: {trades["max_drawdown_pct"].min()}, '
              f'max drawdown: {trades["max_drawdown"].min()}'
              )
        # Create summary row
        summary_row = {
            'boll_window': boll_window,
            'std_multiplier': std_multiplier,
            'macd_threshold': macd_threshold,
            'file_path': file_path,
            'total_return': (pd.Series(trades["profit"]) + 1).prod(),
            'trades_executed': trades["profit"].count(),
            'max unrealised dd': trades['max_drawdown_pct'].min(),
            'max dd': trades['max_drawdown'].min(),
        }
        results.append(summary_row)
        results_df = pd.DataFrame(results)
        results_df.to_csv("backtest_summary2.csv", index=False)
        trades.to_csv('trades_5.csv')
        return trades


file_path = 'ETHUSDC_1m_2025-01-25 to 2026-08-28.csv'

leverage = 7
sl = 0.05

boll_window = 27
std_multiplier = 1.7
signal_window = 9
fast_window = 12
slow_window = 26
macd_threshold = 0.0019
close_period = 25
results = []

# boll_window_range = np.arange(27, 31, 1)
# std_multiplier_range = np.arange(1.5, 2.11, 0.2)
macd_threshold_range = np.arange(0.00, 0.01, 0.0003)
# close_period_range = np.arange(14, 34, 1)
# for boll_window in boll_window_range:
#     for std_multiplier in std_multiplier_range:
# for macd_threshold in macd_threshold_range:
    # for close_period in close_period_range:
close_period = int(close_period)
t1 = time.perf_counter()
initialise_df(file_path)
candles, daily = candles_generation(df)
candles = add_indicators(candles)
df_wc = create_trades_with_candle_data(candles, df)
df = backtest_ready_dataframe(df_wc, daily)
print(f'df ready ({time.perf_counter() - t1}s)')
trades = singlular_backtest()
    # print(f'calculated in {time.perf_counter() - t1}')
