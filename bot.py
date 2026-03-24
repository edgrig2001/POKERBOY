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

user_state = {}
games = {}

# ---------------- USER ----------------
def get_user(user_id):
    user = cursor.execute("SELECT chips,last_bonus,referred FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?,?,?,?)", (user_id, 5000, "", 0))
        conn.commit()
        return 5000, "", 0
    return user

def update_chips(user_id, chips):
    chips = max(0, min(chips, 15000))
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips, user_id))
    conn.commit()

def daily_bonus(user_id):
    chips, last, _ = get_user(user_id)
    today = str(datetime.date.today())
    if last != today:
        chips += 5000
        chips = min(chips, 15000)
        cursor.execute("UPDATE users SET chips=?, last_bonus=? WHERE user_id=?", (chips, today, user_id))
        conn.commit()
        return True, chips
    return False, chips

# ---------------- MENU ----------------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 CRASH", callback_data="crash")],
        [InlineKeyboardButton("🎰 SLOTS", callback_data="slots")],
        [InlineKeyboardButton("🎲 Мини-игра", callback_data="mini")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton("👥 Рефералка", callback_data="ref")],
        [InlineKeyboardButton("🏆 Топ", callback_data="top")]
    ])

def back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu")]])

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    args = context.args

    if args:
        try:
            ref_id = int(args[0])
            if ref_id != user_id:
                chips, _, referred = get_user(ref_id)
                if not referred:
                    update_chips(ref_id, chips + 15000)
                    cursor.execute("UPDATE users SET referred=1 WHERE user_id=?", (ref_id,))
                    conn.commit()
        except:
            pass

    await update.message.reply_text("🎰 Казино Bot", reply_markup=main_menu())
# ---------------- BUTTON ----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    data = q.data

    if data == "menu":
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())

    elif data == "profile":
        chips, _, _ = get_user(user_id)
        await q.edit_message_text(f"👤 Профиль\n💰 {chips}", reply_markup=back())

    elif data == "bonus":
        ok, chips = daily_bonus(user_id)
        await q.edit_message_text(f"{'🎁 +5000' if ok else '❗ Уже получали'}\n💰 {chips}", reply_markup=back())

    elif data == "ref":
        link = f"https://t.me/{context.bot.username}?start={user_id}"
        await q.edit_message_text(f"👥 Ссылка:\n{link}", reply_markup=back())

    elif data == "top":
        users = cursor.execute("SELECT user_id, chips FROM users ORDER BY chips DESC LIMIT 5").fetchall()
        text = "🏆 Топ игроков:\n\n"
        for i,u in enumerate(users,1):
            text += f"{i}. {u[0]} — {u[1]}\n"
        await q.edit_message_text(text, reply_markup=back())

    # ================= CRASH =================
    elif data == "crash":
        await q.edit_message_text("💰 Введите ставку:", reply_markup=back())
        user_state[user_id] = {"crash": True}

    elif data == "cashout":
        game = games.get(user_id)
        if not game: return

        game["stop"] = True
        mult = game["mult"]

        win = int(game["bet"] * mult)
        update_chips(user_id, get_user(user_id)[0] + win)

        await game["msg"].edit_text(f"💸 Забрали x{mult}\n+{win}", reply_markup=main_menu())
        games.pop(user_id)

    # ================= SLOTS =================
    elif data == "slots":
        chips, _, _ = get_user(user_id)
        bet = min(500, chips)

        symbols = ["🍒","🍋","⭐","💎"]
        weights = [55,25,15,5]

        result = random.choices(symbols, weights, k=3)

        if result[0]==result[1]==result[2]:
            mult = {"🍒":2,"⭐":3,"💎":5}.get(result[0],1)
            win = bet * mult
            update_chips(user_id, chips + win)
            text = f"{' '.join(result)}\n🎉 x{mult} (+{win})"
        elif len(set(result))==2:
            update_chips(user_id, chips)
            text = f"{' '.join(result)}\n😐 Возврат"
        else:
            update_chips(user_id, chips - bet)
            text = f"{' '.join(result)}\n😢 -{bet}"

        await q.edit_message_text(text, reply_markup=main_menu())

    # ================= MINI =================
    elif data == "mini":
        kb = [[InlineKeyboardButton(str(i), callback_data=f"mini_{i}")] for i in range(1,16)]
        await q.edit_message_text("🎲 Выбери число", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("mini_"):
        num = int(data.split("_")[1])
        user_state[user_id] = {"mini": num}
        await q.edit_message_text(f"💰 Ставка на {num}?", reply_markup=back())

# ---------------- CRASH LOOP ----------------
async def crash_loop(context, user_id):
    game = games[user_id]

    while True:
        await asyncio.sleep(1)

        if game.get("stop"):
            return

        game["mult"] += round(random.uniform(0.2,0.8),2)

        if random.random() < 0.2:
            await game["msg"].edit_text(f"💥 КРАШ на x{game['mult']}\nВы проиграли", reply_markup=main_menu())
            games.pop(user_id)
            return

        await game["msg"].edit_text(
            f"🚀 x{round(game['mult'],2)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Забрать", callback_data="cashout")]])
        )

# ---------------- MESSAGE ----------------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    text = update.message.text
    state = user_state.get(user_id)

    if state and "crash" in state:
        bet = int(text)
        chips, _, _ = get_user(user_id)

        if bet > chips:
            await update.message.reply_text("❗ Недостаточно")
            return

        update_chips(user_id, chips - bet)

        msg = await update.message.reply_text("🚀 x1.0")

        games[user_id] = {"bet": bet, "mult": 1.0, "msg": msg}

        context.application.create_task(crash_loop(context, user_id))

        user_state.pop(user_id)
        return

    if state and "mini" in state:
        bet = int(text)
        chips, _, _ = get_user(user_id)

        if bet > chips:
            await update.message.reply_text("❗ Недостаточно")
            return

        chips -= bet
        roll = random.randint(1,15)

        if roll == state["mini"]:
            win = bet * 5
            chips += win
            await update.message.reply_text(f"🎉 {roll}\n+{win}")
        else:
            await update.message.reply_text(f"😢 {roll}\n-{bet}")

        update_chips(user_id, chips)
        user_state.pop(user_id)
        return

    await update.message.reply_text("❗ Используй кнопки", reply_markup=main_menu())

# ---------------- RUN ----------------
if __name__ == "__main__":
    threading.Thread(target=run_web).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))

    app.run_polling()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))

    app.run_polling()
