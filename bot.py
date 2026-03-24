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

# ---------------- TOKEN ----------------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
if not TOKEN:
    print("Нет токена")
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

# ---------------- USER FUNCTIONS ----------------
def get_user(uid):
    user = cursor.execute("SELECT chips,last_bonus,referred FROM users WHERE user_id=?", (uid,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?,?,?,?)", (uid,5000,"",0))
        conn.commit()
        return 5000,"",0
    return user

def update_chips(uid,chips):
    chips = max(0,chips)
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips,uid))
    conn.commit()

def daily_bonus(uid):
    chips,last,_ = get_user(uid)
    today=str(datetime.date.today())
    if last != today:
        chips+=5000
        cursor.execute("UPDATE users SET chips=?, last_bonus=? WHERE user_id=?", (chips,today,uid))
        conn.commit()
        return True,chips
    return False,chips

def fmt(num):
    if num>=1_000_000:
        return f"{round(num/1_000_000,2)}M"
    elif num>=1000:
        return f"{round(num/1000,2)}K"
    else:
        return str(num)

# ---------------- MENUS ----------------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 CRASH", callback_data="crash"),
         InlineKeyboardButton("🎰 SLOTS", callback_data="slots")],
        [InlineKeyboardButton("🎯 DOUBLE", callback_data="double"),
         InlineKeyboardButton("🎡 ROULETTE", callback_data="roulette")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton("👥 Рефералка", callback_data="ref"),
         InlineKeyboardButton("🏆 Топ", callback_data="top")]
    ])

def back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu")]])

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.chat.id
    args = context.args
    # рефералка
    if args:
        try:
            ref_id=int(args[0])
            if ref_id!=uid:
                chips,_,referred=get_user(ref_id)
                if not referred:
                    update_chips(ref_id,chips+15000)
                    cursor.execute("UPDATE users SET referred=1 WHERE user_id=?", (ref_id,))
                    conn.commit()
        except: pass
    await update.message.reply_text("🎰 Казино Bot", reply_markup=main_menu())
# ---------------- BUTTON HANDLER ----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE, data=None):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not data:
        data = q.data

    # ---------- МЕНЮ ----------
    if data=="menu":
        user_state.pop(uid,None)
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())

    elif data=="profile":
        chips,_,_ = get_user(uid)
        await q.edit_message_text(f"👤 Профиль\n💰 {fmt(chips)}", reply_markup=back())

    elif data=="bonus":
        ok,chips=daily_bonus(uid)
        await q.edit_message_text(f"{'🎁 +5k' if ok else '❗ Уже получали'}\n💰 {fmt(chips)}", reply_markup=back())

    elif data=="ref":
        link=f"https://t.me/{context.bot.username}?start={uid}"
        await q.edit_message_text(f"👥 Ваша ссылка:\n{link}", reply_markup=back())

    elif data=="top":
        users=cursor.execute("SELECT user_id,chips FROM users ORDER BY chips DESC LIMIT 5").fetchall()
        text="🏆 Топ игроков:\n\n"
        for i,u in enumerate(users,1):
            text+=f"{i}. {u[0]} — {fmt(u[1])}\n"
        await q.edit_message_text(text, reply_markup=back())

    # ================= CRASH =================
elif data == "crash":
    await q.edit_message_text("💰 Введите ставку:", reply_markup=back())
    user_state[user_id] = {"crash": True}

elif data == "cashout":
    game = games.get(user_id)
    if not game:
        return
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

    if result[0] == result[1] == result[2]:
        mult = {"🍒":2,"⭐":3,"💎":5}.get(result[0],1)
        win = bet * mult
        update_chips(user_id, chips + win)
        text = f"{' '.join(result)}\n🎉 x{mult} (+{win})"
    elif len(set(result)) == 2:
        update_chips(user_id, chips)
        text = f"{' '.join(result)}\n😐 Возврат"
    else:
        update_chips(user_id, chips - bet)
        text = f"{' '.join(result)}\n😢 -{bet}"

    await q.edit_message_text(text, reply_markup=main_menu())
    # ---------- DOUBLE ----------
    elif data=="double":
        await q.edit_message_text("💰 Введите ставку для DOUBLE:", reply_markup=back())
        user_state[uid]={"game":"double"}

    elif data in ["double_red","double_black"]:
        state=user_state.get(uid)
        if not state or "bet" not in state: return
        bet=state["bet"]
        chips,_ ,_=get_user(uid)
        if bet>chips:
            await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back())
            return
        update_chips(uid,chips-bet)

        color=random.choice(["red","black"])
        win=0
        if (data=="double_red" and color=="red") or (data=="double_black" and color=="black"):
            win=bet*2
            update_chips(uid,get_user(uid)[0]+win)
            text=f"🎉 Выпало {color.upper()}! Вы выиграли {fmt(win)}"
        else:
            text=f"💀 Выпало {color.upper()}. Вы проиграли -{fmt(bet)}"
        await q.edit_message_text(text, reply_markup=main_menu())
        user_state.pop(uid,None)

   # ================= ROULETTE =================
elif data == "roulette":
    await q.edit_message_text("💰 Введите ставку для ROULETTE:", reply_markup=back())
    user_state[user_id] = {"roulette": True}

elif data.startswith("roulette_spin"):
    state = user_state.get(user_id)
    if not state or "bet" not in state:
        await q.edit_message_text("❗ Введите ставку сначала", reply_markup=back())
        return

    bet = state["bet"]
    chips, _, _ = get_user(user_id)
    if bet > chips:
        await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back())
        return
    update_chips(user_id, chips - bet)

    # Определяем исход
    outcome = random.randint(0, 36)
    colors = {0: "green"}
    for i in range(1, 37):
        colors[i] = "red" if i % 2 == 0 else "black"

    choice = state.get("choice")  # число или color
    win = 0
    text = ""

    # Проверка результата
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
# ---------------- MESSAGE HANDLER ----------------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.chat.id
    text = update.message.text
    state = user_state.get(uid)

    if state:
        game = state.get("game")

        if game=="crash":
            try:
                bet=int(text)
            except:
                await update.message.reply_text("❗ Введите число")
                return
            chips,_ ,_=get_user(uid)
            if bet>chips:
                await update.message.reply_text("❗ Недостаточно фишек")
                return
            update_chips(uid,chips-bet)
            msg=await update.message.reply_text("🚀 x1.0")
            games[uid]={"bet":bet,"mult":1.0,"msg":msg,"stop":False}
            context.application.create_task(crash_loop(context,uid))
            user_state.pop(uid,None)

        elif game=="slots":
            try:
                bet=int(text)
            except:
                await update.message.reply_text("❗ Введите число")
                return
            user_state[uid]["bet"]=bet
            await button(update,context,"spin")

        elif game=="double":
            try:
                bet=int(text)
            except:
                await update.message.reply_text("❗ Введите число")
                return
            user_state[uid]["bet"]=bet
            kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴 RED", callback_data="double_red"),
                 InlineKeyboardButton("⚫ BLACK", callback_data="double_black")]
            ])
            await update.message.reply_text(f"Выберите цвет для ставки {fmt(bet)}", reply_markup=kb)

        elif game=="roulette":
            try:
                bet=int(text)
            except:
                await update.message.reply_text("❗ Введите число")
                return
            user_state[uid]["bet"]=bet
            kb=InlineKeyboardMarkup([[InlineKeyboardButton(str(i),callback_data=f"roulette_spin_{i}") for i in range(0,37)]])
            kb.add([InlineKeyboardButton("🔴 RED",callback_data="roulette_spin_red"),
                    InlineKeyboardButton("⚫ BLACK",callback_data="roulette_spin_black")])
            await update.message.reply_text(f"Выберите число или цвет для ставки {fmt(bet)}", reply_markup=kb)

        return

    await update.message.reply_text("❗ Используйте кнопки", reply_markup=main_menu())

# ---------------- CRASH LOOP ----------------
async def crash_loop(context,uid):
    game=games.get(uid)
    if not game: return
    while not game.get("stop"):
        await asyncio.sleep(1)
        game["mult"]=round(game["mult"]+random.uniform(0.2,0.8),2)
        # случайный краш
        if random.random()<0.2:
            msg=game["msg"]
            bet=game["bet"]
            await msg.edit_text(f"💥 КРАШ на x{round(game['mult'],2)}\nВы проиграли -{fmt(bet)}", reply_markup=main_menu())
            games.pop(uid)
            return
        await game["msg"].edit_text(f"🚀 x{round(game['mult'],2)}\nНажмите 💸 Забрать чтобы забрать выигрыш",
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Забрать",callback_data="cashout")]]))

# ---------------- RUN ----------------
if __name__=="__main__":
    threading.Thread(target=run_web).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))

    app.run_polling()
