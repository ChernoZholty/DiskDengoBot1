import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from datetime import datetime
import os
import json
import random
import asyncio
from flask import Flask
from threading import Thread
from discord.ui import View, Button

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
ORDERS_CHANNEL_ID = 1408282847185338418 # Канал для заказов

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
async def balance(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author

    bal = balances.get(member.id, 0)
    
    # Используем display_name, чтобы выводить то имя, которое видно другим пользователям на сервере
    msg = await ctx.send(f"\U0001F4B0 Баланс {member.display_name}: {bal} монет")

    # Удаляем сообщение с балансом через 15 секунд
    await msg.delete(delay=15)

    # Удаляем сообщение с командой пользователя (если у бота есть права)
    try:
        await ctx.message.delete(delay=15)
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

@bot.command(name="победа")
async def victory_cmd(ctx, battle_id: str, winner: str):
    judge_role = ctx.guild.get_role(JUDGE_ROLE_ID)
    if judge_role not in ctx.author.roles:
        await ctx.send("❌ Только Судья может завершать батлы.")
        return

    if battle_id not in active_battles:
        await ctx.send("⚠️ Батл с таким ID не найден.")
        return

    battle = active_battles[battle_id]
    team1 = battle["team1"]
    team2 = battle["team2"]

    if not team1 or not team2:
        await ctx.send("⚠️ Обе команды должны быть зарегистрированы.")
        return

    if winner.lower() == "батл1":
        winners, losers = team1, team2
    elif winner.lower() == "батл2":
        winners, losers = team2, team1
    else:
        await ctx.send("⚠️ Укажи победителя: батл1 или батл2.")
        return

    total_bank = 0
    # С каждого проигравшего снимаем 50% его баланса
    for m in losers:
        bal = balances.get(m.id, 0)
        penalty = bal // 2
        balances[m.id] = bal - penalty
        total_bank += penalty
        await save_database()

    # Делим банк между победителями
    reward_each = total_bank // len(winners)
    for m in winners:
        balances[m.id] = balances.get(m.id, 0) + reward_each
        await save_database()

    # Итоговое сообщение
    winners_list = ", ".join(m.mention for m in winners)
    losers_list = ", ".join(m.mention for m in losers)
    await ctx.send(
        f"✅ Батл {battle_id} завершён!\n"
        f"🏆 Победители ({winner}): {winners_list} (+{reward_each} монет каждому)\n"
        f"💀 Проигравшие: {losers_list} (минус 50% баланса)\n"
        f"💰 Общий банк: {total_bank} монет"
    )

    # Удаляем батл
    del active_battles[battle_id]

from discord.ui import View, Button

# ==================== БАТЛЫ С МЕСТАМИ (плюс/минус) ====================
active_battles_places = {}

@bot.command(name="батл-места")
async def create_battle_places(ctx, battle_id: str):
    judge_role = ctx.guild.get_role(JUDGE_ROLE_ID)
    if judge_role not in ctx.author.roles:
        await ctx.send("❌ Только Судья может создавать такие батлы.")
        return

    if battle_id in active_battles_places:
        await ctx.send(f"⚠️ Батл с ID {battle_id} уже существует.")
        return

    active_battles_places[battle_id] = {"teams": {}}
    await ctx.send(f"✅ Создан батл с местами. ID: {battle_id}")


# --- Кнопка для вступления/выхода ---
class JoinTeamButton(View):
    def __init__(self, battle_id, team_number):
        super().__init__(timeout=None)
        self.battle_id = battle_id
        self.team_number = team_number

    @discord.ui.button(label="Вступить", style=discord.ButtonStyle.green)
    async def join(self, interaction: discord.Interaction, button: Button):
        team = active_battles_places[self.battle_id]["teams"][self.team_number]
        if interaction.user in team["members"]:
            await interaction.response.send_message("⚠️ Ты уже в этой команде.", ephemeral=True)
            return
        team["members"].append(interaction.user)
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} вступил(а) в команду {self.team_number} ({self.battle_id})",
            ephemeral=True
        )

    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.red)
    async def leave(self, interaction: discord.Interaction, button: Button):
        team = active_battles_places[self.battle_id]["teams"][self.team_number]
        if interaction.user not in team["members"]:
            await interaction.response.send_message("⚠️ Ты не в этой команде.", ephemeral=True)
            return
        team["members"].remove(interaction.user)
        await interaction.response.send_message(
            f"🚪 {interaction.user.mention} покинул(а) команду {self.team_number} ({self.battle_id})",
            ephemeral=True
        )


@bot.command(name="батл-места-команда")
async def add_team_places(ctx, team_number: int, battle_id: str, percent: str):
    judge_role = ctx.guild.get_role(JUDGE_ROLE_ID)
    if judge_role not in ctx.author.roles:
        await ctx.send("❌ Только Судья может добавлять команды.")
        return

    if battle_id not in active_battles_places:
        await ctx.send("⚠️ Указанный ID батла не найден.")
        return

    try:
        sign = percent[0]
        value = int(percent[1:].replace("%", ""))
    except:
        await ctx.send("⚠️ Ошибка в формате. Используй +N% или -N%.")
        return

    if sign not in ["+", "-"]:
        await ctx.send("⚠️ Используй знак + или - перед числом процентов.")
        return

    # Создаём команду (участников пока нет, будут вступать через кнопку)
    active_battles_places[battle_id]["teams"][team_number] = {
        "members": [],
        "sign": sign,
        "percent": value
    }

    # Отправляем сообщение с кнопками
    if sign == "+":
        await ctx.send(
            f"✅ Создана команда {team_number} (победители) для батла {battle_id}.\n"
            f"🏆 Получат {value}% от банка.\n👥 Жмите кнопку, чтобы вступить или выйти:",
            view=JoinTeamButton(battle_id, team_number)
        )
    else:
        await ctx.send(
            f"✅ Создана команда {team_number} (проигравшие) для батла {battle_id}.\n"
            f"💸 Потеряют {value}% от баланса.\n👥 Жмите кнопку, чтобы вступить или выйти:",
            view=JoinTeamButton(battle_id, team_number)
        )


@bot.command(name="батл-места-конец")
async def end_battle_places(ctx, battle_id: str):
    judge_role = ctx.guild.get_role(JUDGE_ROLE_ID)
    if judge_role not in ctx.author.roles:
        await ctx.send("❌ Только Судья может завершать батлы.")
        return

    if battle_id not in active_battles_places:
        await ctx.send("⚠️ Батл с таким ID не найден.")
        return

    battle = active_battles_places[battle_id]
    total_bank = 0
    results = []

    # --- Снимаем монеты с проигравших ---
    for team_number, team in battle["teams"].items():
        if team["sign"] == "-":
            for member in team["members"]:
                balance = balances.get(member.id, 0)
                take = balance * team["percent"] // 100
                if take > 0:
                    success, new_balance = await change_balance(member, -take)
                    if success:
                        total_bank += take
                        results.append(f"💸 {member.mention} потерял {take} монет (баланс {new_balance})")

    # --- Раздаём монеты победителям ---
    for team_number, team in battle["teams"].items():
        if team["sign"] == "+" and team["members"]:
            reward_total = total_bank * team["percent"] // 100
            reward_per_member = reward_total // len(team["members"])
            for member in team["members"]:
                if reward_per_member > 0:
                    success, new_balance = await change_balance(member, reward_per_member)
                    if success:
                        results.append(f"🏆 {member.mention} получил {reward_per_member} монет (баланс {new_balance})")

    # --- Итог ---
    del active_battles_places[battle_id]
    await ctx.send(
        f"✅ Батл {battle_id} завершён!\n"
        f"💰 Общий банк: {total_bank}\n\n" +
        ("\n".join(results) if results else "Никто не участвовал.")
    )


# Меню товаров
shop_items = [
    {"name": "Призыв меня в голосовой канал на (как минимум) 5 минут", "price": 50, "emoji": "🎤"},
    {"name": "Собственная роль", "price": 1000, "emoji": "📜"},
    {"name": "Дать роль другому участнику", "price": 1500, "emoji": "🐉"},
    {"name": "Убрать чужую роль", "price": 500, "emoji": "😭"},
    {"name": "Выделенная роль", "price": 2500, "emoji": "🤩"},
    {"name": "Убрать (Лайк) ((Один раз за вайп))", "price": 5000, "emoji": "🙏"},
    {"name": "Попасть в титры видео", "price": 10000, "emoji": "🏅"},
]

@bot.command(name="магазин")
async def shop_cmd(ctx):
    embed = discord.Embed(
        title="🛒 Магазин",
        description="Для покупки: нажмите на реакцию товара и ✅ для подтверждения. Сообщение исчезнет через 1 час.",
        color=discord.Color.gold()
    )

    description = ""
    for item in shop_items:
        description += f"{item['emoji']} **{item['name']}** — {item['price']} монет\n"
    embed.add_field(name="Товары", value=description, inline=False)

    shop_msg = await ctx.send(embed=embed)

    # Добавляем реакции
    for item in shop_items:
        await shop_msg.add_reaction(item['emoji'])
    await shop_msg.add_reaction("✅")

    def check(reaction, user):
        return (
            user != bot.user
            and reaction.message.id == shop_msg.id
        )

    user_choices = {}

    try:
        while True:
            reaction, user = await bot.wait_for("reaction_add", timeout=3600, check=check)

            if str(reaction.emoji) == "✅":
                if user.id not in user_choices:
                    await ctx.send(f"⚠️ {user.mention}, сначала выбери товар!")
                    continue

                item = user_choices[user.id]
                price = item["price"]
                bal = balances.get(user.id, 0)

                if bal < price:
                    await ctx.send(f"❌ {user.mention}, недостаточно монет для покупки **{item['name']}**.")
                    continue

                # Списываем деньги
                balances[user.id] = bal - price
                await save_database()

                # Генерируем номер заказа
                order_id = f"N{random.randint(1000, 9999)}"

                # Сообщение в канал заказов
                orders_channel = bot.get_channel(ORDERS_CHANNEL_ID)
                await orders_channel.send(
                    f"📦 Заказ {order_id}\n👤 Покупатель: {user.mention}\n"
                    f"🛍️ Товар: **{item['name']}** ({price} монет)"
                )

                # Ответ пользователю
                confirm_msg = await ctx.send(
                    f"✅ {user.mention}, заказ {order_id} оформлен!\n"
                    f"Ты купил **{item['name']}** за {price} монет."
                )

            else:
                # Пользователь выбрал товар
                for item in shop_items:
                    if str(reaction.emoji) == item["emoji"]:
                        user_choices[user.id] = item
                        await ctx.send(f"🛒 {user.mention}, выбран товар: **{item['name']}** ({item['price']} монет).")
                        break

    except asyncio.TimeoutError:
        # Удаляем сообщение магазина по истечении времени
        try:
            await shop_msg.delete()
        except:
            pass


# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    keep_alive()  # Запускаем веб-сервер для UptimeRobot
    bot.run(TOKEN)
