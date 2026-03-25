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
    chips = max(0, chips)
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips, user_id))
    conn.commit()

def daily_bonus(user_id):
    chips, last, _ = get_user(user_id)
    today = str(datetime.date.today())
    if last != today:
        chips += 5000
        cursor.execute("UPDATE users SET chips=?, last_bonus=? WHERE user_id=?", (chips, today, user_id))
        conn.commit()
        return True, chips
    return False, chips

def format_chips(chips):
    if chips >= 1_000_000:
        return f"{chips//1_000_000}M"
    elif chips >= 1_000:
        return f"{chips//1_000}k"
    return str(chips)

# ---------------- MENU ----------------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 CRASH", callback_data="crash"), InlineKeyboardButton("🎰 SLOTS", callback_data="slots")],
        [InlineKeyboardButton("🎲 DOUBLE", callback_data="double"), InlineKeyboardButton("🎡 ROULETTE", callback_data="roulette")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"), InlineKeyboardButton("🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton("👥 Рефералка", callback_data="ref"), InlineKeyboardButton("🏆 Топ", callback_data="top")]
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
        except: pass
    await update.message.reply_text("🎰 Казино Bot", reply_markup=main_menu())

# ---------------- BUTTON ----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    data = q.data
    chips, _, _ = get_user(user_id)

    # --- MENU ---
    if data == "menu":
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())

    elif data == "profile":
        await q.edit_message_text(f"👤 Профиль\n💰 {format_chips(chips)}", reply_markup=back())

    elif data == "bonus":
        ok, new_chips = daily_bonus(user_id)
        await q.edit_message_text(f"{'🎁 +5000' if ok else '❗ Уже получали'}\n💰 {format_chips(new_chips)}", reply_markup=back())

    elif data == "ref":
        link = f"https://t.me/{context.bot.username}?start={user_id}"
        await q.edit_message_text(f"👥 Реферальная ссылка:\n{link}", reply_markup=back())

    elif data == "top":
        users = cursor.execute("SELECT user_id, chips FROM users ORDER BY chips DESC LIMIT 5").fetchall()
        text = "🏆 Топ игроков:\n\n"
        for i,u in enumerate(users,1):
            text += f"{i}. {u[0]} — {format_chips(u[1])}\n"
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
        await game["msg"].edit_text(f"💸 Забрали x{round(mult,2)}\n+{format_chips(win)}", reply_markup=main_menu())
        games.pop(user_id)

    # ================= SLOTS =================
    elif data == "slots":
        bet = min(500, chips)
        symbols = ["🍒","🍋","⭐","💎"]
        weights = [55,25,15,5]
        result = random.choices(symbols, weights, k=3)
        if result[0]==result[1]==result[2]:
            mult = {"🍒":2,"⭐":10,"💎":25}.get(result[0],1)
            win = bet * mult
            update_chips(user_id, chips + win)
            text = f"{' '.join(result)}\n🎉 Вы выиграли {format_chips(win)}!"
        elif len(set(result))==2:
            text = f"{' '.join(result)}\n😐 Возврат"
        else:
            update_chips(user_id, chips - bet)
            text = f"{' '.join(result)}\n😢 -{format_chips(bet)}"
        await q.edit_message_text(text, reply_markup=main_menu())

    # ================= DOUBLE =================
    elif data == "double":
        kb = [[InlineKeyboardButton("🔴 Red", callback_data="double_red"),
               InlineKeyboardButton("⚫ Black", callback_data="double_black")]]
        await q.edit_message_text(f"💰 Ваш баланс: {format_chips(chips)}\nВыберите цвет", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("double_"):
        choice = data.split("_")[1]
        color = random.choice(["red","black"])
        win = chips // 10
        if choice == color:
            update_chips(user_id, chips + win)
            text = f"🎉 Выпало {color.capitalize()}! Вы выиграли {format_chips(win)}"
        else:
            update_chips(user_id, chips - win)
            text = f"😢 Выпало {color.capitalize()}! Вы проиграли {format_chips(win)}"
        await q.edit_message_text(text, reply_markup=main_menu())

    # ================= ROULETTE =================
    elif data == "roulette":
        kb = [[InlineKeyboardButton(str(i), callback_data=f"roulette_{i}") for i in range(1,6)]]
        kb.append([InlineKeyboardButton("🔴 Red", callback_data="roulette_red"), InlineKeyboardButton("⚫ Black", callback_data="roulette_black")])
        await q.edit_message_text(f"💰 Ваш баланс: {format_chips(chips)}\nВыберите ставку", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("roulette_"):
        choice = data.split("_")[1]
        if choice.isdigit():
            num = int(choice)
            result = random.randint(1,5)
            win = 500 if result == num else -500
            update_chips(user_id, chips + win)
            text = f"🎡 Выпало {result}\n{'🎉 Вы выиграли!' if win>0 else '😢 Вы проиграли'} {format_chips(abs(win))}"
        else:
            color_choice = choice
            color_result = random.choice(["red","black"])
            win = 500 if color_choice.split()[0].lower() == color_result else -500
            update_chips(user_id, chips + win)
            text = f"🎡 Выпало {color_result.capitalize()}\n{'🎉 Вы выиграли!' if win>0 else '😢 Вы проиграли'} {format_chips(abs(win))}"
        await q.edit_message_text(text, reply_markup=main_menu())

# ---------------- CRASH LOOP ----------------
async def crash_loop(context, user_id):
    game = games[user_id]
    while True:
        await asyncio.sleep(1)
        if game.get("stop"): return
        game["mult"] += round(random.uniform(0.2,0.8),2)
        if random.random() < 0.2:
            await game["msg"].edit_text(f"💥 КРАШ на x{round(game['mult'],2)}\nВы проиграли", reply_markup=main_menu())
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
    chips, _, _ = get_user(user_id)

    if state and "crash" in state:
        try:
            bet = int(text)
        except: 
            await update.message.reply_text("❗ Введите число")
            return
        if bet > chips:
            await update.message.reply_text("❗ Недостаточно")
            return
        update_chips(user_id, chips - bet)
        msg = await update.message.reply_text("🚀 x1.0")
        games[user_id] = {"bet": bet, "mult": 1.0, "msg": msg}
        context.application.create_task(crash_loop(context, user_id))
        user_state.pop(user_id)
        return

    await update.message.reply_text("❗ Используйте кнопки", reply_markup=main_menu())

# ---------------- RUN ----------------
if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))
    app.run_polling()
