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
LOG_CHANNEL_ID = 1407081468525805748
COMMAND_CHANNEL_ID = 1407081468525805748
DB_CHANNEL_ID = 1407213722824343602
LEADERBOARD_CHANNEL_ID = 1407421547785883749
ORDERS_CHANNEL_ID = 1408282847185338418
JUDGES_CHANNEL_ID = 1408318242916798505
DUELS_CHANNEL_ID = 1409123208543604786  # Канал для анкет дуэлей

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="*", intents=intents)

voice_times = {}
balances = {}
db_messages = {}
MONEY_PER_MINUTE = 1
active_duels = {}  # Для хранения данных о дуэлях

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
    await update_leaderboard()
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
    await update_leaderboard()

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
            await command_channel.send(f"🎧 {member.display_name}, добро пожаловать в {after.channel.mention}!")
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
                        f"💰 {member.display_name}, тебе начислено **{money}** монет "
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
                        f"⚠️ {member.display_name}, был в войсе <1 мин. Монеты не начислены."
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
    msg = await ctx.send(f"\U0001F4B0 Баланс {member.display_name}: {bal} монет")
    await msg.delete(delay=15)
    try:
        await ctx.message.delete(delay=15)
    except discord.Forbidden:
        pass

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

# ==================== БАТЛЫ С МЕСТАМИ (плюс/минус) ====================
active_battles_places = {}

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

@bot.command(name="батл-места")
async def create_battle_places(ctx, battle_id: str):
    JUDGE_ROLE_ID = 1407534809034784879
    judge_role = ctx.guild.get_role(JUDGE_ROLE_ID)
    if judge_role not in ctx.author.roles:
        await ctx.send("❌ Только Судья может создавать такие батлы.")
        return

    if battle_id in active_battles_places:
        await ctx.send(f"⚠️ Батл с ID {battle_id} уже существует.")
        return

    active_battles_places[battle_id] = {"teams": {}}
    await ctx.send(f"✅ Создан батл с местами. ID: {battle_id}")

@bot.command(name="батл-места-команда")
async def add_team_places(ctx, team_number: int, battle_id: str, percent: str):
    JUDGE_ROLE_ID = 1407534809034784879
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

    active_battles_places[battle_id]["teams"][team_number] = {
        "members": [],
        "sign": sign,
        "percent": value
    }

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
    JUDGE_ROLE_ID = 1407534809034784879
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

    for team_number, team in battle["teams"].items():
        if team["sign"] == "-":
            for member in team["members"]:
                balance_val = balances.get(member.id, 0)
                take = balance_val * team["percent"] // 100
                if take > 0:
                    success, new_balance = await change_balance(member, -take)
                    if success:
                        total_bank += take
                        results.append(f"💸 {member.mention} потерял {take} монет (баланс {new_balance})")

    for team_number, team in battle["teams"].items():
        if team["sign"] == "+" and team["members"]:
            reward_total = total_bank * team["percent"] // 100
            reward_per_member = reward_total // len(team["members"])
            for member in team["members"]:
                if reward_per_member > 0:
                    success, new_balance = await change_balance(member, reward_per_member)
                    if success:
                        results.append(f"🏆 {member.mention} получил {reward_per_member} монет (баланс {new_balance})")

    del active_battles_places[battle_id]
    await ctx.send(
        f"✅ Батл {battle_id} завершён!\n"
        f"💰 Общий банк: {total_bank}\n\n" +
        ("\n".join(results) if results else "Никто не участвовал.")
    )

# ==================== ДУЭЛИ И СТАВКИ ====================
active_duels = {}
active_bets = {}  # {duel_id: {"author": {user_id: amount}, "opponent": {user_id: amount}}}

class AcceptDuelView(View):
    def __init__(self, duel_id):
        super().__init__(timeout=None)
        self.duel_id = duel_id

    @discord.ui.button(label="Принять дуэль", style=discord.ButtonStyle.green)
    async def accept_duel(self, interaction: discord.Interaction, button: Button):
        duel = active_duels.get(self.duel_id)
        if not duel:
            await interaction.response.send_message("Дуэль не найдена или уже завершена.", ephemeral=True)
            return
        
        if interaction.user.id == duel["author"].id:
            await interaction.response.send_message("Нельзя принять свою же дуэль!", ephemeral=True)
            return
        
        if duel["accepted_by"]:
            await interaction.response.send_message("Дуэль уже принята другим участником.", ephemeral=True)
            return

        duel["accepted_by"] = interaction.user
        thread = await interaction.message.create_thread(name=f"Дуэль {duel['author'].display_name} vs {interaction.user.display_name}")
        duel["thread"] = thread
        
        # Инициализируем ставки для этой дуэли
        active_bets[self.duel_id] = {"author": {}, "opponent": {}}

        # Отправляем кнопки для выбора победителя сразу после принятия дуэли
        embed = discord.Embed(
            title="Выберите победителя дуэли",
            description="Каждый участник может выбрать только одного победителя",
            color=discord.Color.blue()
        )
        vote_message = await thread.send(embed=embed, view=VoteWinnerView(self.duel_id))
        duel["vote_message"] = vote_message  # Сохраняем сообщение с голосованием

        # Уведомляем судей (опционально)
        judges_channel = bot.get_channel(JUDGES_CHANNEL_ID)
        if judges_channel:
            embed = discord.Embed(
                title="Новая дуэль ожидает судью",
                description=f"Дуэль между {duel['author'].mention} и {interaction.user.mention}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Дисциплина", value=duel["discipline"])
            embed.add_field(name="Описание", value=duel["description"])
            embed.set_footer(text=f"ID дуэли: {self.duel_id}")
            judge_message = await judges_channel.send(embed=embed, view=TakeDuelView(self.duel_id))
            duel["judge_message"] = judge_message  # Сохраняем сообщение для судей

        await interaction.response.send_message(f"Вы приняли дуэль! Обсуждение в {thread.mention}.", ephemeral=True)
        await interaction.message.edit(view=None)

class TakeDuelView(View):
    def __init__(self, duel_id):
        super().__init__(timeout=None)
        self.duel_id = duel_id

    @discord.ui.button(label="Взяться за дуэль", style=discord.ButtonStyle.blurple)
    async def take_duel(self, interaction: discord.Interaction, button: Button):
        JUDGE_ROLE_ID = 1407534809034784879
        judge_role = interaction.guild.get_role(JUDGE_ROLE_ID)
        if judge_role not in interaction.user.roles:
            await interaction.response.send_message("Только судьи могут браться за дуэли.", ephemeral=True)
            return

        duel = active_duels.get(self.duel_id)
        if not duel:
            await interaction.response.send_message("Дуэль не найдена.", ephemeral=True)
            return

        duel["judge"] = interaction.user
        await interaction.response.send_message(f"Вы взялись судить эту дуэль!", ephemeral=True)
        
        # Обновляем сообщение для судей
        if duel.get("judge_message"):
            embed = duel["judge_message"].embeds[0]
            embed.description = f"Дуэль между {duel['author'].mention} и {duel['accepted_by'].mention}\nСудья: {interaction.user.mention}"
            await duel["judge_message"].edit(embed=embed, view=None)
        
        # Если голоса уже разделились, показываем кнопки судье
        if duel.get("votes_split", False) and duel.get("thread"):
            embed = discord.Embed(
                title="Голоса разделились - требуется решение судьи",
                description="Пожалуйста, выберите победителя дуэли",
                color=discord.Color.orange()
            )
            await duel["thread"].send(embed=embed, view=JudgeDecisionView(self.duel_id))

class VoteWinnerView(View):
    def __init__(self, duel_id):
        super().__init__(timeout=None)
        self.duel_id = duel_id
        self.votes = {}
        self.voted_users = set()

    @discord.ui.button(label="Победитель автор", style=discord.ButtonStyle.green, emoji="✅")
    async def vote_author(self, interaction: discord.Interaction, button: Button):
        await self.process_vote(interaction, "author")

    @discord.ui.button(label="Победитель оппонент", style=discord.ButtonStyle.green, emoji="✅")
    async def vote_opponent(self, interaction: discord.Interaction, button: Button):
        await self.process_vote(interaction, "opponent")

    async def process_vote(self, interaction, vote_type):
        duel = active_duels.get(self.duel_id)
        if not duel:
            await interaction.response.send_message("Дуэль не найдена.", ephemeral=True)
            return

        if interaction.user.id not in [duel["author"].id, duel["accepted_by"].id]:
            await interaction.response.send_message("Только участники дуэли могут голосовать.", ephemeral=True)
            return

        # Проверяем, не голосовал ли уже пользователь
        if interaction.user.id in self.voted_users:
            await interaction.response.send_message("Вы уже проголосовали!", ephemeral=True)
            return

        # Записываем голос и добавляем пользователя в список проголосовавших
        self.votes[interaction.user.id] = vote_type
        self.voted_users.add(interaction.user.id)

        # Создаем новое представление с отключенной кнопкой только для этого пользователя
        new_view = VoteWinnerView(self.duel_id)
        new_view.votes = self.votes.copy()
        new_view.voted_users = self.voted_users.copy()
        
        # Отключаем кнопки для этого пользователя
        if interaction.user.id in new_view.voted_users:
            # Не отключаем кнопки для всех, только сохраняем состояние голосования
            pass

        # Добавляем реакцию подтверждения
        await interaction.response.send_message("✅ Ваш голос учтен!", ephemeral=True)

        # Проверяем, есть ли два голоса
        if len(self.votes) == 2:
            votes_list = list(self.votes.values())
            if votes_list[0] == votes_list[1]:
                winner_type = votes_list[0]
                if winner_type == "author":
                    winner = duel["author"]
                    loser = duel["accepted_by"]
                else:
                    winner = duel["accepted_by"]
                    loser = duel["author"]

                # Переводим 50% баланса проигравшего победителю
                loser_balance = balances.get(loser.id, 0)
                amount = loser_balance // 2
                if amount > 0:
                    success, new_balance = await change_balance(winner, amount)
                    if success:
                        await change_balance(loser, -amount)
                        # Обрабатываем ставки
                        await self.process_bets(winner_type, duel, interaction)
                        # Убираем кнопки полностью после завершения голосования
                        await interaction.message.edit(view=None)
                        await interaction.followup.send(
                            f"🏆 Победитель: {winner.mention}\n"
                            f"💸 С баланса {loser.mention} списано {amount} монет\n"
                            f"💰 Баланс {winner.mention}: {new_balance} монет"
                        )
                else:
                    # Обрабатываем ставки
                    await self.process_bets(winner_type, duel, interaction)
                    # Убираем кнопки полностью после завершения голосования
                    await interaction.message.edit(view=None)
                    await interaction.followup.send("Недостаточно монет для перевода.")
                
                # Удаляем дуэль из активных
                if self.duel_id in active_duels:
                    del active_duels[self.duel_id]
                # Удаляем ставки для этой дуэли
                if self.duel_id in active_bets:
                    del active_bets[self.duel_id]
            else:
                # Голоса разделились - отмечаем это и ждем судью
                duel["votes_split"] = True
                # Убираем кнопки полностью после завершения голосования
                await interaction.message.edit(view=None)
                await interaction.followup.send("Голоса разделились! Ожидаем решение судьи.")
                
                # Если судья уже назначен, отправляем ему кнопки для решения
                if duel.get("judge") and duel.get("thread"):
                    embed = discord.Embed(
                        title="Голоса разделились - требуется решение судьи",
                        description="Пожалуйста, выберите победителя дуэли",
                        color=discord.Color.orange()
                    )
                    await duel["thread"].send(embed=embed, view=JudgeDecisionView(self.duel_id))
    
    async def process_bets(self, winner_type, duel, interaction):
        """Обрабатывает ставки после завершения дуэли"""
        if self.duel_id not in active_bets:
            return
            
        bets = active_bets[self.duel_id]
        total_author_bets = sum(bets["author"].values())
        total_opponent_bets = sum(bets["opponent"].values())
        
        # Определяем победившую и проигравшую стороны
        winning_side = "author" if winner_type == "author" else "opponent"
        losing_side = "opponent" if winner_type == "author" else "author"
        
        total_winning_bets = sum(bets[winning_side].values())
        total_losing_bets = sum(bets[losing_side].values())
        
        # Общий банк (все ставки)
        total_bank = total_author_bets + total_opponent_bets
        
        # Если есть ставки на победившую сторону, распределяем выигрыш
        if total_winning_bets > 0 and total_losing_bets > 0:
            # Коэффициент выигрыша: доля от проигравшего банка на каждого победителя
            win_multiplier = total_losing_bets / total_winning_bets
            
            # Выплачиваем выигрыш (ставка возвращается + доля от проигравшего банка)
            for user_id, amount in bets[winning_side].items():
                win_amount = amount + int(amount * win_multiplier)
                member = interaction.guild.get_member(user_id)
                if member:
                    await change_balance(member, win_amount)
                    try:
                        await member.send(f"🎉 Вы выиграли {win_amount} монет на дуэли {self.duel_id} (ставка: {amount} монет, выигрыш: {int(amount * win_multiplier)} монет)!")
                    except:
                        pass  # Не удалось отправить ЛС
        
        # Если есть только ставки на победившую сторону (нет проигравших ставок)
        elif total_winning_bets > 0:
            # Просто возвращаем ставки
            for user_id, amount in bets[winning_side].items():
                member = interaction.guild.get_member(user_id)
                if member:
                    await change_balance(member, amount)
                    try:
                        await member.send(f"💰 Вам возвращена ставка {amount} монет на дуэли {self.duel_id} (нет проигравших ставок).")
                    except:
                        pass
        
        # Отправляем результаты ставок в тред
        result_text = [
            f"📊 **Результаты ставок на дуэль:**",
            f"🏆 Победитель: {winning_side}",
            f"💰 Общий банк: {total_bank} монет",
            f"📈 Ставок на автора: {total_author_bets} монет",
            f"📈 Ставок на оппонента: {total_opponent_bets} монет",
        ]
        
        if total_winning_bets > 0 and total_losing_bets > 0:
            win_multiplier = total_losing_bets / total_winning_bets
            result_text.append(f"🎯 Коэффициент выигрыша: x{win_multiplier:.2f} (ставка возвращается + выигрыш)")
        elif total_winning_bets > 0:
            result_text.append("💰 Все ставки возвращены (нет проигравших ставок)")
        
        await interaction.followup.send("\n".join(result_text))

class JudgeDecisionView(View):
    def __init__(self, duel_id):
        super().__init__(timeout=None)
        self.duel_id = duel_id

    @discord.ui.button(label="Победитель автор", style=discord.ButtonStyle.green)
    async def decide_author(self, interaction: discord.Interaction, button: Button):
        await self.process_decision(interaction, "author")

    @discord.ui.button(label="Победитель оппонент", style=discord.ButtonStyle.green)
    async def decide_opponent(self, interaction: discord.Interaction, button: Button):
        await self.process_decision(interaction, "opponent")

    async def process_decision(self, interaction, decision):
        duel = active_duels.get(self.duel_id)
        if not duel:
            await interaction.response.send_message("Дуэль не найдена.", ephemeral=True)
            return

        # Проверяем, что решение принимает судья
        JUDGE_ROLE_ID = 1407534809034784879
        judge_role = interaction.guild.get_role(JUDGE_ROLE_ID)
        if judge_role not in interaction.user.roles:
            await interaction.response.send_message("Только судьи могут принимать решение.", ephemeral=True)
            return

        if not duel.get("judge") or interaction.user.id != duel["judge"].id:
            await interaction.response.send_message("Только назначенный судья может принимать решение.", ephemeral=True)
            return

        if decision == "author":
            winner = duel["author"]
            loser = duel["accepted_by"]
            winner_type = "author"
        else:
            winner = duel["accepted_by"]
            loser = duel["author"]
            winner_type = "opponent"

        # Переводим 50% баланса проигравшего победителю
        loser_balance = balances.get(loser.id, 0)
        amount = loser_balance // 2
        if amount > 0:
            success, new_balance = await change_balance(winner, amount)
            if success:
                await change_balance(loser, -amount)
                # Обрабатываем ставки
                await self.process_bets(winner_type, duel, interaction)
                await interaction.response.send_message(
                    f"🏆 Победитель: {winner.mention}\n"
                    f"💸 С баланса {loser.mention} списано {amount} монет\n"
                    f"💰 Баланс {winner.mention}: {new_balance} монет"
                )
        else:
            # Обрабатываем ставки
            await self.process_bets(winner_type, duel, interaction)
            await interaction.response.send_message("Недостаточно монет для перевода.")

        # Удаляем дуэль из активных
        if self.duel_id in active_duels:
            del active_duels[self.duel_id]
        # Удаляем ставки для этой дуэли
        if self.duel_id in active_bets:
            del active_bets[self.duel_id]
        
        # Убираем кнопки
        await interaction.message.edit(view=None)
    
    async def process_bets(self, winner_type, duel, interaction):
        """Обрабатывает ставки после завершения дуэли"""
        if self.duel_id not in active_bets:
            return
            
        bets = active_bets[self.duel_id]
        total_author_bets = sum(bets["author"].values())
        total_opponent_bets = sum(bets["opponent"].values())
        
        # Определяем победившую и проигравшую стороны
        winning_side = "author" if winner_type == "author" else "opponent"
        losing_side = "opponent" if winner_type == "author" else "author"
        
        total_winning_bets = sum(bets[winning_side].values())
        total_losing_bets = sum(bets[losing_side].values())
        
        # Общий банк (все ставки)
        total_bank = total_author_bets + total_opponent_bets
        
        # Если есть ставки на победившую сторону, распределяем выигрыш
        if total_winning_bets > 0 and total_losing_bets > 0:
            # Коэффициент выигрыша: доля от проигравшего банка на каждого победителя
            win_multiplier = total_losing_bets / total_winning_bets
            
            # Выплачиваем выигрыш (ставка возвращается + доля от проигравшего банка)
            for user_id, amount in bets[winning_side].items():
                win_amount = amount + int(amount * win_multiplier)
                member = interaction.guild.get_member(user_id)
                if member:
                    await change_balance(member, win_amount)
                    try:
                        await member.send(f"🎉 Вы выиграли {win_amount} монет на дуэли {self.duel_id} (ставка: {amount} монет, выигрыш: {int(amount * win_multiplier)} монет)!")
                    except:
                        pass  # Не удалось отправить ЛС
        
        # Если есть только ставки на победившую сторону (нет проигравших ставок)
        elif total_winning_bets > 0:
            # Просто возвращаем ставки
            for user_id, amount in bets[winning_side].items():
                member = interaction.guild.get_member(user_id)
                if member:
                    await change_balance(member, amount)
                    try:
                        await member.send(f"💰 Вам возвращена ставка {amount} монет на дуэли {self.duel_id} (нет проигравших ставок).")
                    except:
                        pass
        
        # Отправляем результаты ставок в тред
        result_text = [
            f"📊 **Результаты ставок на дуэль:**",
            f"🏆 Победитель: {winning_side}",
            f"💰 Общий банк: {total_bank} монет",
            f"📈 Ставок на автора: {total_author_bets} монет",
            f"📈 Ставок на оппонента: {total_opponent_bets} монет",
        ]
        
        if total_winning_bets > 0 and total_losing_bets > 0:
            win_multiplier = total_losing_bets / total_winning_bets
            result_text.append(f"🎯 Коэффициент выигрыша: x{win_multiplier:.2f} (ставка возвращается + выигрыш)")
        elif total_winning_bets > 0:
            result_text.append("💰 Все ставки возвращены (нет проигравших ставок)")
        
        await interaction.followup.send("\n".join(result_text))

@bot.command(name="дуэль")
async def duel_cmd(ctx):
    # Проверяем, что автор не имеет активной дуэли
    for duel in active_duels.values():
        if duel["author"].id == ctx.author.id:
            await ctx.send("У вас уже есть активная дуэль!", delete_after=15)
            return

    # Запрос дисциплины
    await ctx.send("Введите дисциплину для дуэли:", delete_after=15)
    try:
        discipline_msg = await bot.wait_for(
            'message',
            timeout=60,
            check=lambda m: m.author == ctx.author and m.channel == ctx.channel
        )
        discipline = discipline_msg.content
    except asyncio.TimeoutError:
        await ctx.send("Время вышло!", delete_after=15)
        return

    # Запрос описания
    await ctx.send("Введите описание дуэли:", delete_after=15)
    try:
        description_msg = await bot.wait_for(
            'message',
            timeout=60,
            check=lambda m: m.author == ctx.author and m.channel == ctx.channel
        )
        description = description_msg.content
    except asyncio.TimeoutError:
        await ctx.send("Время вышло!", delete_after=15)
        return

    # Удаляем сообщения
    try:
        await ctx.message.delete()
        await discipline_msg.delete()
        await description_msg.delete()
    except:
        pass

    # Создаем дуэль
    duel_id = f"duel_{random.randint(1000, 9999)}"
    active_duels[duel_id] = {
        "author": ctx.author,
        "discipline": discipline,
        "description": description,
        "accepted_by": None,
        "judge": None,
        "thread": None,
        "vote_message": None,
        "judge_message": None,
        "votes_split": False
    }

    # Отправляем анкету в канал дуэлей
    duels_channel = bot.get_channel(DUELS_CHANNEL_ID)
    if duels_channel:
        embed = discord.Embed(
            title="Новая дуэль",
            description=f"Автор: {ctx.author.mention}",
            color=discord.Color.purple()
        )
        embed.add_field(name="Дисциплина", value=discipline)
        embed.add_field(name="Описание", value=description)
        embed.set_footer(text=f"ID: {duel_id}")
        await duels_channel.send(embed=embed, view=AcceptDuelView(duel_id))

    await ctx.send("Дуэль создана! Анкета отправлена в канал дуэлей.", delete_after=15)

#=========================Ставки========================
@bot.command(name="ставка")
async def bet_cmd(ctx, percent: str, side: str):
    # Проверяем формат процента
    if not percent.endswith('%'):
        await ctx.send("Укажите процент в формате: 10%", delete_after=15)
        return
    
    try:
        percent_value = float(percent[:-1])
        if percent_value <= 0 or percent_value > 100:
            await ctx.send("Процент должен быть больше 0 и не больше 100", delete_after=15)
            return
    except ValueError:
        await ctx.send("Неверный формат процента", delete_after=15)
        return
    
    # Проверяем сторону и преобразуем в английский ключ
    side_lower = side.lower()
    if side_lower == "автор":
        side_key = "author"
    elif side_lower == "оппонент":
        side_key = "opponent"
    else:
        await ctx.send("Укажите сторону: автор или оппонент", delete_after=15)
        return
    
    # Ищем активную дуэль в этом канале (треде)
    duel_id = None
    for did, duel in active_duels.items():
        if duel.get("thread") and duel["thread"].id == ctx.channel.id:
            duel_id = did
            break
    
    if not duel_id:
        await ctx.send("Ставки можно делать только в тредах активных дуэлей!", delete_after=15)
        return
    
    # Проверяем, что дуэль уже принята (есть оба участника)
    duel = active_duels[duel_id]
    if not duel["accepted_by"]:
        await ctx.send("Ставки можно делать только после того, как дуэль принята!", delete_after=15)
        return
    
    # Проверяем, что пользователь не участник дуэли
    if ctx.author.id in [duel["author"].id, duel["accepted_by"].id]:
        await ctx.send("Участники дуэли не могут делать ставки!", delete_after=15)
        return
    
    # Проверяем, что дуэль еще не завершена
    if duel_id not in active_duels:
        await ctx.send("Эта дуэль уже завершена!", delete_after=15)
        return
    
    # Проверяем, что у пользователя достаточно денег
    user_balance = balances.get(ctx.author.id, 0)
    bet_amount = int(user_balance * percent_value / 100)
    
    if bet_amount <= 0:
        await ctx.send("Сумма ставки должна быть больше 0!", delete_after=15)
        return
    
    if user_balance < bet_amount:
        await ctx.send("Недостаточно средств для ставки!", delete_after=15)
        return
    
    # Проверяем, не делал ли пользователь уже ставку на эту дуэль
    if duel_id in active_bets:
        user_bets = active_bets[duel_id]
        if ctx.author.id in user_bets["author"] or ctx.author.id in user_bets["opponent"]:
            await ctx.send("Вы уже сделали ставку на эту дуэль!", delete_after=15)
            return
    
    # Снимаем деньги и регистрируем ставку
    success, new_balance = await change_balance(ctx.author, -bet_amount)
    if not success:
        await ctx.send("Ошибка при списании средств!", delete_after=15)
        return
    
    # Добавляем ставку
    if duel_id not in active_bets:
        active_bets[duel_id] = {"author": {}, "opponent": {}}
    
    active_bets[duel_id][side_key][ctx.author.id] = bet_amount
    
    await ctx.send(
        f"{ctx.author.mention} поставил(а) {bet_amount} монет на {side_lower}.\n"
        f"Новый баланс: {new_balance} монет",
        delete_after=15
    )

# ==================== МАГАЗИН ====================
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
@commands.has_permissions(administrator=True)
async def shop_cmd(ctx):
    embed = discord.Embed(
        title="🛒 Магазин",
        description="Для покупки: нажмите на реакцию товара и ✅ для подтверждения.",
        color=discord.Color.gold()
    )

    description = ""
    for item in shop_items:
        description += f"{item['emoji']} **{item['name']}** — {item['price']} монет\n"
    embed.add_field(name="Товары", value=description, inline=False)

    shop_msg = await ctx.send(embed=embed)

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
                    warn_msg = await ctx.send(f"⚠️ {user.mention}, сначала выбери товар!")
                    await warn_msg.delete(delay=15)
                    continue

                item = user_choices[user.id]
                price = item["price"]
                bal = balances.get(user.id, 0)

                if bal < price:
                    error_msg = await ctx.send(f"❌ {user.mention}, недостаточно монет для покупки **{item['name']}**.")
                    await error_msg.delete(delay=15)
                    continue

                balances[user.id] = bal - price
                await save_database()

                order_id = f"N{random.randint(1000, 9999)}"
                orders_channel = bot.get_channel(ORDERS_CHANNEL_ID)
                await orders_channel.send(
                    f"📦 Заказ {order_id}\n👤 Покупатель: {user.mention}\n"
                    f"🛍️ Товар: **{item['name']}** ({price} монет)"
                )

                confirm_msg = await ctx.send(
                    f"✅ {user.mention}, заказ {order_id} оформлен!\n"
                    f"Ты купил **{item['name']}** за {price} монет."
                )
                await confirm_msg.delete(delay=15)

            else:
                for item in shop_items:
                    if str(reaction.emoji) == item["emoji"]:
                        user_choices[user.id] = item
                        choice_msg = await ctx.send(
                            f"🛒 {user.mention}, выбран товар: **{item['name']}** ({item['price']} монет)."
                        )
                        await choice_msg.delete(delay=15)
                        break
    except asyncio.TimeoutError:
        pass

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
