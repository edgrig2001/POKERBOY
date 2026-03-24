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
if not TOKEN: exit()

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

# ---------------- GLOBALS ----------------
user_state = {}  # временные состояния для ввода
games = {}       # состояния для каждой игры, разделены по uid и игре

# ---------- USER ----------
def get_user(uid):
    user = cursor.execute("SELECT chips,last_bonus,referred FROM users WHERE user_id=?", (uid,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?,?,?,?)", (uid, 5000, "", 0))
        conn.commit()
        return 5000, "", 0
    return user

def update(uid, chips):
    chips = max(0, chips)   # баланс не может быть отрицательным, верхний лимит снят
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips, uid))
    conn.commit()

def daily_bonus(uid):
    chips, last, _ = get_user(uid)
    today = str(datetime.date.today())
    if last != today:
        chips += 5000
        chips = min(chips, 15000)
        cursor.execute("UPDATE users SET chips=?, last_bonus=? WHERE user_id=?", (chips, today, uid))
        conn.commit()
        return True, chips
    return False, chips

# ---------- MENU ----------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 CRASH", callback_data="crash"),
         InlineKeyboardButton("🎰 SLOTS", callback_data="slots")],
        [InlineKeyboardButton("📈 Hi-Lo", callback_data="hilo"),
         InlineKeyboardButton("⚡ Double", callback_data="double")],
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

    await update.message.reply_text("🎰 Казино Bot", reply_markup=main_menu())

# ---------- BUTTON ----------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # ---------- MENU ----------
    if data == "menu":
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())

    elif data == "profile":
        chips, _, _ = get_user(uid)
        await q.edit_message_text(f"👤 Профиль\n💰 {fmt(chips)}", reply_markup=nav())

    elif data == "bonus":
        ok, chips = daily_bonus(uid)
        text = "🎁 +5K!" if ok else "❗ Уже получали сегодня"
        await q.edit_message_text(f"{text}\n💰 Сейчас: {fmt(chips)}", reply_markup=nav())

    elif data == "ref":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await q.edit_message_text(f"👥 Приглашай друзей:\n{link}", reply_markup=nav())

    elif data == "top":
        users = cursor.execute("SELECT user_id, chips FROM users ORDER BY chips DESC LIMIT 5").fetchall()
        text = "🏆 Топ игроков:\n\n"
        for i,u in enumerate(users,1):
            text += f"{i}. {u[0]} — {fmt(u[1])}\n"
        await q.edit_message_text(text, reply_markup=nav())

# ---------- SLOTS ----------
    elif data == "slots":
        chips, _, _ = get_user(uid)
        bet = min(500, chips)
        symbols = ["🍒","🍋","⭐","💎"]
        weights = [55,25,15,5]
        r = random.choices(symbols, weights, k=3)

        if r[0]==r[1]==r[2]:
            mult = {"🍒":2,"⭐":3,"💎":5}.get(r[0],1)
            win = bet*mult
            update(uid, chips+win)
            text = f"{' '.join(r)}\n🎉 Вы выиграли {fmt(win)}!"
        elif len(set(r))==2:
            win = bet
            text = f"{' '.join(r)}\n🎉 Вы выиграли {fmt(win)}!"
        else:
            update(uid, chips-bet)
            text = f"{' '.join(r)}\n😢 -{fmt(bet)}"

        await q.edit_message_text(text, reply_markup=nav())

# ---------- CRASH ----------
    elif data == "crash":
        await q.edit_message_text("💰 Введите ставку:", reply_markup=nav())
        user_state[uid] = {"crash":True}

# ---------- CRASH CASHOUT ----------
    elif data == "cashout":
        g = games.get(uid, {}).get("crash")
        if not g: return
        g["stop"]=True
        win = int(g["bet"]*g["mult"])
        update(uid, get_user(uid)[0]+win)
        await g["msg"].edit_text(f"💸 Забрали x{round(g['mult'],2)}\n+{fmt(win)}", reply_markup=nav())
        games[uid].pop("crash",None)

# ---------- CRASH LOOP ----------
async def crash_loop(uid):
    g = games[uid]["crash"]
    while True:
        await asyncio.sleep(1)
        if g.get("stop"): return
        g["mult"] += random.uniform(0.2,0.7)
        if random.random() < 0.2:
            update(uid, get_user(uid)[0]-g["bet"])
            await g["msg"].edit_text(f"💥 КРАШ! x{round(g['mult'],2)}\nВы проиграли", reply_markup=nav())
            games[uid].pop("crash",None)
            return
        await g["msg"].edit_text(f"🚀 x{round(g['mult'],2)}",
                                 reply_markup=InlineKeyboardMarkup([
                                     [InlineKeyboardButton("💸 Забрать",callback_data="cashout")],
                                     [InlineKeyboardButton("🔙",callback_data="menu")]
                                 ]))

# ---------- MESSAGE ----------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.chat.id
    st = user_state.get(uid)
    txt = update.message.text

    # CRASH STAKE
    if st and "crash" in st:
        bet = int(txt)
        chips,_,_ = get_user(uid)
        if bet > chips:
            await update.message.reply_text("❗ Недостаточно фишек")
            return
        update(uid, chips-bet)
        msg = await update.message.reply_text("🚀 x1.0")
        if uid not in games: games[uid]={}
        games[uid]["crash"]={"bet":bet,"mult":1.0,"msg":msg,"stop":False}
        context.application.create_task(crash_loop(uid))
        user_state.pop(uid)
        return

    await update.message.reply_text("❗ Используйте кнопки", reply_markup=main_menu())

# ---------- RUN ----------
if __name__=="__main__":
    threading.Thread(target=run_web).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))
    app.run_polling()
    app.run_polling()
# ---------- HI-LO ----------
async def start_hilo(uid, q):
    chips,_,_ = get_user(uid)
    bet = min(500, chips)
    number = random.randint(1,10)
    if uid not in games: games[uid]={}
    games[uid]["hilo"] = {"bet":bet, "number":number}
    await q.edit_message_text(f"📈 Hi-Lo\n💰 Ставка: {fmt(bet)}\nСкажи, будет ли следующее число выше или ниже (H/L)?", reply_markup=nav())

# ---------- DOUBLE ----------
async def start_double(uid, q):
    chips,_,_ = get_user(uid)
    bet = min(500, chips)
    if uid not in games: games[uid]={}
    games[uid]["double"] = {"bet":bet}
    update(uid, chips-bet)
    await q.edit_message_text(f"⚡ Double\n💰 Ставка: {fmt(bet)}\nВыбери цвет: 🔴 или ⚫", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴", callback_data="double_red"),
         InlineKeyboardButton("⚫", callback_data="double_black")],
        [InlineKeyboardButton("🔙", callback_data="menu")]
    ]))

# ---------- DOUBLE CHOICE ----------
async def double_choice(uid, color, q):
    g = games[uid].get("double")
    if not g: return
    bet = g["bet"]
    win_color = random.choice(["red","black"])
    chips,_,_ = get_user(uid)
    if (color=="red" and win_color=="red") or (color=="black" and win_color=="black"):
        chips += bet*2
        text = f"🎉 Вы выиграли! x2 = {fmt(bet*2)}"
    else:
        text = f"😢 Вы проиграли -{fmt(bet)}"
    update(uid, chips)
    games[uid].pop("double",None)
    await q.edit_message_text(text, reply_markup=nav())

# ---------- ROULETTE ----------
async def start_roulette(uid, q):
    chips,_,_ = get_user(uid)
    bet = min(500, chips)
    number = random.randint(0,36)
    if uid not in games: games[uid]={}
    games[uid]["roulette"] = {"bet":bet, "number":number}
    await q.edit_message_text(f"🎡 Roulette\n💰 Ставка: {fmt(bet)}\nВыберите число 0-36:", reply_markup=nav())
    # для упрощения ставим случайное число за игрока при нажатии кнопки в сообщении

# ---------- CASES ----------
async def open_case(uid, q):
    chips,_,_ = get_user(uid)
    cost = min(500, chips)
    update(uid, chips-cost)
    items = [
        ("Common", 2),    # 2x
        ("Rare", 5),      # 5x
        ("Epic", 10),     # 10x
        ("Legendary", 20) # 20x
    ]
    weights = [60,25,10,5]
    choice = random.choices(items, weights=weights)[0]
    win = cost*choice[1]
    update(uid, get_user(uid)[0]+win)
    await q.edit_message_text(f"📦 Открыл кейс: {choice[0]}\n🎉 Вы выиграли {fmt(win)}", reply_markup=nav())

# ---------- BUTTON EXTENSION ----------
async def button_ext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # HI-LO
    if data == "hilo":
        await start_hilo(uid, q)
    elif data in ["hilo_h","hilo_l"]:
        g = games[uid].get("hilo")
        if not g: return
        number = g["number"]
        next_number = random.randint(1,10)
        chips,_,_ = get_user(uid)
        bet = g["bet"]
        if (data=="hilo_h" and next_number>number) or (data=="hilo_l" and next_number<number):
            chips += bet*2
            text = f"🎉 Следующее число: {next_number}\nВы выиграли {fmt(bet*2)}!"
        else:
            chips -= bet
            text = f"😢 Следующее число: {next_number}\nВы проиграли {fmt(bet)}"
        update(uid, chips)
        games[uid].pop("hilo",None)
        await q.edit_message_text(text, reply_markup=nav())

    # DOUBLE
    elif data == "double":
        await start_double(uid, q)
    elif data in ["double_red","double_black"]:
        color = "red" if data=="double_red" else "black"
        await double_choice(uid, color, q)

    # ROULETTE
    elif data == "roulette":
        await start_roulette(uid, q)

    # CASES
    elif data == "cases":
        await open_case(uid, q)
