import os
import time
import logging
import pandas as pd

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

from pybit.unified_trading import HTTP
from pybit.exceptions import FailedRequestError
import ta

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

TRADE_USDT = float(os.getenv("TRADE_USDT", 10))
LEVERAGE = int(os.getenv("LEVERAGE", 5))

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)

# ================== BYBIT CLIENT ==================
session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# ================== RATE LIMIT + CACHE ==================
LAST_CALL = 0
CACHE = {}
CACHE_TTL = 30  # seconds

# ================== MENU ==================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Market Analysis", callback_data="scan")],
        [InlineKeyboardButton("📈 Open Auto Trade", callback_data="trade")],
        [InlineKeyboardButton("📉 Close Position", callback_data="close")],
        [InlineKeyboardButton("ℹ️ Bot Status", callback_data="status")]
    ])

def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")]
    ])

# ================== STRATEGY (SAFE) ==================
def analyze_symbol(symbol):
    global LAST_CALL

    now = time.time()

    # Cache hit
    if symbol in CACHE and now - CACHE[symbol]["time"] < CACHE_TTL:
        return CACHE[symbol]["score"], CACHE[symbol]["price"]

    # Rate limit protection
    if now - LAST_CALL < 1.2:
        time.sleep(1.2)

    LAST_CALL = time.time()

    try:
        k = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=5,
            limit=50
        )
    except FailedRequestError:
        return 0, 0

    df = pd.DataFrame(k["result"]["list"])
    df = df.iloc[::-1]
    df.columns = ["time","open","high","low","close","volume","turnover"]
    df["close"] = df["close"].astype(float)

    df["rsi"] = ta.momentum.RSIIndicator(df["close"]).rsi()
    df["ema_fast"] = ta.trend.EMAIndicator(df["close"], 9).ema_indicator()
    df["ema_slow"] = ta.trend.EMAIndicator(df["close"], 21).ema_indicator()

    latest = df.iloc[-1]

    score = 0
    if latest["ema_fast"] > latest["ema_slow"]:
        score += 1
    if latest["rsi"] < 70:
        score += 1

    CACHE[symbol] = {
        "score": score,
        "price": latest["close"],
        "time": now
    }

    return score, latest["close"]

# ================== TELEGRAM HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bybit Futures Trading Bot\nChoose an option:",
        reply_markup=main_menu()
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Main Menu:", reply_markup=main_menu())

async def scan_market(update: Update, context):
    query = update.callback_query
    await query.answer()

    try:
        best = None
        best_score = -1

        for sym in SYMBOLS:
            score, _ = analyze_symbol(sym)
            if score > best_score:
                best_score = score
                best = sym

        if not best:
            raise Exception("No data")

        await query.edit_message_text(
            f"📊 Best Setup Found:\n\n🔹 {best}\nScore: {best_score}",
            reply_markup=back_menu()
        )

    except Exception:
        await query.edit_message_text(
            "⚠️ Bybit rate limit reached.\nPlease wait 30 seconds.",
            reply_markup=back_menu()
        )

async def open_trade(update: Update, context):
    query = update.callback_query
    await query.answer()

    try:
        best = None
        best_score = -1

        for sym in SYMBOLS:
            score, _ = analyze_symbol(sym)
            if score > best_score:
                best_score = score
                best = sym

        if not best:
            raise Exception("No trade")

        session.set_leverage(
            category="linear",
            symbol=best,
            buyLeverage=LEVERAGE,
            sellLeverage=LEVERAGE
        )

        session.place_order(
            category="linear",
            symbol=best,
            side="Buy",
            orderType="Market",
            qty=TRADE_USDT,
            timeInForce="GoodTillCancel"
        )

        await query.edit_message_text(
            f"📈 Trade Opened on {best}",
            reply_markup=back_menu()
        )

    except Exception:
        await query.edit_message_text(
            "⚠️ Trade failed (rate limit / API block).\nTry again later.",
            reply_markup=back_menu()
        )

async def close_trade(update: Update, context):
    query = update.callback_query
    await query.answer()

    try:
        for sym in SYMBOLS:
            session.place_order(
                category="linear",
                symbol=sym,
                side="Sell",
                orderType="Market",
                qty=TRADE_USDT,
                reduceOnly=True
            )

        await query.edit_message_text(
            "📉 All positions closed",
            reply_markup=back_menu()
        )

    except Exception:
        await query.edit_message_text(
            "⚠️ Close failed due to rate limit.",
            reply_markup=back_menu()
        )

async def status(update: Update, context):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "✅ Bot is running\n📡 Connected to Bybit",
        reply_markup=back_menu()
    )

# ================== GLOBAL ERROR HANDLER ==================
async def error_handler(update, context):
    logging.error("Telegram error", exc_info=context.error)

# ================== RUN ==================
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(menu, pattern="menu"))
app.add_handler(CallbackQueryHandler(scan_market, pattern="scan"))
app.add_handler(CallbackQueryHandler(open_trade, pattern="trade"))
app.add_handler(CallbackQueryHandler(close_trade, pattern="close"))
app.add_handler(CallbackQueryHandler(status, pattern="status"))

app.add_error_handler(error_handler)

app.run_polling()
