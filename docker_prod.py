from binance import AsyncClient, Client
from dotenv import load_dotenv
import pandas as pd
import asyncio
import os
import time
import logging
import sys
import subprocess
from flask import Flask, Response, request, send_file
from threading import Thread

load_dotenv()
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')
USERNAME = os.getenv('DASHBOARD_USERNAME')
PASSWORD = os.getenv('DASHBOARD_PASSWORD')

app = Flask(__name__)
IMAGE_NAME = "docker_prod"


def get_container_name_from_image(image_name):
    """Find the name of a running container by image name."""
    try:
        output = subprocess.check_output(
            ["docker", "ps", "--filter", f"ancestor={image_name}", "--format", "{{.Names}}"],
            stderr=subprocess.STDOUT
        )
        names = output.decode().strip().splitlines()
        return names[0] if names else None
    except subprocess.CalledProcessError as e:
        return None


# Start Flask in a separate thread
def run_flask():
    app.run(host="0.0.0.0", port=5000)



@app.before_request
def check_auth():
    if not USERNAME or not PASSWORD:
        logger.warning(
            "DASHBOARD_USERNAME/DASHBOARD_PASSWORD not set — dashboard auth is DISABLED. "
            "Set both in your .env before exposing this service."
        )
        return
    auth = request.authorization
    if not auth or not (auth.username == USERNAME and auth.password == PASSWORD):
        return Response("Unauthorized", 401, {"WWW-Authenticate": "Basic realm='Login Required'"})

@app.route("/")
def dashboard():
    try:
        return send_file("dashboard.html")
    except Exception as e:
        return f"Error loading dashboard: {str(e)}", 500


@app.route("/logs", methods=["GET"])
def get_logs():
    container_name = get_container_name_from_image(IMAGE_NAME)
    if not container_name:
        return f"No running container found for image '{IMAGE_NAME}'", 404
    try:
        logs = subprocess.check_output(
            ["docker", "logs", "--tail", "100", container_name],
            stderr=subprocess.STDOUT
        )
        return Response(logs, mimetype="text/plain")
    except Exception as e:
        return f"Internal server error: {e}", 500


@app.route("/restart", methods=["POST"])
def restart_container():
    container_name = get_container_name_from_image(IMAGE_NAME)
    if not container_name:
        return f"No running container found for image '{IMAGE_NAME}'", 404
    try:
        subprocess.check_call(["docker", "restart", container_name])
        return f"Container '{container_name}' restarted successfully."
    except subprocess.CalledProcessError as e:
        return f"Failed to restart container: {e.output.decode()}", 500


# 1) Configure the root logger once
logging.basicConfig(
    level=logging.INFO,  # minimum level to capture
    format="%(asctime)s %(levelname)-8s %(name)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),  # echo to console
    ]
)


# 2) Grab a logger in each module or function
logger = logging.getLogger(__name__)

open_positions_result = []
open_orders_result = []

runner_lock = asyncio.Lock()
last_order_run = time.monotonic()

df = pd.DataFrame()
state = {
    'lower_bound': None,
    'SMA_price': None,
    'upper_bound': None,
    'macd_signal': None,
    'last_position_amount': 0,
    'trading symbol': 'ETHUSDC',
    'leverage': 5,
    'boll window': 27,
    'boll std': 1.7,
    'macd_threshold': 0.0019,
    'usdc_balance': 0,
}


async def safe_order_handler(client):
    global last_order_run
    # logger.info('awaiting runner lock to handle orders')
    async with runner_lock:
        await order_handler(client)
        last_order_run = time.monotonic()


def ema(df, window):
    a = 2 / (1 + window)
    ema = df['close'].iloc[:window].mean()
    padded_close = df['close'].copy()
    padded_close.iloc[:window - 1] = pd.NA
    padded_close.iloc[window - 1] = ema
    return padded_close.ewm(alpha=a, adjust=False).mean()


def boll_mean_reversion_strategy():
    global state
    df['SMA'] = df['close'].rolling(window=state['boll window']).mean()
    df['STD'] = df['close'].rolling(window=state['boll window']).std(ddof=0)
    df[f'EMA(12)'] = ema(df, 12)
    df[f'EMA(26)'] = ema(df, 26)

    last_row = df.iloc[-1]
    state['SMA_price'] = round(last_row['SMA'], 1)
    state['upper_bound'] = round(last_row['SMA'] + (state['boll std'] * last_row['STD']), 1)
    state['lower_bound'] = round(last_row['SMA'] - (state['boll std'] * last_row['STD']), 1)

    def signal_ema(df, window):
        a = 2 / (1 + window)
        ema = df['MACD_LINE'].iloc[:window].mean()
        padded_close = df['MACD_LINE'].copy()
        padded_close.iloc[:window - 1] = pd.NA
        padded_close.iloc[window - 1] = ema
        return padded_close.ewm(alpha=a, adjust=False).mean().iloc[-1]

    df['MACD_LINE'] = df[f'EMA(12)'] - df[f'EMA(26)']
    state['macd_signal'] = signal_ema(df, 9)

    return


def store_kline_to_df(klines):
    global df  # df[4]: close df[0]: start time

    df = pd.DataFrame([[
        k[0],  # start_time
        float(k[4]),  # current/close
    ] for k in klines], columns=[
        'start_time', 'close'
    ])


async def retrieve_open_orders(client, trading_symbol):
    global open_orders_result
    try:
        open_orders = await client.futures_get_open_orders(symbol=trading_symbol)
    except Exception as e:
        logger.error(f'error retrieving open orders {e}')
        restart_container()
    # logger.info(f'Open orders retrieved {open_orders}')
    open_orders_result = open_orders
    pass


async def retrieve_open_positions(client, trading_symbol):
    global state
    try:
        open_positions = await client.futures_position_information(symbol=trading_symbol)
    except Exception as e:
        logger.error(f'error retrieving open positions {e}')
        restart_container()
    # logger.info(f'Open positions retrieved {open_positions}')
    if open_positions:
        state['last_position_amount'] = float(open_positions[0]['positionAmt'])
    else:
        state['last_position_amount'] = 0
    pass


async def cancel_order(client, trading_symbol, orderId):
    try:
        response = await client.futures_cancel_order(symbol=trading_symbol, orderId=orderId)
        # logger.info(f"Order cancelled: {trading_symbol}, {type(orderId)}, {orderId}")
        return response
    except Exception as e:
        logger.error(f'unhandled error while cancelling orders: {e}')
        restart_container()
    pass


async def periodic_order_runner(client):
    global last_order_run
    interval = 90
    # skip until we have data
    while state['lower_bound'] is None:
        await asyncio.sleep(1)

    while True:
        # wait for 20s since the last run (immediate or periodic)
        to_wait = interval - (time.monotonic() - last_order_run)
        if to_wait > 0:
            await asyncio.sleep(to_wait)

        # fire with the latest state
        logger.info('entered periodicc order runner')
        await safe_order_handler(client)


async def retrieve_usdc_balance(client):
    balances = await client.futures_account_balance()
    state['usdc_balance'] = float(balances[-1]['balance'])


async def place_order(client, price, side, order_quantity):
    while True:
        try:
            # logger.info(f'placing {side} order, price: {price}, quantity: {order_quantity}')
            order = await client.futures_create_order(
                symbol=state['trading symbol'],
                side=side,
                type='LIMIT',
                quantity=order_quantity,
                price=price,
                timeInForce='GTX'
            )
            logger.info(f"Order placed: {order}")
            order_id = order['orderId']

            return order_id


        except Exception as e:
            if e.code == -5022:
                logger.error("Post-Only order rejected (would have matched as taker). Skipping...")
                book = await client.futures_order_book(symbol=state['trading symbol'])
                if side == 'BUY':
                    price = float(book['bids'][0][0])
                elif side == 'SELL':
                    price = float(book['asks'][0][0])
            else:
                logger.error(f'order_qty: {order_quantity}, price: {price}, side: {side}')
                logger.error(f"Unhandled Binance API error: {e}")
                restart_container()
    pass


async def order_handler(client):
    logger.info(f'entered order handler..., retrieving open positions/orders')
    try:
        await asyncio.gather(
            retrieve_open_positions(client, state['trading symbol']),
            retrieve_open_orders(client, state['trading symbol']),
            retrieve_usdc_balance(client),
        )
    except Exception as e:
        logger.error(f"unhandled error on attempt {e}")
        restart_container()

    if open_orders_result:
        # logger.info('Cancelling open orders')
        for o in open_orders_result:
            await cancel_order(client, state['trading symbol'], o['orderId'])
            # logger.info(f'Open orders cancelled')

    await client.futures_change_leverage(symbol=state['trading symbol'], leverage=state['leverage'])

    open_quantity = state['last_position_amount']

    if open_quantity == 0 and state['macd_signal'] >= (state['SMA_price'] * state['macd_threshold']):
        order_quantity = state['usdc_balance'] * state['leverage'] / state['lower_bound']
        order_quantity = abs(round(order_quantity * 0.99, 3))
        await place_order(client, price=state["lower_bound"], side='BUY', order_quantity=order_quantity)
    elif open_quantity == 0 and state['macd_signal'] <= -(state['SMA_price'] * state['macd_threshold']):
        order_quantity = state['usdc_balance'] * state['leverage'] / state['upper_bound']
        order_quantity = abs(round(order_quantity * 0.99, 3))
        await place_order(client, price=state["upper_bound"], side='SELL', order_quantity=order_quantity)

    elif open_quantity > 0:
        # logger.info(f'position is long, position = ({open_quantity}), placing sell order @{state["SMA_price"]}')
        await place_order(client, price=state['upper_bound'], side='SELL', order_quantity=abs(open_quantity))
        # logger.info(f'sell order placed following long position')

    elif open_quantity < 0:
        # logger.info(f'position is short, position = ({open_quantity}), placing buy order @{state["SMA_price"]}')
        await place_order(client, price=state["lower_bound"], side='BUY', order_quantity=abs(open_quantity))
        # logger.info(f'buy order placed following short position')
    pass


async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)

    asyncio.create_task(periodic_order_runner(client))

    last_position_amount = 0
    first_run = True

    while True:

        if int(client.response.headers["x-mbx-used-weight-1m"]) > 2000:
            await asyncio.sleep(60)

        try:
            klines, x, y = await asyncio.gather(
                client.futures_klines(
                    symbol=state['trading symbol'],
                    interval=Client.KLINE_INTERVAL_1HOUR,
                    limit=300
                ),
                retrieve_open_positions(client, state['trading symbol']),
                retrieve_open_orders(client, state['trading symbol']),
            )
        except Exception as e:
            logger.error(f'error retrieving klines and open positions {e}')
            restart_container()

        # logger.info(f'Api usage: {int(client.response.headers["x-mbx-used-weight-1m"])}')

        if klines:
            await asyncio.sleep(0.5)
            store_kline_to_df(klines)
            boll_mean_reversion_strategy()

            def kick_off_immediate_run():
                # schedule it, but don’t await here
                asyncio.create_task(safe_order_handler(client))

            if first_run:
                kick_off_immediate_run()
                first_run = False

            # Inside the loop
            if state['last_position_amount'] != last_position_amount:
                await asyncio.sleep(5)
                logger.info(
                    f'position change detected, running order_handler immediately, position amount: {state["last_position_amount"]}')
                kick_off_immediate_run()

            last_position_amount = state['last_position_amount']


if __name__ == '__main__':
    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    asyncio.run(main())
