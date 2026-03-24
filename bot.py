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

# ---------------- Flask ----------------
app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "OK"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

# ---------------- Token ----------------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
if not TOKEN:
    exit()

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

# ---------------- UTILS ----------------
def fmt(chips):
    if chips>=1_000_000: return f"{chips//1_000_000}M"
    if chips>=1_000: return f"{chips//1_000}K"
    return str(chips)

user_state = {}
games = {}

def get_user(uid):
    user = cursor.execute("SELECT chips,last_bonus,referred FROM users WHERE user_id=?", (uid,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?,?,?,?)", (uid, 5000, "", 0))
        conn.commit()
        return 5000, "", 0
    return user

def update(uid, chips):
    chips = max(0, chips)  # лимит сверху снят
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips, uid))
    conn.commit()

def daily_bonus(uid):
    chips, last, _ = get_user(uid)
    today = str(datetime.date.today())
    if last != today:
        chips += 5000
        update(uid, chips)
        cursor.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (today, uid))
        conn.commit()
        return True, chips
    return False, chips

# ---------------- MENU ----------------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 CRASH", callback_data="crash"),
         InlineKeyboardButton("🎰 SLOTS", callback_data="slots")],
        [InlineKeyboardButton("📈 Hi-Lo", callback_data="hilo"),
         InlineKeyboardButton("⚡ Double", callback_data="double")],
        [InlineKeyboardButton("🎡 Roulette", callback_data="roulette"),
         InlineKeyboardButton("📦 Кейсы", callback_data="cases")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton("👥 Рефералка", callback_data="ref"),
         InlineKeyboardButton("🏆 Топ", callback_data="top")]
    ])

def nav():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu")]])
# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    args = context.args

    # регистрация пользователя
    chips, last, referred = get_user(user_id)

    # рефералка
    if args:
        try:
            ref_id = int(args[0])
            if ref_id != user_id:
                r_chips, _, r_referred = get_user(ref_id)
                if r_referred == 0:
                    update(ref_id, r_chips + 15000)
                    cursor.execute("UPDATE users SET referred=1 WHERE user_id=?", (ref_id,))
                    conn.commit()
                    await context.bot.send_message(ref_id, "👥 Реферал пришёл! +15K фишек")
        except:
            pass

    # главное меню
    await update.message.reply_text(
        "🎰 Добро пожаловать в Казино Bot!\nВыберите игру ниже ⬇️",
        reply_markup=main_menu()
    )

# ---------------- PROFILE ----------------
async def profile_text(uid):
    chips, _, _ = get_user(uid)
    return f"👤 Профиль\n💰 {fmt(chips)} фишек"

# ---------------- BUTTON ----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # --- Главное меню ---
    if data == "menu":
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())

    elif data == "profile":
        await q.edit_message_text(await profile_text(uid), reply_markup=nav())

    elif data == "bonus":
        ok, chips = daily_bonus(uid)
        text = "🎁 +5000 фишек!" if ok else "❗ Уже получали сегодня"
        await q.edit_message_text(f"{text}\n💰 Сейчас: {fmt(chips)}", reply_markup=nav())

    elif data == "ref":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await q.edit_message_text(f"👥 Ваша реферальная ссылка:\n{link}", reply_markup=nav())

    elif data == "top":
        users = cursor.execute("SELECT user_id, chips FROM users ORDER BY chips DESC LIMIT 5").fetchall()
        text = "🏆 Топ игроков:\n"
        for i,u in enumerate(users,1):
            text += f"{i}. {u[0]} — {fmt(u[1])}\n"
        await q.edit_message_text(text, reply_markup=nav())

# ---------------- CRASH ----------------
async def crash_loop(context, uid):
    game = games.get(uid)
    if not game: return

    while True:
        await asyncio.sleep(1)
        if game.get("stop"): return
        game["mult"] = round(game["mult"] + random.uniform(0.1,0.5),2)
        # шанс краша
        if random.random() < 0.15:
            await game["msg"].edit_text(f"💥 КРАШ x{game['mult']}\n😢 Вы проиграли", reply_markup=main_menu())
            games.pop(uid)
            return
        await game["msg"].edit_text(
            f"🚀 x{game['mult']}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Забрать", callback_data="cashout")]])
        )

# ---------------- MESSAGE ----------------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.chat.id
    text = update.message.text
    state = user_state.get(uid)

    if state:
        # CRASH ставка
        if "crash" in state:
            try:
                bet = int(text)
            except:
                await update.message.reply_text("❗ Введите число")
                return
            chips, _, _ = get_user(uid)
            if bet>chips:
                await update.message.reply_text("❗ Недостаточно фишек")
                return
            update(uid, chips-bet)
            msg = await update.message.reply_text("🚀 x1.0")
            games[uid] = {"bet": bet, "mult": 1.0, "msg": msg}
            context.application.create_task(crash_loop(context, uid))
            user_state.pop(uid)
            return

        # Hi-Lo / Double / кейсы / другие игры аналогично можно добавить здесь

    await update.message.reply_text("❗ Используй кнопки", reply_markup=main_menu())

# ---------------- RUN ----------------
if __name__ == "__main__":
    threading.Thread(target=run_web).start()

    app = ApplicationBuilder().token(TOKEN).build()

    # Хэндлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))

    app.run_polling()
