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
    chips = max(0, chips)  # Баланс теперь не ограничен сверху
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
            text = f"{' '.join(result)}\n🎉 Вы выиграли {win}"
        elif len(set(result))==2:
            update_chips(user_id, chips)
            text = f"{' '.join(result)}\n😐 Возврат"
        else:
            update_chips(user_id, chips - bet)
            text = f"{' '.join(result)}\n😢 Проигрыш -{bet}"
        await q.edit_message_text(text, reply_markup=main_menu())

    # ================= DOUBLE =================
    elif data == "double":
        await q.edit_message_text("💰 Введите ставку для DOUBLE:", reply_markup=back())
        user_state[user_id] = {"double": True}

    elif data.startswith("double_guess"):
        state = user_state.get(user_id)
        if not state or "bet" not in state or "choice" not in state:
            await q.edit_message_text("❗ Введите ставку и выбор сначала", reply_markup=back())
            return
        bet = state["bet"]
        choice = state["choice"]
        chips, _, _ = get_user(user_id)
        if bet > chips:
            await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back())
            return
        update_chips(user_id, chips - bet)
        outcome = random.choice(["red","black"])
        if choice == outcome:
            win = bet * 2
            update_chips(user_id, get_user(user_id)[0] + win)
            text = f"🎉 Выпало {outcome.upper()}! Вы выиграли {win}"
        else:
            text = f"💀 Выпало {outcome.upper()}. Проигрыш -{bet}"
        await q.edit_message_text(text, reply_markup=main_menu())
        user_state.pop(user_id, None)

    # ================= ROULETTE =================
    elif data == "roulette":
        await q.edit_message_text("💰 Введите ставку для ROULETTE:", reply_markup=back())
        user_state[user_id] = {"roulette": True}

    elif data.startswith("roulette_spin"):
        state = user_state.get(user_id)
        if not state or "bet" not in state or "choice" not in state:
            await q.edit_message_text("❗ Введите ставку и выбор сначала", reply_markup=back())
            return
        bet = state["bet"]
        choice = state["choice"]
        chips, _, _ = get_user(user_id)
        if bet > chips:
            await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back())
            return
        update_chips(user_id, chips - bet)
        outcome = random.randint(0,36)
        colors = {0:"green"}
        for i in range(1,37):
            colors[i] = "red" if i%2==0 else "black"
        win = 0
        if choice == str(outcome) or choice == colors[outcome]:
            if choice == str(outcome):
                win = bet * 35
            else:
                win = bet * 2
            update_chips(user_id, get_user(user_id)[0] + win)
            text = f"🎉 Выпало {outcome} ({colors[outcome].upper()})! Вы выиграли {win}"
        else:
            text = f"💀 Выпало {outcome} ({colors[outcome].upper()}). Проигрыш -{bet}"
        await q.edit_message_text(text, reply_markup=main_menu())
        user_state.pop(user_id, None)
# ---------------- CRASH LOOP ----------------
async def crash_loop(context, user_id):
    game = games[user_id]

    while True:
        await asyncio.sleep(1)

        if game.get("stop"):
            return

        # Увеличиваем множитель
        game["mult"] += round(random.uniform(0.2,0.8),2)

        # Случайный CRASH
        if random.random() < 0.15:  # 15% шанс CRASH
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

    # ----- CRASH -----
    if state and "crash" in state:
        try:
            bet = int(text)
        except:
            await update.message.reply_text("❗ Введите число")
            return
        chips, _, _ = get_user(user_id)
        if bet > chips:
            await update.message.reply_text("❗ Недостаточно фишек")
            return
        update_chips(user_id, chips - bet)
        msg = await update.message.reply_text("🚀 x1.0")
        games[user_id] = {"bet": bet, "mult": 1.0, "msg": msg}
        context.application.create_task(crash_loop(context, user_id))
        user_state.pop(user_id)
        return

    # ----- DOUBLE -----
    if state and "double" in state:
        try:
            bet = int(text)
        except:
            await update.message.reply_text("❗ Введите число")
            return
        chips, _, _ = get_user(user_id)
        if bet > chips:
            await update.message.reply_text("❗ Недостаточно фишек")
            return
        # Запрашиваем выбор Red/Black
        user_state[user_id] = {"double": True, "bet": bet}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Red", callback_data="double_choice_red"),
             InlineKeyboardButton("⚫ Black", callback_data="double_choice_black")]
        ])
        await update.message.reply_text(f"💰 Ставка {bet}. Выберите Red или Black:", reply_markup=kb)
        return

    # ----- ROULETTE -----
    if state and "roulette" in state:
        try:
            bet = int(text)
        except:
            await update.message.reply_text("❗ Введите число")
            return
        chips, _, _ = get_user(user_id)
        if bet > chips:
            await update.message.reply_text("❗ Недостаточно фишек")
            return
        # Запрашиваем выбор номера или цвета
        user_state[user_id] = {"roulette": True, "bet": bet}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("0", callback_data="roulette_choice_0")],
            [InlineKeyboardButton("1-12 Red", callback_data="roulette_choice_red")],
            [InlineKeyboardButton("1-12 Black", callback_data="roulette_choice_black")],
            [InlineKeyboardButton("13-24 Red", callback_data="roulette_choice_red")],
            [InlineKeyboardButton("13-24 Black", callback_data="roulette_choice_black")],
            [InlineKeyboardButton("25-36 Red", callback_data="roulette_choice_red")],
            [InlineKeyboardButton("25-36 Black", callback_data="roulette_choice_black")]
        ])
        await update.message.reply_text(f"💰 Ставка {bet}. Выберите номер или цвет:", reply_markup=kb)
        return

    await update.message.reply_text("❗ Используйте кнопки", reply_markup=main_menu())
# ---------------- RUN ----------------
if __name__ == "__main__":
    # Запуск веб-сервера для Render/Heroku
    threading.Thread(target=run_web).start()

    # Создание приложения
    app = ApplicationBuilder().token(TOKEN).build()

    # Обработчики команд и кнопок
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))

    # Запуск polling
    app.run_polling()
