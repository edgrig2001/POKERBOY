# === УЛУЧШЕННЫЙ КАЗИНО БОТ ===

import os
import sqlite3
import threading
import random
import datetime
import asyncio
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)

# ---------- FORMAT ----------
def fmt(n):
    if n >= 1_000_000:
        return f"{n//1_000_000}M"
    if n >= 1000:
        return f"{n//1000}K"
    return str(n)

# ---------------- Flask ----------------
app_web = Flask(__name__)
@app_web.route("/")
def home():
    return "OK"

def run_web():
    app_web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

TOKEN = os.environ.get("TELEGRAM_TOKEN")

# ---------------- DB ----------------
conn = sqlite3.connect("casino.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    chips INTEGER,
    last_bonus TEXT,
    referred INTEGER DEFAULT 0
)
""")
conn.commit()

user_state = {}
games = {}

# ---------- USER ----------
def get_user(uid):
    user = cursor.execute("SELECT chips,last_bonus,referred FROM users WHERE user_id=?", (uid,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?,?,?,?)", (uid, 5000, "", 0))
        conn.commit()
        return 5000, "", 0
    return user

def update(uid, chips):
    chips = max(0, min(chips, 15000))
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips, uid))
    conn.commit()

# ---------- MENU ----------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 CRASH", callback_data="crash"),
         InlineKeyboardButton("🎰 SLOTS", callback_data="slots")],
        [InlineKeyboardButton("📈 Hi-Lo", callback_data="hilo"),
         InlineKeyboardButton("🎯 Roulette", callback_data="roulette")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton("👥 Рефералка", callback_data="ref"),
         InlineKeyboardButton("🏆 Топ", callback_data="top")]
    ])

def nav():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад", callback_data="menu"),
         InlineKeyboardButton("🏠 Меню", callback_data="menu")]
    ])

# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.chat.id
    args = context.args

    if args:
        try:
            ref = int(args[0])
            if ref != uid:
                chips, _, referred = get_user(ref)
                if not referred:
                    update(ref, chips + 15000)
                    cursor.execute("UPDATE users SET referred=1 WHERE user_id=?", (ref,))
                    conn.commit()
                    await context.bot.send_message(ref, "🎉 Вам начислено 15K за друга!")
        except:
            pass

    await update.message.reply_text("🎰 Казино", reply_markup=main_menu())

# ---------- BUTTON ----------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # --- меню ---
    if data == "menu":
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())

    elif data == "profile":
        chips,_,_ = get_user(uid)
        await q.edit_message_text(f"👤 Профиль\n💰 {fmt(chips)}", reply_markup=nav())

    elif data == "ref":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await q.edit_message_text(f"👥 Приглашай:\n{link}", reply_markup=nav())

    # ---------- SLOTS ----------
    elif data == "slots":
        chips,_,_ = get_user(uid)
        bet = min(500, chips)

        symbols = ["🍒","🍋","⭐","💎"]
        weights = [55,25,15,5]
        r = random.choices(symbols, weights, k=3)

        if r[0]==r[1]==r[2]:
            mult = {"🍒":2,"⭐":3,"💎":5}.get(r[0],1)
            win = bet*mult
            update(uid, chips+win)
            text=f"{' '.join(r)}\n🎉 Вы выиграли {fmt(win)}!"
        elif len(set(r))==2:
            update(uid, chips)
            text=f"{' '.join(r)}\n🎉 Вы выиграли {fmt(bet)}!"
        else:
            update(uid, chips-bet)
            text=f"{' '.join(r)}\n😢 -{fmt(bet)}"

        await q.edit_message_text(text, reply_markup=nav())

    # ---------- CRASH ----------
    elif data == "crash":
        await q.edit_message_text("💰 Введи ставку", reply_markup=nav())
        user_state[uid] = {"crash":True}

    elif data == "cash":
        g = games.get(uid)
        if not g: return
        g["stop"]=True

        win=int(g["bet"]*g["mult"])
        update(uid, get_user(uid)[0]+win)

        await g["msg"].edit_text(f"💸 Забрал x{round(g['mult'],2)}\n+{fmt(win)}", reply_markup=nav())
        games.pop(uid)

    # ---------- HILO ----------
    elif data == "hilo":
        card=random.randint(2,14)
        games[uid]={"card":card,"bet":500,"mult":1}
        await q.edit_message_text(
            f"📈 Карта: {card}\nВыше или ниже?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬆️ Выше",callback_data="hi"),
                 InlineKeyboardButton("⬇️ Ниже",callback_data="lo")],
                [InlineKeyboardButton("💰 Забрать",callback_data="take")],
                [InlineKeyboardButton("🔙",callback_data="menu")]
            ])
        )

    elif data in ["hi","lo"]:
        g=games[uid]
        new=random.randint(2,14)

        win=False
        if data=="hi" and new>g["card"]: win=True
        if data=="lo" and new<g["card"]: win=True

        if win:
            g["card"]=new
            g["mult"]+=0.5
            await q.edit_message_text(f"📈 {new}\nx{g['mult']}",reply_markup=q.message.reply_markup)
        else:
            update(uid, get_user(uid)[0]-g["bet"])
            await q.edit_message_text(f"💀 {new}\nВы проиграли",reply_markup=nav())
            games.pop(uid)

    elif data=="take":
        g=games[uid]
        win=int(g["bet"]*g["mult"])
        update(uid,get_user(uid)[0]+win)
        await q.edit_message_text(f"💰 +{fmt(win)}",reply_markup=nav())
        games.pop(uid)

    # ---------- ROULETTE ----------
    elif data=="roulette":
        await q.edit_message_text(
            "🎯 Выбери цвет",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴",callback_data="red"),
                 InlineKeyboardButton("⚫",callback_data="black")],
                [InlineKeyboardButton("🟢",callback_data="zero")],
                [InlineKeyboardButton("🔙",callback_data="menu")]
            ])
        )

    elif data in ["red","black","zero"]:
        roll=random.choice(["red","black","zero"])
        chips,_ ,_=get_user(uid)

        if data==roll:
            mult=2 if data!="zero" else 10
            win=500*mult
            update(uid,chips+win)
            text=f"🎉 Выпало {roll}\n+{fmt(win)}"
        else:
            update(uid,chips-500)
            text=f"😢 Выпало {roll}\n-500"

        await q.edit_message_text(text,reply_markup=nav())

# ---------- CRASH LOOP ----------
async def crash_loop(uid):
    g=games[uid]
    while True:
        await asyncio.sleep(1)
        if g.get("stop"): return

        g["mult"]+=random.uniform(0.2,0.7)

        if random.random()<0.2:
            await g["msg"].edit_text(f"💥 КРАШ x{round(g['mult'],2)}",reply_markup=nav())
            games.pop(uid)
            return

        await g["msg"].edit_text(
            f"🚀 x{round(g['mult'],2)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 Забрать",callback_data="cash")],
                [InlineKeyboardButton("🔙",callback_data="menu")]
            ])
        )

# ---------- MESSAGE ----------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.message.chat.id
    txt=update.message.text
    st=user_state.get(uid)

    if st and "crash" in st:
        bet=int(txt)
        chips,_ ,_=get_user(uid)

        update(uid,chips-bet)
        msg=await update.message.reply_text("🚀 x1.0")

        games[uid]={"bet":bet,"mult":1,"msg":msg}
        context.application.create_task(crash_loop(uid))

        user_state.pop(uid)
        return

    await update.message.reply_text("❗ Используй кнопки",reply_markup=main_menu())

# ---------- RUN ----------
if __name__ == "__main__":
    threading.Thread(target=run_web).start()

    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND),message))

    app.run_polling()
