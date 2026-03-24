import os
import sqlite3
import threading
import random
import datetime
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
    print("Нет токена")
    exit()

# ---------------- DB ----------------
conn = sqlite3.connect("poker.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    chips INTEGER,
    last_bonus TEXT
)
""")
conn.commit()

user_state = {}
games = {}

# ---------------- Карты ----------------
suits = ["♠️", "♥️", "♦️", "♣️"]
ranks = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]

def draw_cards(n=2):
    return [random.choice(ranks)+random.choice(suits) for _ in range(n)]

# ---------------- Фишки ----------------
def get_user(user_id):
    user = cursor.execute("SELECT chips,last_bonus FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?,?,?)", (user_id, 5000, ""))
        conn.commit()
        return 5000, ""
    return user

def update_chips(user_id, chips):
    chips = min(chips, 15000)
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips, user_id))
    conn.commit()

def daily_bonus(user_id):
    chips, last = get_user(user_id)
    today = str(datetime.date.today())
    if last != today:
        chips += 5000
        chips = min(chips, 15000)
        cursor.execute("UPDATE users SET chips=?, last_bonus=? WHERE user_id=?", (chips, today, user_id))
        conn.commit()
        return True, chips
    return False, chips

# ---------------- Меню ----------------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 Играть в покер", callback_data="poker")],
        [InlineKeyboardButton("🎲 Мини-игра", callback_data="mini")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🎁 Бонус", callback_data="bonus")],
    ])

def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад", callback_data="menu")]
    ])

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("♠️ Добро пожаловать в Poker Bot!", reply_markup=main_menu())

# ---------------- PROFILE ----------------
async def profile_text(user_id):
    chips, _ = get_user(user_id)
    return f"👤 Профиль\n\n💰 Фишки: {chips}"

# ---------------- BUTTON ----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    data = q.data

    # --- МЕНЮ ---
    if data == "menu":
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())

    # --- ПРОФИЛЬ ---
    elif data == "profile":
        await q.edit_message_text(await profile_text(user_id), reply_markup=back_menu())

    # --- БОНУС ---
    elif data == "bonus":
        ok, chips = daily_bonus(user_id)
        text = "🎁 Вы получили 5000!" if ok else "❗ Уже получали сегодня"
        await q.edit_message_text(f"{text}\n💰 Сейчас: {chips}", reply_markup=back_menu())

    # ================= ПОКЕР =================
    elif data == "poker":
        games[user_id] = {
            "player": draw_cards(),
            "bot": draw_cards(),
            "bank": 1000
        }
        await show_game(q, user_id)

    elif data.startswith("act_"):
        game = games.get(user_id)
        if not game:
            return

        action = data.split("_")[1]

        player_score = random.randint(1,100)
        bot_score = random.randint(1,100)

        if action == "fold":
            result = "😢 Вы сбросили карты"
            update_chips(user_id, get_user(user_id)[0] - 500)

        else:
            if player_score >= bot_score:
                result = "🏆 Вы выиграли!"
                update_chips(user_id, get_user(user_id)[0] + 1000)
            else:
                result = "💀 Вы проиграли"
                update_chips(user_id, get_user(user_id)[0] - 1000)

        await q.edit_message_text(result, reply_markup=main_menu())

    # ================= МИНИ ИГРА =================
    elif data == "mini":
        buttons = []
        for i in range(1,16):
            buttons.append([InlineKeyboardButton(str(i), callback_data=f"mini_{i}")])
        await q.edit_message_text("🎲 Выбери число:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("mini_"):
        num = int(data.split("_")[1])
        user_state[user_id] = {"mini": num}
        await q.edit_message_text(f"💰 Сколько ставишь на {num}?", reply_markup=back_menu())

# ---------------- MESSAGE ----------------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    text = update.message.text

    state = user_state.get(user_id)

    if state and "mini" in state:
        try:
            bet = int(text)
        except:
            await update.message.reply_text("❗ Введи число")
            return

        chips, _ = get_user(user_id)
        if bet > chips:
            await update.message.reply_text("❗ Недостаточно фишек")
            return

        roll = random.randint(1,15)

        if roll == state["mini"]:
            win = bet * 5
            update_chips(user_id, chips + win)
            await update.message.reply_text(f"🎉 Выпало {roll}\nВы выиграли {win}!")
        else:
            update_chips(user_id, chips - bet)
            await update.message.reply_text(f"😢 Выпало {roll}\nВы проиграли")

        user_state.pop(user_id)
        return

    await update.message.reply_text("❗ Используй кнопки", reply_markup=main_menu())

# ---------------- SHOW GAME ----------------
async def show_game(q, user_id):
    game = games[user_id]

    text = (
        f"🃏 Покер\n\n"
        f"Ваши карты: {' '.join(game['player'])}\n"
        f"Банк: {game['bank']}\n\n"
        f"Выберите действие:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Чек", callback_data="act_check"),
         InlineKeyboardButton("📞 Колл", callback_data="act_call")],
        [InlineKeyboardButton("💰 Алл-ин", callback_data="act_allin"),
         InlineKeyboardButton("❌ Фолд", callback_data="act_fold")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu")]
    ])

    await q.edit_message_text(text, reply_markup=keyboard)

# ---------------- RUN ----------------
if __name__ == "__main__":
    threading.Thread(target=run_web).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))

    app.run_polling()