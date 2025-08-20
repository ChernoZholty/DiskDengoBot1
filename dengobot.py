import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from datetime import datetime
import os
import json
from flask import Flask
from threading import Thread

# --- ЛОГИ ---
logging.basicConfig(level=logging.INFO)
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# --- ID РОЛИ + КАНАЛОВ ---
ACTIVE_ROLE_ID = 1407089794240090263
LOG_CHANNEL_ID = 1407081468525805748  # канал для логов (админ)
COMMAND_CHANNEL_ID = 1407081468525805748  # канал, где бот будет писать /give mone
DB_CHANNEL_ID = 1407213722824343602  # отдельный приватный канал для "базы"
LEADERBOARD_CHANNEL_ID = 1407421547785883749  # ЛИДЕРБОРД

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="*", intents=intents)

voice_times = {}  # {member_id: datetime}
balances = {}  # {member_id: int}
db_messages = {}  # {member_id: message_id}
MONEY_PER_MINUTE = 1

# ---------------- WEB-СЕРВЕР для UptimeRobot ----------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is running!"
def run_web():
    app.run(host="0.0.0.0", port=8080)
def keep_alive():
    t = Thread(target=run_web)
    t.start()

# ==================== РАБОТА С БАЗОЙ ====================
async def load_database():
    global balances, db_message
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        logging.error("DB_CHANNEL_ID неверный или бот не видит канал.")
        return
    messages = [m async for m in db_channel.history(limit=None, oldest_first=True)]
    last_valid_msg = None
    last_valid_data = None
    to_delete = []
    for m in messages:
        try:
            data = json.loads(m.content)
            if isinstance(data, dict):
                if last_valid_msg:
                    to_delete.append(last_valid_msg)
                last_valid_msg = m
                last_valid_data = data
            else:
                to_delete.append(m)
        except json.JSONDecodeError:
            to_delete.append(m)
    for m in to_delete:
        try:
            await m.delete()
        except Exception:
            pass
    if last_valid_msg is not None:
        balances = {int(k): int(v) for k, v in last_valid_data.items()}
        db_message = last_valid_msg
        logging.info(f"База данных загружена. Записей: {len(balances)}")
        return
    new_msg = await db_channel.send(json.dumps({}))
    db_message = new_msg
    balances = {}
    logging.info("Создано новое JSON-сообщение для базы.")

async def save_database():
    global db_message
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        logging.error("DB_CHANNEL_ID неверный или бот не видит канал.")
        return
    if not db_message:
        await load_database()
    if not db_message:
        logging.error("Не удалось инициализировать базу.")
        return
    payload = {str(k): int(v) for k, v in balances.items()}
    try:
        await db_message.edit(content=json.dumps(payload, ensure_ascii=False))
        logging.info("База обновлена.")
    except discord.NotFound:
        db_message = await db_channel.send(json.dumps(payload, ensure_ascii=False))
        logging.info("База пересоздана (старое сообщение отсутствовало).")
    except Exception as e:
        logging.error(f"Не удалось сохранить базу: {e}")

async def change_balance(member: discord.Member, amount: int):
    old_balance = balances.get(member.id, 0)
    new_balance = old_balance + amount
    if new_balance < 0:
        return False, old_balance
    balances[member.id] = new_balance
    await save_database()
    await update_leaderboard()  # ЛИДЕРБОРД
    return True, new_balance

# ==================== ЛИДЕРБОРД ====================
async def update_leaderboard():
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        logging.error("LEADERBOARD_CHANNEL_ID неверный или бот не видит канал.")
        return

    top = sorted(balances.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for i, (user_id, balance) in enumerate(top[:10], start=1):
        member = channel.guild.get_member(user_id)
        name = member.display_name if member else f"User {user_id}"
        lines.append(f"**{i}. {name}** — 💰 {balance} монет")
    text = "🏆 **Топ участников по балансу:**\n\n" + "\n".join(lines) if lines else "Пока нет данных."

    async for msg in channel.history(limit=50):
        if msg.author == bot.user:
            await msg.edit(content=text)
            return
    await channel.send(text)

# ==================== СОБЫТИЯ ====================
@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")
    await load_database()
    await update_leaderboard()  # ЛИДЕРБОРД при старте

@bot.event
async def on_voice_state_update(member, before, after):
    role = member.guild.get_role(ACTIVE_ROLE_ID)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    command_channel = bot.get_channel(COMMAND_CHANNEL_ID)

    if before.channel is None and after.channel is not None:
        if role and role not in member.roles:
            try:
                await member.add_roles(role)
            except discord.Forbidden:
                logging.warning("Нет прав выдавать роль.")
        voice_times[member.id] = datetime.now()
        if log_channel:
            await log_channel.send(f"✅ {member} зашёл в {after.channel}, роль выдана.")
        if command_channel:
            await command_channel.send(f"🎧 {member.mention}, добро пожаловать в {after.channel.mention}!")
    if before.channel is not None and after.channel is None:
        if role and role in member.roles:
            try:
                await member.remove_roles(role)
            except discord.Forbidden:
                logging.warning("Нет прав снимать роль.")
        if member.id in voice_times:
            join_time = voice_times.pop(member.id)
            minutes = int((datetime.now() - join_time).total_seconds() // 60)
            if minutes > 0:
                money = minutes * MONEY_PER_MINUTE
                success, total_balance = await change_balance(member, money)
                if success and command_channel:
                    await command_channel.send(
                        f"💰 {member.mention}, тебе начислено **{money}** монет "
                        f"(за {minutes} мин). Баланс: **{total_balance}**."
                    )
                if log_channel:
                    await log_channel.send(
                        f"📈 Начислено {minutes}×{MONEY_PER_MINUTE} = **{money}** монет пользователю {member}. "
                        f"Баланс: **{total_balance}**."
                    )
            else:
                if command_channel:
                    await command_channel.send(
                        f"⚠️ {member.mention}, был в войсе <1 мин. Монеты не начислены."
                    )
                if log_channel:
                    await log_channel.send(
                        f"ℹ️ {member} вышел слишком быстро (<1 минуты). Начисления нет."
                    )

# ==================== КОМАНДЫ ====================
@bot.command(name="баланс")
async def balance_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    balance = balances.get(member.id, 0)

    # Отправляем ответ и удаляем его через 30 секунд
    msg = await ctx.send(f"💰 Баланс {member.mention}: **{balance}** монет.")
    await msg.delete(delay=30)

    # Удаляем сообщение с командой пользователя (если есть права)
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass  # если у бота нет прав удалять сообщения

@bot.command(name="givemoney")
@commands.has_permissions(administrator=True)
async def givemoney_cmd(ctx, amount: int, member: discord.Member):
    success, total_balance = await change_balance(member, amount)
    if not success:
        await ctx.send(f"⚠️ Нельзя уменьшить баланс {member.mention} ниже нуля! "
                       f"(текущий баланс: {total_balance})")
        return
    if amount > 0:
        await ctx.send(f"✅ {member.mention} получил {amount} монет. Баланс: {total_balance}")
    elif amount < 0:
        await ctx.send(f"✅ У {member.mention} списано {-amount} монет. Баланс: {total_balance}")
    else:
        await ctx.send("⚠️ Изменение на 0 монет не имеет смысла.")

@bot.command(name="takemoney")
@commands.has_permissions(administrator=True)
async def takemoney_cmd(ctx, amount: int, member: discord.Member):
    if amount <= 0:
        await ctx.send("⚠️ Укажи положительную сумму для списания, например: *takemoney 50 @user")
        return
    await givemoney_cmd(ctx, -amount, member)

@bot.command(name="cleardb")
@commands.has_permissions(administrator=True)
async def cleardb_cmd(ctx):
    global balances, db_message
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        await ctx.send("❌ Канал базы данных не найден.")
        return
    msgs = [m async for m in db_channel.history(limit=None)]
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass
    balances = {}
    db_message = await db_channel.send(json.dumps({}))
    await ctx.send("✅ База очищена. Все балансы сброшены на 0.")

# ==================== БАТЛЫ ====================
active_battles = {}
JUDGE_ROLE_ID = 1407534809034784879

def generate_battle_id(members):
    return "_".join(str(m.id) for m in members)

@bot.command(name="батл1")
async def battle1_cmd(ctx, *members: discord.Member):
    if not members:
        await ctx.send("⚠️ Укажи хотя бы одного участника для команды 1.")
        return

    battle_id = generate_battle_id(members)
    if battle_id in active_battles:
        await ctx.send(f"⚠️ Батл с таким ID уже существует: {battle_id}")
        return

    active_battles[battle_id] = {"team1": list(members), "team2": []}
    team_list = ", ".join(m.mention for m in members)
    await ctx.send(f"✅ Команда 1 зарегистрирована. ID батла: {battle_id}\n👥 Состав команды 1: {team_list}")

@bot.command(name="батл2")
async def battle2_cmd(ctx, battle_id: str, *members: discord.Member):
    if battle_id not in active_battles:
        await ctx.send("⚠️ Указанный ID батла не найден.")
        return
    if not members:
        await ctx.send("⚠️ Укажи хотя бы одного участника для команды 2.")
        return

    if active_battles[battle_id]["team2"]:
        await ctx.send("⚠️ Для этого батла команда 2 уже зарегистрирована.")
        return

    active_battles[battle_id]["team2"] = list(members)
    team_list = ", ".join(m.mention for m in members)
    await ctx.send(f"✅ Команда 2 зарегистрирована для батла {battle_id}.\n👥 Состав команды 2: {team_list}")

@bot.command(name="батл_отмена")
async def cancel_battle_cmd(ctx, battle_id: str):
    judge_role = ctx.guild.get_role(JUDGE_ROLE_ID)
    if judge_role not in ctx.author.roles:
        await ctx.send("❌ Только Судья может отменять батлы.")
        return

    if battle_id not in active_battles:
        await ctx.send("⚠️ Батл с таким ID не найден.")
        return

    del active_battles[battle_id]
    await ctx.send(f"🛑 Батл {battle_id} был отменён судьёй {ctx.author.mention}.")

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    keep_alive()  # Запускаем веб-сервер для UptimeRobot
    bot.run(TOKEN)
