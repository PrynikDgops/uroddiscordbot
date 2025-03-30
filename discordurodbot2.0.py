import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Получаем токен из переменной окружения
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Переменная окружения DISCORD_BOT_TOKEN не установлена.")

# Настройка интентов
intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True

# Создаем бота; префикс не используется для слеш-команд
bot = commands.Bot(command_prefix="!", intents=intents)

# Файл конфигурации
CONFIG_FILE = "config.json"

def load_config() -> dict:
    default_config = {
        "required_work_time_hours": 8,
        "report_check_period_hours": 24,
        "applicable_roles": [],
        "auto_report_enabled": False,
        "auto_report_channel": None,
        "command_access_users": [],
        "command_access_roles": [],
        "whitelist": [],
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            try:
                config = json.load(f)
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
            except json.JSONDecodeError:
                return default_config
    return default_config

def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f)

# Глобальная конфигурация
config = load_config()

def is_applicable(member: discord.Member) -> bool:
    applicable_roles = config.get("applicable_roles", [])
    if not applicable_roles:
        return True
    return any(role.id in applicable_roles for role in member.roles)

async def allowed_check(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    allowed_users = config.get("command_access_users", [])
    allowed_roles = config.get("command_access_roles", [])
    if interaction.user.id in allowed_users:
        return True
    return any(role.id in allowed_roles for role in interaction.user.roles)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("Ошибка: недостаточно прав для использования этой команды.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Ошибка: {error}", ephemeral=True)

# --- СЛЕШ-КОМАНДЫ (доступ только администраторам/доверенным) ---

@app_commands.command(name="voice_data", description="Выводит данные о голосовых и Stage каналах (JSON).")
@app_commands.check(allowed_check)
async def voice_data(interaction: discord.Interaction, channel: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None):
    await interaction.response.defer(ephemeral=True)
    voice_data_dict = {}
    if channel:
        channels = [channel]
    else:
        channels = interaction.guild.voice_channels + getattr(interaction.guild, "stage_channels", [])
    for vc in channels:
        members = []
        for member in vc.members:
            if is_applicable(member):
                members.append({
                    "id": member.id,
                    "name": member.name,
                    "discriminator": member.discriminator,
                    "display_name": member.display_name,
                })
        voice_data_dict[vc.name] = members
    json_data = json.dumps(voice_data_dict, indent=4, ensure_ascii=False)
    await interaction.followup.send(f"```json\n{json_data}\n```", ephemeral=True)

@app_commands.command(name="message_voice_data", description="Отправляет данные голосовых/Stage каналов отдельными сообщениями.")
@app_commands.check(allowed_check)
async def message_voice_data(interaction: discord.Interaction, channel: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None):
    await interaction.response.defer(ephemeral=True)
    if channel:
        channels = [channel]
    else:
        channels = interaction.guild.voice_channels + getattr(interaction.guild, "stage_channels", [])
    for vc in channels:
        if vc.members:
            member_list = "\n".join([f"{member.display_name} (ID: {member.id})" for member in vc.members if is_applicable(member)])
            msg = f"**Канал:** {vc.name}\n**Участники:**\n{member_list if member_list else 'Нет подходящих участников'}"
        else:
            msg = f"**Канал:** {vc.name}\n**Участники:** Нет участников"
        await interaction.followup.send(msg, ephemeral=True)

@app_commands.command(name="mention_not_in_channel", description="Упоминает пользователей, не находящихся в голосовом/Stage канале.")
@app_commands.check(allowed_check)
async def mention_not_in_channel(interaction: discord.Interaction, channel: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None):
    if channel:
        not_in_channel = [
            member.mention for member in interaction.guild.members
            if (member.voice is None or member.voice.channel != channel)
            and not member.bot and member.id not in config.get("whitelist", [])
            and is_applicable(member)
        ]
    else:
        not_in_channel = [
            member.mention for member in interaction.guild.members
            if member.voice is None and not member.bot and member.id not in config.get("whitelist", [])
            and is_applicable(member)
        ]
    if not not_in_channel:
        await interaction.response.send_message("Все подходящие пользователи находятся в голосовых каналах!", ephemeral=True)
        return
    messages = []
    message = ""
    for mention in not_in_channel:
        if len(message) + len(mention) + 1 > 1900:
            messages.append(message)
            message = mention + " "
        else:
            message += mention + " "
    if message:
        messages.append(message)
    for msg in messages:
        await interaction.followup.send(msg, ephemeral=True)

@app_commands.command(name="whitelist_add", description="Добавляет пользователя в whitelist.")
@app_commands.check(allowed_check)
async def whitelist_add_cmd(interaction: discord.Interaction, member: discord.Member):
    whitelist_list = config.get("whitelist", [])
    if member.id not in whitelist_list:
        whitelist_list.append(member.id)
        config["whitelist"] = whitelist_list
        save_config(config)
        await interaction.response.send_message(f"{member.name}#{member.discriminator} добавлен в whitelist.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{member.name}#{member.discriminator} уже в whitelist.", ephemeral=True)

@app_commands.command(name="whitelist_remove", description="Удаляет пользователя из whitelist.")
@app_commands.check(allowed_check)
async def whitelist_remove_cmd(interaction: discord.Interaction, member: discord.Member):
    whitelist_list = config.get("whitelist", [])
    if member.id in whitelist_list:
        whitelist_list.remove(member.id)
        config["whitelist"] = whitelist_list
        save_config(config)
        await interaction.response.send_message(f"{member.name}#{member.discriminator} удалён из whitelist.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{member.name}#{member.discriminator} не найден в whitelist.", ephemeral=True)

@app_commands.command(name="whitelist_list", description="Выводит список пользователей в whitelist.")
@app_commands.check(allowed_check)
async def whitelist_list_cmd(interaction: discord.Interaction):
    whitelist_list = config.get("whitelist", [])
    if not whitelist_list:
        await interaction.response.send_message("Whitelist пуст.", ephemeral=True)
        return
    members_list = []
    for user_id in whitelist_list:
        member = interaction.guild.get_member(user_id)
        if member:
            members_list.append(member.mention)
        else:
            members_list.append(str(user_id))
    await interaction.response.send_message("Whitelist: " + ", ".join(members_list), ephemeral=True)

@app_commands.command(name="set_required_work_time", description="Устанавливает требуемое время работы (часы).")
@app_commands.check(allowed_check)
async def set_required_work_time(interaction: discord.Interaction, hours: float):
    config["required_work_time_hours"] = hours
    save_config(config)
    await interaction.response.send_message(f"Требуемое время работы установлено: {hours} часов.", ephemeral=True)

@app_commands.command(name="set_report_check_period", description="Устанавливает период проверки отчетности (часы).")
@app_commands.check(allowed_check)
async def set_report_check_period(interaction: discord.Interaction, hours: float):
    config["report_check_period_hours"] = hours
    save_config(config)
    await interaction.response.send_message(f"Период проверки отчетности установлен: {hours} часов.", ephemeral=True)

@app_commands.command(name="add_applicable_role", description="Добавляет роль в список применимых ролей.")
@app_commands.check(allowed_check)
async def add_applicable_role(interaction: discord.Interaction, role: discord.Role):
    applicable = config.get("applicable_roles", [])
    if role.id not in applicable:
        applicable.append(role.id)
        config["applicable_roles"] = applicable
        save_config(config)
        await interaction.response.send_message(f"Роль {role.name} добавлена в список применимых ролей.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Роль {role.name} уже присутствует.", ephemeral=True)

@app_commands.command(name="remove_applicable_role", description="Удаляет роль из списка применимых ролей.")
@app_commands.check(allowed_check)
async def remove_applicable_role(interaction: discord.Interaction, role: discord.Role):
    applicable = config.get("applicable_roles", [])
    if role.id in applicable:
        applicable.remove(role.id)
        config["applicable_roles"] = applicable
        save_config(config)
        await interaction.response.send_message(f"Роль {role.name} удалена из списка применимых ролей.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Роль {role.name} не найдена.", ephemeral=True)

@app_commands.command(name="applicable_roles_list", description="Выводит список применимых ролей.")
@app_commands.check(allowed_check)
async def applicable_roles_list(interaction: discord.Interaction):
    applicable = config.get("applicable_roles", [])
    if not applicable:
        await interaction.response.send_message("Список применимых ролей пуст (применяются все участники).", ephemeral=True)
        return
    roles_names = []
    for role_id in applicable:
        role = interaction.guild.get_role(role_id)
        if role:
            roles_names.append(role.name)
        else:
            roles_names.append(str(role_id))
    await interaction.response.send_message("Применимые роли: " + ", ".join(roles_names), ephemeral=True)

# Функция для генерации отчета (аналог check_reports)
async def generate_report(report_channel: discord.TextChannel, period: float) -> str:
    now = datetime.utcnow()
    after_time = now - timedelta(hours=period)
    messages = await report_channel.history(after=after_time).flatten()
    work_times = {}
    pattern = r"(?i)\b(?:работал|работала|отработал|отработала)\s+(\d+(?:[.,]\d+)?)\s*(?:час(?:ов|а)?)\b"
    for msg in messages:
        match = re.search(pattern, msg.content)
        if match:
            hours_str = match.group(1).replace(",", ".")
            try:
                hours_val = float(hours_str)
                minutes = hours_val * 60
                work_times[msg.author.id] = work_times.get(msg.author.id, 0) + minutes
                await msg.add_reaction("✅")
            except Exception:
                await msg.add_reaction("❌")
        else:
            await msg.add_reaction("❌")
    required_minutes = config["required_work_time_hours"] * 60
    worked_enough = []
    worked_insufficient = []
    not_worked = []
    for member in report_channel.guild.members:
        if member.bot or not is_applicable(member):
            continue
        total = work_times.get(member.id, 0)
        if total >= required_minutes:
            worked_enough.append(f"{member.mention} ({total:.0f} мин)")
        elif total > 0:
            worked_insufficient.append(f"{member.mention} ({total:.0f} мин)")
        else:
            not_worked.append(member.mention)
    report = (
        f"Отчетность за последние {period} часов\n\n"
        f"1. Работал достаточно (>= {config['required_work_time_hours']} ч):\n"
        + ("\n".join(worked_enough) if worked_enough else "Нет данных")
        + "\n\n"
        + f"2. Работал, но не достаточно (< {config['required_work_time_hours']} ч):\n"
        + ("\n".join(worked_insufficient) if worked_insufficient else "Нет данных")
        + "\n\n"
        + "3. Не работал:\n"
        + ("\n".join(not_worked) if not_worked else "Нет данных")
    )
    return report

@app_commands.command(name="check_reports", description="Проверяет отчетность в указанном канале.")
@app_commands.check(allowed_check)
async def check_reports(interaction: discord.Interaction, report_channel: discord.TextChannel, period: Optional[float] = None):
    if period is None:
        period = config.get("report_check_period_hours", 24)
    report = await generate_report(report_channel, period)
    await interaction.response.send_message(report)

# Автоотчет: фоновая задача и команды для включения/отключения
auto_report_task = None

async def auto_report_task_func():
    while config.get("auto_report_enabled", False):
        period = config.get("report_check_period_hours", 24)
        await asyncio.sleep(period * 3600)
        channel_id = config.get("auto_report_channel")
        if channel_id is None:
            continue
        channel = bot.get_channel(channel_id)
        if channel is None:
            continue
        report = await generate_report(channel, period)
        await channel.send(report)

@app_commands.command(name="enable_auto_report", description="Включает автоотчет в указанном канале.")
@app_commands.check(allowed_check)
async def enable_auto_report(interaction: discord.Interaction, channel: discord.TextChannel):
    config["auto_report_enabled"] = True
    config["auto_report_channel"] = channel.id
    save_config(config)
    global auto_report_task
    if auto_report_task is None or auto_report_task.done():
        auto_report_task = bot.loop.create_task(auto_report_task_func())
    await interaction.response.send_message(
        f"Автоотчет включен. Отчеты будут публиковаться в {channel.mention} каждые {config.get('report_check_period_hours', 24)} часов.",
        ephemeral=True,
    )

@app_commands.command(name="disable_auto_report", description="Отключает автоотчет.")
@app_commands.check(allowed_check)
async def disable_auto_report(interaction: discord.Interaction):
    config["auto_report_enabled"] = False
    save_config(config)
    global auto_report_task
    if auto_report_task is not None:
        auto_report_task.cancel()
        auto_report_task = None
    await interaction.response.send_message("Автоотчет отключен.", ephemeral=True)

@app_commands.command(name="echo", description="Отправляет сообщение от лица бота в указанный текстовый канал.")
@app_commands.check(allowed_check)
async def echo(interaction: discord.Interaction, channel: discord.TextChannel, *, message: str):
    await channel.send(message)
    await interaction.response.send_message("Сообщение отправлено.", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")
    try:
        print("Начинаю синхронизацию команд...")
        print(f"Количество команд до очистки: {len(bot.tree.get_commands())}")
        await bot.tree.sync()
        app_commands_list = await bot.tree.fetch_commands()
        print(f"Успешно синхронизировано {len(app_commands_list)} слеш-команд:")
        for command in app_commands_list:
            print(f"- /{command.name}")
    except Exception as e:
        print(f"Ошибка синхронизации: {str(e)}")
        import traceback
        traceback.print_exc()
    
    if config.get("auto_report_enabled", False):
        global auto_report_task
        if auto_report_task is None or auto_report_task.done():
            auto_report_task = bot.loop.create_task(auto_report_task_func())

bot.run(TOKEN)
