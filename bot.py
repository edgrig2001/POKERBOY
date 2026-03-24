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

user_state = {}
games = {}

# ---------------- Utils ----------------
def fmt(chips):
    if chips>=1_000_000: return f"{chips//1_000_000}M"
    if chips>=1_000: return f"{chips//1_000}K"
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
        cursor.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (today,uid))
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

# ---------------- Button ----------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data=="menu": await q.edit_message_text("🏠 Главное меню", reply_markup=main_menu()); return
    if data=="profile": await q.edit_message_text(await profile_text(uid), reply_markup=back()); return
    if data=="bonus":
        ok,chips=daily_bonus(uid)
        await q.edit_message_text(f"{'🎁 +5000' if ok else '❗ Уже получали'}\n💰 {fmt(chips)}", reply_markup=back())
        return
    if data=="ref":
        link=f"https://t.me/{context.bot.username}?start={uid}"
        await q.edit_message_text(f"👥 Ваша рефералька:\n{link}", reply_markup=back()); return
    if data=="top":
        users=cursor.execute("SELECT user_id,chips FROM users ORDER BY chips DESC LIMIT 5").fetchall()
        text="🏆 Топ игроков:\n"
        for i,u in enumerate(users,1): text+=f"{i}. {u[0]} — {fmt(u[1])}\n"
        await q.edit_message_text(text, reply_markup=back()); return

    # ---------------- CRASH ----------------
    if data=="crash":
        await q.edit_message_text("💰 Введите ставку:", reply_markup=back())
        user_state[uid]={"crash":True}; return
    if data=="cashout":
        game = games.get(uid)
        if not game: return
        game["stop"]=True
        win=int(game["bet"]*game["mult"])
        chips,_,_ = get_user(uid)
        update_chips(uid,chips+win)
        await game["msg"].edit_text(f"💸 Вы забрали x{game['mult']}\n+{fmt(win)}", reply_markup=main_menu())
        games.pop(uid); return

    # ---------------- SLOTS ----------------
    if data=="slots":
        chips,_,_ = get_user(uid)
        user_state[uid]={"slots":True}
        await q.edit_message_text(f"💰 Баланс: {fmt(chips)}\nВведите ставку:", reply_markup=back()); return
    if data=="spin":
        state=user_state.get(uid)
        if not state or "slots" not in state: return
        bet=state.get("bet"); chips,_,_=get_user(uid)
        if bet>chips: await q.edit_message_text("❗ Недостаточно фишек", reply_markup=back()); return
        update_chips(uid,chips-bet)
        symbols=["🍒","🍋","⭐","💎"]; weights=[55,25,15,5]
        result=random.choices(symbols,weights,k=3)
        if result[0]==result[1]==result[2]:
            mult={"🍒":2,"⭐":3,"💎":5}.get(result[0],1); win=bet*mult
            update_chips(uid,chips+win); text=f"{' '.join(result)}\n🎉 Вы выиграли +{fmt(win)}!"
        elif len(set(result))==2: text=f"{' '.join(result)}\n😐 Ничья"
        else: update_chips(uid,chips-bet); text=f"{' '.join(result)}\n😢 Проигрыш -{fmt(bet)}"
        await q.edit_message_text(text, reply_markup=main_menu()); user_state.pop(uid,None); return

# ---------------- CRASH LOOP ----------------
async def crash_loop(context, uid):
    game=games.get(uid)
    if not game: return
    while True:
        await asyncio.sleep(1)
        if game.get("stop"): return
        game["mult"]=round(game["mult"]+random.uniform(0.1,0.5),2)
        if random.random()<0.15:
            chips,_,_=get_user(uid)
            await game["msg"].edit_text(f"💥 КРАШ x{game['mult']}\nВы проиграли -{fmt(game['bet'])}", reply_markup=main_menu())
            games.pop(uid); return
        keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Забрать",callback_data="cashout")],[InlineKeyboardButton("🔙 Назад",callback_data="menu")]])
        await game["msg"].edit_text(f"🚀 x{game['mult']}",reply_markup=keyboard)

# ---------------- MESSAGE ----------------
async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.message.chat.id; text=update.message.text; state=user_state.get(uid)
    # CRASH
    if state and "crash" in state:
        try: bet=int(text)
        except: await update.message.reply_text("❗ Введите число"); return
        chips,_,_=get_user(uid)
        if bet>chips: await update.message.reply_text("❗ Недостаточно фишек"); return
        update_chips(uid,chips-bet)
        msg=await update.message.reply_text("🚀 x1.0")
        games[uid]={"bet":bet,"mult":1.0,"msg":msg,"stop":False}
        user_state.pop(uid,None)
        context.application.create_task(crash_loop(context,uid))
        return
    # SLOTS
    if state and "slots" in state:
        try: bet=int(text)
        except: await update.message.reply_text("❗ Введите число"); return
        chips,_,_=get_user(uid)
        if bet>chips: await update_message.reply_text("❗ Недостаточно фишек"); return
        user_state[uid]["bet"]=bet
        keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🎰 Крутить",callback_data="spin")],[InlineKeyboardButton("🔙 Назад",callback_data="menu")]])
        await update.message.reply_text(f"💰 Ставка: {fmt(bet)}\nНажмите крутить 🎰", reply_markup=keyboard)
        return
    await update.message.reply_text("❗ Используйте кнопки",reply_markup=main_menu())

# ---------------- RUN ----------------
if __name__=="__main__":
    threading.Thread(target=run_web).start()
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND),message))
    app.run_polling()
