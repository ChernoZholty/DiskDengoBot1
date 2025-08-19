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

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="*", intents=intents)

# словарь для хранения времени входа
voice_times = {}   # {member_id: datetime}
# словарь для хранения балансов
balances = {}      # {member_id: int}

# Сколько монет за минуту
MONEY_PER_MINUTE = 1

@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")


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


# Команда для проверки баланса
@bot.command(name="баланс")
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    balance = balances.get(member.id, 0)
    await ctx.send(f"💰 Баланс {member.mention}: **{balance}** монет.")


# Команда для ручной выдачи монет
@bot.command(name="givemoney")
@commands.has_permissions(administrator=True)  # только админам
async def givemoney(ctx, amount: int, member: discord.Member):
    if amount <= 0:
        await ctx.send("⚠️ Сумма должна быть положительной!")
        return

    balances[member.id] = balances.get(member.id, 0) + amount
    total_balance = balances[member.id]

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    command_channel = bot.get_channel(COMMAND_CHANNEL_ID)

    if log_channel:
        await log_channel.send(f"🛠 {ctx.author} начислил {amount} монет пользователю {member}. Итоговый баланс: {total_balance}.")
    if command_channel:
        await command_channel.send(f"💰 {member.mention}, тебе вручную добавили **{amount}** монет. Итоговый баланс: **{total_balance}**.")

    await ctx.send(f"✅ {member.mention} получил {amount} монет. Итоговый баланс: {total_balance}")


bot.run(TOKEN)