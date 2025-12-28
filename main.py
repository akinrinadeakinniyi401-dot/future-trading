import os
import time
import logging
import numpy as np
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
    k = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=5,
        limit=100
    )

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

    best = None
    best_score = -1

    for sym in SYMBOLS:
        score, price = analyze_symbol(sym)
        if score > best_score:
            best_score = score
            best = sym

    await query.edit_message_text(
        f"📊 Best Setup Found:\n\n🔹 {best}\nScore: {best_score}",
        reply_markup=back_menu()
    )

async def open_trade(update: Update, context):
    query = update.callback_query
    await query.answer()

    best = None
    best_score = -1

    for sym in SYMBOLS:
        score, price = analyze_symbol(sym)
        if score > best_score:
            best_score = score
            best = sym

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

async def close_trade(update: Update, context):
    query = update.callback_query
    await query.answer()

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

async def status(update: Update, context):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "✅ Bot is running\n📡 Connected to Bybit",
        reply_markup=back_menu()
    )

# ================== RUN ==================
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(menu, pattern="menu"))
app.add_handler(CallbackQueryHandler(scan_market, pattern="scan"))
app.add_handler(CallbackQueryHandler(open_trade, pattern="trade"))
app.add_handler(CallbackQueryHandler(close_trade, pattern="close"))
app.add_handler(CallbackQueryHandler(status, pattern="status"))

app.run_polling()
