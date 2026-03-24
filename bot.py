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
def home(): return "OK"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

# ---------------- Token ----------------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
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

user_state = {}  # текущие действия пользователя
games = {}       # данные игр: CRASH, SLOTS, Double, Roulette

# ---------------- Utils ----------------
def fmt(chips):
    if chips >= 1_000_000: return f"{chips//1_000_000}M"
    if chips >= 1_000: return f"{chips//1_000}K"
    return str(chips)

def get_user(uid):
    user = cursor.execute("SELECT chips,last_bonus,referred FROM users WHERE user_id=?", (uid,)).fetchone()
    if not user:
        cursor.execute("INSERT INTO users VALUES (?,?,?,?)", (uid,5000,"",0))
        conn.commit()
        return 5000,"",0
    return user

def update_chips(uid, chips):
    chips = max(0, chips)
    cursor.execute("UPDATE users SET chips=? WHERE user_id=?", (chips,uid))
    conn.commit()

def daily_bonus(uid):
    chips, last, _ = get_user(uid)
    today = str(datetime.date.today())
    if last != today:
        chips += 5000
        update_chips(uid, chips)
        cursor.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (today, uid))
        conn.commit()
        return True, chips
    return False, chips

# ---------------- Menus ----------------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 CRASH", callback_data="crash"),
         InlineKeyboardButton("🎰 SLOTS", callback_data="slots")],
        [InlineKeyboardButton("⚡ Double", callback_data="double"),
         InlineKeyboardButton("🎡 Roulette", callback_data="roulette")],
        [InlineKeyboardButton("👤 Профиль", callback_data="profile"),
         InlineKeyboardButton("🎁 Бонус", callback_data="bonus")],
        [InlineKeyboardButton("👥 Рефералька", callback_data="ref"),
         InlineKeyboardButton("🏆 Топ", callback_data="top")]
    ])
def back(): return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="menu")]])

# ---------------- Start ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.chat.id
    args = context.args
    # Рефералька при старте
    if args:
        try:
            ref_id = int(args[0])
            if ref_id != uid:
                r_chips, _, r_referred = get_user(ref_id)
                if r_referred==0:
                    update_chips(ref_id, r_chips+15000)
                    cursor.execute("UPDATE users SET referred=1 WHERE user_id=?", (ref_id,))
                    conn.commit()
        except: pass
    await update.message.reply_text("🎰 Казино Bot", reply_markup=main_menu())

# ---------------- Profile ----------------
async def profile_text(uid):
    chips, _, _ = get_user(uid)
    return f"👤 Профиль\n💰 {fmt(chips)} фишек"
# ---------------- BUTTONS ----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # --- Главное меню ---
    if data=="menu":
        await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu())
        return
    elif data=="profile":
        await q.edit_message_text(await profile_text(uid), reply_markup=back())
        return
    elif data=="bonus":
        ok,chips=daily_bonus(uid)
        await q.edit_message_text(f"{'🎁 +5000' if ok else '❗ Уже получали'}\n💰 {fmt(chips)}", reply_markup=back())
        return
    elif data=="ref":
        link=f"https://t.me/{context.bot.username}?start={uid}"
        await q.edit_message_text(f"👥 Рефералька\n{link}\n🎁 +15000 фишек другу", reply_markup=back())
        return
    elif data=="top":
        users=cursor.execute("SELECT user_id,chips FROM users ORDER BY chips DESC LIMIT 5").fetchall()
        text="🏆 Топ игроков:\n"
        for i,u in enumerate(users,1):
            text+=f"{i}. {u[0]} — {fmt(u[1])}\n"
        await q.edit_message_text(text, reply_markup=back())
        return

    # ================= CRASH =================
    elif data=="crash":
        await q.edit_message_text("💰 Введите ставку для CRASH:", reply_markup=back())
        user_state[uid]={"game":"crash"}

    elif data=="cashout":
        game=games.get(uid)
        if not game: return
        game["stop"]=True
        mult=game["mult"]
        win=int(game["bet"]*mult)
        update_chips(uid, get_user(uid)[0]+win)
        await game["msg"].edit_text(f"💸 Вы забрали x{round(mult,2)}\n+{fmt(win)}", reply_markup=main_menu())
        games.pop(uid)
        return

    # ================= SLOTS =================
    elif data=="slots":
        await q.edit_message_text("💰 Введите ставку для SLOTS:", reply_markup=back())
        user_state[uid]={"game":"slots"}

    elif data=="spin":
        state=user_state.get(uid)
        if not state or state.get("game")!="slots": return
        chips,_ ,_=get_user(uid)
        bet=state.get("bet")
        if bet>chips:
            await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back())
            return
        symbols=["🍒","🍋","⭐","💎"]
        weights=[55,25,15,5]
        result=random.choices(symbols,weights,k=3)
        win=0
        if result[0]==result[1]==result[2]:
            mult={"🍒":2,"⭐":3,"💎":5}.get(result[0],1)
            win=bet*mult
            text=f"{' '.join(result)}\n🎉 Вы выиграли x{mult} +{fmt(win)}"
        elif len(set(result))==2:
            win=bet
            text=f"{' '.join(result)}\n😐 Возврат +{fmt(win)}"
        else:
            win=-bet
            text=f"{' '.join(result)}\n😢 Вы проиграли {fmt(bet)}"
        update_chips(uid, get_user(uid)[0]+win)
        await q.edit_message_text(text, reply_markup=main_menu())
        user_state.pop(uid)
        return

    # ================= DOUBLE =================
    elif data=="double":
        await q.edit_message_text("💰 Введите ставку для DOUBLE:", reply_markup=back())
        user_state[uid]={"game":"double"}

    elif data.startswith("double_"):
        color=data.split("_")[1]
        state=user_state.get(uid)
        if not state or state.get("game")!="double": return
        bet=state.get("bet")
        chips,_ ,_=get_user(uid)
        if bet>chips:
            await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back())
            return
        roll=random.choice(["red","black"])
        if roll==color:
            win=bet*2
            update_chips(uid,chips+win)
            await q.edit_message_text(f"🎉 {roll}! Вы выиграли +{fmt(win)}", reply_markup=main_menu())
        else:
            update_chips(uid,chips-bet)
            await q.edit_message_text(f"💀 {roll}! Вы проиграли {fmt(bet)}", reply_markup=main_menu())
        user_state.pop(uid)
        return

    # ================= ROULETTE =================
    elif data=="roulette":
        await q.edit_message_text("💰 Введите ставку для ROULETTE (число 0-36 или red/black):", reply_markup=back())
        user_state[uid]={"game":"roulette"}

    elif data.startswith("roulette_"):
        choice=data.split("_")[1]
        state=user_state.get(uid)
        if not state or state.get("game")!="roulette": return
        bet=state.get("bet")
        chips,_ ,_=get_user(uid)
        if bet>chips:
            await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back())
            return
        number=random.randint(0,36)
        color="red" if number%2==0 else "black"
        win=0
        if choice.isdigit() and int(choice)==number: win=bet*36
        elif choice in ["red","black"] and choice==color: win=bet*2
        else: win=-bet
        update_chips(uid,chips+win)
        await q.edit_message_text(f"🎡 Выпало {number} ({color})\n{'🎉 Вы выиграли +'+fmt(win) if win>0 else '💀 Вы проиграли '+fmt(bet)}", reply_markup=main_menu())
        user_state.pop(uid)
        return
# ---------------- MESSAGE HANDLER ----------------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.chat.id
    text = update.message.text
    state = user_state.get(uid)

    if not state:
        await update.message.reply_text("❗ Используйте кнопки", reply_markup=main_menu())
        return

    # ---------------- CRASH ----------------
    if state.get("game")=="crash":
        try:
            bet=int(text)
        except:
            await update.message.reply_text("❗ Введите число фишек")
            return
        chips,_ ,_=get_user(uid)
        if bet>chips:
            await update.message.reply_text("❗ Недостаточно фишек")
            return
        update_chips(uid,chips-bet)
        msg=await update.message.reply_text("🚀 x1.0")
        games[uid]={"bet":bet,"mult":1.0,"msg":msg,"stop":False}
        user_state.pop(uid)
        # запускаем цикл CRASH
        context.application.create_task(crash_loop(context, uid))
        return

    # ---------------- SLOTS ----------------
    elif state.get("game")=="slots":
        try:
            bet=int(text)
        except:
            await update.message.reply_text("❗ Введите число фишек")
            return
        state["bet"]=bet
        await button(update, context, data="spin")
        return

    # ---------------- DOUBLE ----------------
    elif state.get("game")=="double":
        try:
            bet=int(text)
        except:
            await update.message.reply_text("❗ Введите число фишек")
            return
        state["bet"]=bet
        kb=[[InlineKeyboardButton("🔴 Red", callback_data="double_red"),
             InlineKeyboardButton("⚫ Black", callback_data="double_black")]]
        await update.message.reply_text("🎯 Выберите цвет:", reply_markup=InlineKeyboardMarkup(kb))
        return

    # ---------------- ROULETTE ----------------
    elif state.get("game")=="roulette":
        try:
            bet=int(text)
        except:
            await update.message.reply_text("❗ Введите число фишек")
            return
        state["bet"]=bet
        await update.message.reply_text("🎡 Введите число (0-36) или red/black:")
        return

# ---------------- CRASH LOOP ----------------
async def crash_loop(context, uid):
    game=games.get(uid)
    if not game: return
    msg=game["msg"]
    while not game.get("stop"):
        await asyncio.sleep(1)
        incr=random.uniform(0.2,0.8)
        game["mult"]+=round(incr,2)
        # шанс краша 15%
        if random.random()<0.15:
            await msg.edit_text(f"💥 КРАШ на x{round(game['mult'],2)}\n💀 Вы проиграли {fmt(game['bet'])}")
            games.pop(uid)
            return
        await msg.edit_text(f"🚀 x{round(game['mult'],2)}\n💸 Нажмите 'Забрать', чтобы остановить", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Забрать", callback_data="cashout")]]))
# ---------------- RUN ----------------
if __name__ == "__main__":
    # запуск Flask веб-сервера
    threading.Thread(target=run_web).start()

    # запуск Telegram бота
    app = ApplicationBuilder().token(TOKEN).build()

    # обработчики команд
    app.add_handler(CommandHandler("start", start))

    # обработчик кнопок
    app.add_handler(CallbackQueryHandler(button))

    # обработчик сообщений (ставки и ввод)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message))

    # polling
    app.run_polling()
