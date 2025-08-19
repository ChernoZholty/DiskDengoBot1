import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from datetime import datetime
import os
import json

# --- ЛОГИ ---
logging.basicConfig(level=logging.INFO)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# --- ID РОЛИ + КАНАЛОВ ---
ACTIVE_ROLE_ID = 1407089794240090263
LOG_CHANNEL_ID = 1407081468525805748        # канал для логов (админ)
COMMAND_CHANNEL_ID = 1407081468525805748   # канал, где бот будет писать /give mone
DB_CHANNEL_ID = 1407213722824343602  # создаёшь отдельный приватный канал для "базы"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="*", intents=intents)

voice_times = {}   # {member_id: datetime}
balances = {}      # {member_id: int}
db_messages = {}     # {member_id: message_id} — индекс сообщения с балансом

# Сколько монет за минуту
MONEY_PER_MINUTE = 1

@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")


# ==================== РАБОТА С БАЗОЙ ====================

async def load_database():
    """Загружает базу из одного JSON-сообщения в канале"""
    global balances, db_message
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        logging.error("DB_CHANNEL_ID неверный или бот не видит канал.")
        return

    async for msg in db_channel.history(limit=1, oldest_first=True):
        try:
            data = json.loads(msg.content)
            if isinstance(data, dict):
                balances = {int(k): int(v) for k, v in data.items()}
                db_message = msg
                logging.info("База данных успешно загружена.")
                return
        except json.JSONDecodeError:
            pass

    # если сообщений нет → создаём пустую базу
    msg = await db_channel.send(json.dumps({}))
    db_message = msg
    balances = {}
    logging.info("Создано новое сообщение для базы.")


async def save_database():
    """Сохраняет текущие балансы в одно JSON-сообщение"""
    global db_message
    if not db_message:
        return
    try:
        await db_message.edit(content=json.dumps(balances, ensure_ascii=False))
        logging.info("База данных обновлена.")
    except Exception as e:
        logging.error(f"Не удалось сохранить базу: {e}")


# ==================== ОСНОВНЫЕ СОБЫТИЯ ====================

@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")
    await load_database()


@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    role = guild.get_role(ACTIVE_ROLE_ID)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    command_channel = bot.get_channel(COMMAND_CHANNEL_ID)

    # Вход в голос
    if before.channel is None and after.channel is not None:
        if role not in member.roles:
            await member.add_roles(role)
        voice_times[member.id] = datetime.now()

        if log_channel:
            await log_channel.send(f"✅ {member} зашёл в {after.channel}, роль выдана.")
        if command_channel:
            await command_channel.send(f"🎧 {member.mention}, добро пожаловать в {after.channel.mention}!")

    # Выход из голоса
    if before.channel is not None and after.channel is None:
        if role in member.roles:
            await member.remove_roles(role)

        if member.id in voice_times:
            join_time = voice_times.pop(member.id)
            minutes = int((datetime.now() - join_time).total_seconds() // 60)

            if minutes > 0:
                money = minutes * MONEY_PER_MINUTE
                balances[member.id] = balances.get(member.id, 0) + money
                await save_database()

                total_balance = balances[member.id]

                if log_channel:
                    await log_channel.send(
                        f"❌ {member} вышел из {before.channel}, роль снята. "
                        f"Начислено {minutes} мин × {MONEY_PER_MINUTE} = **{money}** монет. "
                        f"Итоговый баланс: **{total_balance}**."
                    )
                if command_channel:
                    await command_channel.send(
                        f"💰 {member.mention}, тебе начислено **{money}** монет "
                        f"(за {minutes} мин). Текущий баланс: **{total_balance}**."
                    )
            else:
                if log_channel:
                    await log_channel.send(f"❌ {member} вышел слишком быстро (<1 минуты). Ничего не начислено.")
                if command_channel:
                    await command_channel.send(f"⚠️ {member.mention}, ты был в войсе меньше минуты, монеты не начислены.")


# ==================== КОМАНДЫ ====================

@bot.command(name="баланс")
async def balance_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    balance = balances.get(member.id, 0)
    logging.info(f"Команда *баланс вызвана пользователем {ctx.author} для {member} → {balance}")
    await ctx.send(f"💰 Баланс {member.mention}: **{balance}** монет.")


async def change_balance(member: discord.Member, amount: int):
    """Меняет баланс (может быть отрицательным), сохраняет"""
    old_balance = balances.get(member.id, 0)
    new_balance = old_balance + amount
    if new_balance < 0:
        return False, old_balance

    balances[member.id] = new_balance
    await save_database()
    return True, new_balance


@bot.command(name="givemoney")
@commands.has_permissions(administrator=True)
async def givemoney(ctx, amount: int, member: discord.Member):
    success, total_balance = await change_balance(member, amount)

    if not success:
        await ctx.send(f"⚠️ Нельзя уменьшить баланс {member.mention} ниже нуля! "
                       f"(текущий баланс: {total_balance})")
        return

    if amount > 0:
        await ctx.send(f"✅ {member.mention} получил {amount} монет. Баланс: {total_balance}")
    elif amount < 0:
        await ctx.send(f"✅ У {member.mention} забрали {-amount} монет. Баланс: {total_balance}")
    else:
        await ctx.send(f"⚠️ Изменение на 0 монет не имеет смысла.")


@bot.command(name="cleardb")
@commands.has_permissions(administrator=True)
async def cleardb(ctx):
    """Полностью очищает базу данных (ставит всем 0)"""
    global balances
    balances = {}
    await save_database()
    await ctx.send("✅ База очищена. Все балансы сброшены на 0.")


# ==================== ЗАПУСК ====================
bot.run(TOKEN)
