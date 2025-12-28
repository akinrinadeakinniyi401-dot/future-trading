import os
import time
import logging
import pandas as pd

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

from pybit.unified_trading import HTTP
from pybit.exceptions import FailedRequestError
import ta

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

TESTNET = False  # 🔴 REAL MONEY CONFIRMED

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
CACHE_TTL = 30

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

# ================== STRATEGY ==================
def analyze_symbol(symbol):
    global LAST_CALL
    now = time.time()

    if symbol in CACHE and now - CACHE[symbol]["time"] < CACHE_TTL:
        return CACHE[symbol]

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
        return None

    df = pd.DataFrame(k["result"]["list"])
    df = df.iloc[::-1]
    df.columns = ["time","open","high","low","close","volume","turnover"]
    df["close"] = df["close"].astype(float)

    df["ema_fast"] = ta.trend.EMAIndicator(df["close"], 9).ema_indicator()
    df["ema_slow"] = ta.trend.EMAIndicator(df["close"], 21).ema_indicator()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"]).rsi()

    latest = df.iloc[-1]

    trend_ok = latest["ema_fast"] > latest["ema_slow"]
    rsi_ok = 45 < latest["rsi"] < 65

    score = int(trend_ok) + int(rsi_ok)

    data = {
        "score": score,
        "price": latest["close"],
        "trend_ok": trend_ok,
        "rsi_ok": rsi_ok,
        "time": now
    }

    CACHE[symbol] = data
    return data

# ================== CHECK OPEN POSITION ==================
def has_open_position(symbol):
    pos = session.get_positions(category="linear", symbol=symbol)
    for p in pos["result"]["list"]:
        if float(p["size"]) > 0:
            return True
    return False

# ================== TELEGRAM HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bybit Futures Trading Bot (LIVE)\nChoose an option:",
        reply_markup=main_menu()
    )

async def menu(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Main Menu:", reply_markup=main_menu())

async def scan_market(update: Update, context):
    query = update.callback_query
    await query.answer()

    best = None
    best_score = -1

    for sym in SYMBOLS:
        data = analyze_symbol(sym)
        if not data:
            continue
        if data["score"] > best_score:
            best_score = data["score"]
            best = sym

    if not best:
        await query.edit_message_text(
            "⚠️ No safe setup found.",
            reply_markup=back_menu()
        )
        return

    await query.edit_message_text(
        f"📊 Best Setup\n\n"
        f"🔹 {best}\n"
        f"Score: {best_score}",
        reply_markup=back_menu()
    )

async def open_trade(update: Update, context):
    query = update.callback_query
    await query.answer()

    try:
        best = None
        best_data = None

        for sym in SYMBOLS:
            data = analyze_symbol(sym)
            if data and data["score"] == 2:
                best = sym
                best_data = data
                break

        if not best:
            raise Exception("No valid entry")

        if has_open_position(best):
            await query.edit_message_text(
                f"⚠️ Position already open on {best}",
                reply_markup=back_menu()
            )
            return

        price = best_data["price"]
        qty = round((TRADE_USDT * LEVERAGE) / price, 3)

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
            qty=qty
        )

        await query.edit_message_text(
            f"📈 LONG opened on {best}\n"
            f"Qty: {qty}\nLeverage: {LEVERAGE}x",
            reply_markup=back_menu()
        )

    except Exception:
        await query.edit_message_text(
            "❌ Entry failed (rate limit or unsafe market).",
            reply_markup=back_menu()
        )

async def close_trade(update: Update, context):
    query = update.callback_query
    await query.answer()

    try:
        for sym in SYMBOLS:
            if has_open_position(sym):
                session.place_order(
                    category="linear",
                    symbol=sym,
                    side="Sell",
                    orderType="Market",
                    qty=1,
                    reduceOnly=True
                )

        await query.edit_message_text(
            "📉 Positions closed",
            reply_markup=back_menu()
        )
    except Exception:
        await query.edit_message_text(
            "⚠️ Failed to close positions.",
            reply_markup=back_menu()
        )

async def status(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✅ Bot running\n💰 LIVE trading enabled",
        reply_markup=back_menu()
    )

# ================== ERROR HANDLER ==================
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
