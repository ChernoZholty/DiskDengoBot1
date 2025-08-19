import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from datetime import datetime
import os

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

# Сколько монет за минуту
MONEY_PER_MINUTE = 1

@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")


# --- функции для "базы" ---
async def load_balances():
    """Загружаем все балансы из канала"""
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        logging.error("❌ Канал для базы не найден!")
        return

    async for msg in db_channel.history(limit=None, oldest_first=True):
        try:
            user_id, balance = msg.content.split(":")
            balances[int(user_id)] = int(balance)
        except ValueError:
            continue


async def save_balance(user_id: int):
    """Обновляем/создаём сообщение с балансом"""
    db_channel = bot.get_channel(DB_CHANNEL_ID)
    if not db_channel:
        logging.error("❌ Канал для базы не найден!")
        return

    # ищем сообщение с балансом этого юзера
    async for msg in db_channel.history(limit=None):
        if msg.content.startswith(f"{user_id}:"):
            await msg.edit(content=f"{user_id}:{balances[user_id]}")
            return

    # если не нашли, создаём новое
    await db_channel.send(f"{user_id}:{balances[user_id]}")


@bot.event
async def on_ready():
    await load_balances()
    print(f"Бот запущен как {bot.user}, загружено {len(balances)} балансов.")


@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    role = guild.get_role(ACTIVE_ROLE_ID)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    command_channel = bot.get_channel(COMMAND_CHANNEL_ID)

    if before.channel is None and after.channel is not None:  # вход
        if role not in member.roles:
            await member.add_roles(role)
        voice_times[member.id] = datetime.now()
        if log_channel:
            await log_channel.send(f"✅ {member} зашёл в {after.channel}, роль выдана.")

    if before.channel is not None and after.channel is None:  # выход
        if role in member.roles:
            await member.remove_roles(role)

        if member.id in voice_times:
            join_time = voice_times.pop(member.id)
            minutes = int((datetime.now() - join_time).total_seconds() // 60)

            if minutes > 0:
                money = minutes * MONEY_PER_MINUTE
                balances[member.id] = balances.get(member.id, 0) + money
                await save_balance(member.id)  # сохраняем в "базу"

                total_balance = balances[member.id]
                if command_channel:
                    await command_channel.send(
                        f"💰 {member.mention}, тебе начислено **{money}** монет "
                        f"(за {minutes} мин). Баланс: **{total_balance}**."
                    )
            else:
                if command_channel:
                    await command_channel.send(
                        f"⚠️ {member.mention}, был в войсе <1 мин. Монеты не начислены."
                    )


@bot.command(name="баланс")
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    balance = balances.get(member.id, 0)
    await ctx.send(f"💰 Баланс {member.mention}: **{balance}** монет.")


@bot.command(name="givemoney")
@commands.has_permissions(administrator=True)
async def givemoney(ctx, amount: int, member: discord.Member):
    if amount <= 0:
        await ctx.send("⚠️ Сумма должна быть положительной!")
        return

    balances[member.id] = balances.get(member.id, 0) + amount
    await save_balance(member.id)  # сохраняем в "базу"

    total_balance = balances[member.id]
    await ctx.send(f"✅ {member.mention} получил {amount} монет. Итоговый баланс: {total_balance}")


bot.run(TOKEN)
