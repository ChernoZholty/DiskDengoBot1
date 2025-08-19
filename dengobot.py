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


# ==================== РАБОТА С БАЗОЙ (одно JSON-сообщение) ====================

async def load_database():
    """Находит/создаёт единственное JSON-сообщение с базой и загружает balances."""
    global balances, db_message
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        logging.error("DB_CHANNEL_ID неверный или бот не видит канал.")
        return

    messages = [m async for m in db_channel.history(limit=None, oldest_first=True)]
    last_valid_msg = None
    last_valid_data = None
    to_delete = []

    # Находим последнее валидное JSON-сообщение-словарь, остальное удаляем
    for m in messages:
        try:
            data = json.loads(m.content)
            if isinstance(data, dict):
                if last_valid_msg:
                    to_delete.append(last_valid_msg)  # старое валидное удалим, оставим более новое
                last_valid_msg = m
                last_valid_data = data
            else:
                to_delete.append(m)
        except json.JSONDecodeError:
            to_delete.append(m)

    # Удаляем мусор/дубли (если есть права)
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

    # Если валидного сообщения нет — создаём новое пустое
    new_msg = await db_channel.send(json.dumps({}))
    db_message = new_msg
    balances = {}
    logging.info("Создано новое JSON-сообщение для базы.")


async def save_database():
    """Перезаписывает единственное JSON-сообщение актуальным словарём balances."""
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

    # ключи в JSON должны быть строками
    payload = {str(k): int(v) for k, v in balances.items()}

    try:
        await db_message.edit(content=json.dumps(payload, ensure_ascii=False))
        logging.info("База обновлена.")
    except discord.NotFound:
        # сообщение удалили вручную — создаём заново
        db_message = await db_channel.send(json.dumps(payload, ensure_ascii=False))
        logging.info("База пересоздана (старое сообщение отсутствовало).")
    except Exception as e:
        logging.error(f"Не удалось сохранить базу: {e}")


async def change_balance(member: discord.Member, amount: int):
    """Меняет баланс (amount может быть отрицательным). Не допускает минуса. Сохраняет в базу."""
    old_balance = balances.get(member.id, 0)
    new_balance = old_balance + amount
    if new_balance < 0:
        return False, old_balance

    balances[member.id] = new_balance
    await save_database()
    return True, new_balance


# ==================== СОБЫТИЯ ====================

@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")
    await load_database()


@bot.event
async def on_voice_state_update(member, before, after):
    role = member.guild.get_role(ACTIVE_ROLE_ID)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    command_channel = bot.get_channel(COMMAND_CHANNEL_ID)

    # Вход в голос
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

    # Выход из голоса
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
    logging.info(f"*баланс от {ctx.author} → {member} = {balance}")
    await ctx.send(f"💰 Баланс {member.mention}: **{balance}** монет.")


@bot.command(name="givemoney")
@commands.has_permissions(administrator=True)
async def givemoney_cmd(ctx, amount: int, member: discord.Member):
    """Положительные суммы — начисляем, отрицательные — списываем (не ниже 0)."""
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
    """Удобное списание: *takemoney 50 @user"""
    if amount <= 0:
        await ctx.send("⚠️ Укажи положительную сумму для списания, например: *takemoney 50 @user")
        return
    await givemoney_cmd(ctx, -amount, member)


@bot.command(name="cleardb")
@commands.has_permissions(administrator=True)
async def cleardb_cmd(ctx):
    """Полностью очищает базу: удаляет все сообщения в DB-канале и создаёт пустую JSON-базу."""
    global balances, db_message
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        await ctx.send("❌ Канал базы данных не найден.")
        return

    # удаляем все сообщения базы
    msgs = [m async for m in db_channel.history(limit=None)]
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass

    # создаём пустую
    balances = {}
    db_message = await db_channel.send(json.dumps({}))
    await ctx.send("✅ База очищена. Все балансы сброшены на 0.")


# ==================== ЗАПУСК ====================
bot.run(TOKEN)
