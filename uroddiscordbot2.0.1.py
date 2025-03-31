import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional, Union

import disnake # type: ignore
from disnake.ext import commands # type: ignore
from disnake.ext.commands import MissingPermissions, Context # type: ignore
from dotenv import load_dotenv, find_dotenv # type: ignore
from disnake import HTTPException, NotFound

load_dotenv(find_dotenv())

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Переменная окружения DISCORD_BOT_TOKEN не установлена.")

# Настройка интентов
intents = disnake.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True

# Создаем бота. Для слеш-команд используем InteractionBot.
bot = commands.InteractionBot(intents=intents)

# Файл конфигурации
CONFIG_FILE = "config.json"


def load_config() -> dict:
    default_config = {
        "required_work_time_hours": 8,
        "report_check_period_hours": 24,
        "applicable_roles": [],  # Если список не пуст, функции применяются только к участникам с указанными ролями
        "auto_report_enabled": False,
        "auto_report_channel": None,
        "command_access_users": [],  # Список ID пользователей, которым разрешен доступ
        "command_access_roles": [],  # Список ID ролей, которым разрешен доступ
        "whitelist": [],  # Список ID пользователей, исключаемых из некоторых функций
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


def is_applicable(member: disnake.Member) -> bool:
    """Возвращает True, если список applicable_roles пуст или участник имеет хотя бы одну из указанных ролей."""
    applicable_roles = config.get("applicable_roles", [])
    if not applicable_roles:
        return True
    return any(role.id in applicable_roles for role in member.roles)


async def allowed_check(ctx: Context) -> bool:
    """Проверяет, имеет ли пользователь доступ к командам."""
    if ctx.author.guild_permissions.administrator:
        return True
    allowed_users = config.get("command_access_users", [])
    allowed_roles = config.get("command_access_roles", [])
    if ctx.author.id in allowed_users:
        return True
    return any(role.id in allowed_roles for role in ctx.author.roles)


@bot.event
async def on_slash_command_error(
    inter: disnake.ApplicationCommandInteraction, error: Exception
):
    if isinstance(error, MissingPermissions):
        await inter.response.send_message(
            "Ошибка: недостаточно прав для использования этой команды.", ephemeral=True
        )
    else:
        await inter.response.send_message(f"Ошибка: {error}", ephemeral=True)


# --- СЛЕШ-КОМАНДЫ (Доступ только администраторам/доверенным) ---
# Для каждого слеш-команды добавляем декоратор @commands.check(allowed_check) через @bot.slash_command.


@bot.slash_command(
    name="voice_data", description="Выводит данные о голосовых и Stage каналах (JSON)."
)
@commands.check(allowed_check)
async def voice_data(
    inter: disnake.ApplicationCommandInteraction,
    channel: Optional[Union[disnake.VoiceChannel, disnake.StageChannel]] = None,
):
    await inter.response.defer(ephemeral=True)
    voice_data_dict = {}
    if channel:
        channels = [channel]
    else:
        channels = inter.guild.voice_channels + getattr(
            inter.guild, "stage_channels", []
        )
    for vc in channels:
        members = []
        for member in vc.members:
            if is_applicable(member):
                members.append(
                    {
                        "id": member.id,
                        "name": member.name,
                        "discriminator": member.discriminator,
                        "display_name": member.display_name,
                    }
                )
        voice_data_dict[vc.name] = members
    json_data = json.dumps(voice_data_dict, indent=4, ensure_ascii=False)
    await inter.followup.send(f"```json\n{json_data}\n```", ephemeral=True)


@bot.slash_command(
    name="message_voice_data",
    description="Отправляет данные голосовых/Stage каналов отдельными сообщениями.",
)
@commands.check(allowed_check)
async def message_voice_data(
    inter: disnake.ApplicationCommandInteraction,
    channel: Optional[Union[disnake.VoiceChannel, disnake.StageChannel]] = None,
):
    await inter.response.defer(ephemeral=True)
    if channel:
        channels = [channel]
    else:
        channels = inter.guild.voice_channels + getattr(
            inter.guild, "stage_channels", []
        )
    for vc in channels:
        if vc.members:
            member_list = "\n".join(
                [
                    f"{member.display_name} (ID: {member.id})"
                    for member in vc.members
                    if is_applicable(member)
                ]
            )
            msg = f"**Канал:** {vc.name}\n**Участники:**\n{member_list if member_list else 'Нет подходящих участников'}"
        else:
            msg = f"**Канал:** {vc.name}\n**Участники:** Нет участников"
        await inter.followup.send(msg, ephemeral=True)


@bot.slash_command(
    name="mention_not_in_channel",
    description="Упоминает пользователей, не находящихся в голосовом/Stage канале.",
)
@commands.check(allowed_check)
async def mention_not_in_channel(
    inter: disnake.ApplicationCommandInteraction,
    channel: Optional[Union[disnake.VoiceChannel, disnake.StageChannel]] = None,
):
    await inter.response.defer(ephemeral=True)
    if channel:
        not_in_channel = [
            member.mention
            for member in inter.guild.members
            if (member.voice is None or member.voice.channel != channel)
            and not member.bot
            and member.id not in config.get("whitelist", [])
            and is_applicable(member)
        ]
    else:
        not_in_channel = [
            member.mention
            for member in inter.guild.members
            if member.voice is None
            and not member.bot
            and member.id not in config.get("whitelist", [])
            and is_applicable(member)
        ]
    if not not_in_channel:
        await inter.response.send_message(
            "Все подходящие пользователи находятся в голосовых каналах!", ephemeral=True
        )
        return
    messages = []
    msg_chunk = ""
    for mention in not_in_channel:
        if len(msg_chunk) + len(mention) + 1 > 1900:
            messages.append(msg_chunk)
            msg_chunk = mention + " "
        else:
            msg_chunk += mention + " "
    if msg_chunk:
        messages.append(msg_chunk)
    for msg in messages:
        await inter.followup.send(msg, ephemeral=True)


@bot.slash_command(
    name="whitelist_add", description="Добавляет пользователя в whitelist."
)
@commands.check(allowed_check)
async def whitelist_add_cmd(
    inter: disnake.ApplicationCommandInteraction, member: disnake.Member
):
    whitelist_list = config.get("whitelist", [])
    if member.id not in whitelist_list:
        whitelist_list.append(member.id)
        config["whitelist"] = whitelist_list
        save_config(config)
        await inter.response.send_message(
            f"{member.display_name} добавлен в whitelist.", ephemeral=True
        )
    else:
        await inter.response.send_message(
            f"{member.display_name} уже в whitelist.", ephemeral=True
        )


@bot.slash_command(
    name="whitelist_remove", description="Удаляет пользователя из whitelist."
)
@commands.check(allowed_check)
async def whitelist_remove_cmd(
    inter: disnake.ApplicationCommandInteraction, member: disnake.Member
):
    whitelist_list = config.get("whitelist", [])
    if member.id in whitelist_list:
        whitelist_list.remove(member.id)
        config["whitelist"] = whitelist_list
        save_config(config)
        await inter.response.send_message(
            f"{member.display_name} удалён из whitelist.", ephemeral=True
        )
    else:
        await inter.response.send_message(
            f"{member.display_name} не найден в whitelist.", ephemeral=True
        )


@bot.slash_command(
    name="whitelist_list", description="Выводит список пользователей в whitelist."
)
@commands.check(allowed_check)
async def whitelist_list_cmd(inter: disnake.ApplicationCommandInteraction):
    whitelist_list = config.get("whitelist", [])
    if not whitelist_list:
        await inter.response.send_message("Whitelist пуст.", ephemeral=True)
        return
    members_list = []
    for user_id in whitelist_list:
        member = inter.guild.get_member(user_id)
        if member:
            members_list.append(member.mention)
        else:
            members_list.append(str(user_id))
    await inter.response.send_message(
        "Whitelist: " + ", ".join(members_list), ephemeral=True
    )


@bot.slash_command(
    name="set_required_work_time",
    description="Устанавливает требуемое время работы (часы).",
)
@commands.check(allowed_check)
async def set_required_work_time(
    inter: disnake.ApplicationCommandInteraction, hours: float
):
    config["required_work_time_hours"] = hours
    save_config(config)
    await inter.response.send_message(
        f"Требуемое время работы установлено: {hours} часов.", ephemeral=True
    )


@bot.slash_command(
    name="set_report_check_period",
    description="Устанавливает период проверки отчетности (часы).",
)
@commands.check(allowed_check)
async def set_report_check_period(
    inter: disnake.ApplicationCommandInteraction, hours: float
):
    config["report_check_period_hours"] = hours
    save_config(config)
    await inter.response.send_message(
        f"Период проверки отчетности установлен: {hours} часов.", ephemeral=True
    )


@bot.slash_command(
    name="add_applicable_role", description="Добавляет роль в список применимых ролей."
)
@commands.check(allowed_check)
async def add_applicable_role(
    inter: disnake.ApplicationCommandInteraction, role: disnake.Role
):
    applicable = config.get("applicable_roles", [])
    if role.id not in applicable:
        applicable.append(role.id)
        config["applicable_roles"] = applicable
        save_config(config)
        await inter.response.send_message(
            f"Роль {role.name} добавлена в список применимых ролей.", ephemeral=True
        )
    else:
        await inter.response.send_message(
            f"Роль {role.name} уже присутствует.", ephemeral=True
        )


@bot.slash_command(
    name="remove_applicable_role",
    description="Удаляет роль из списка применимых ролей.",
)
@commands.check(allowed_check)
async def remove_applicable_role(
    inter: disnake.ApplicationCommandInteraction, role: disnake.Role
):
    applicable = config.get("applicable_roles", [])
    if role.id in applicable:
        applicable.remove(role.id)
        config["applicable_roles"] = applicable
        save_config(config)
        await inter.response.send_message(
            f"Роль {role.name} удалена из списка применимых ролей.", ephemeral=True
        )
    else:
        await inter.response.send_message(
            f"Роль {role.name} не найдена.", ephemeral=True
        )


@bot.slash_command(
    name="applicable_roles_list", description="Выводит список применимых ролей."
)
@commands.check(allowed_check)
async def applicable_roles_list(inter: disnake.ApplicationCommandInteraction):
    applicable = config.get("applicable_roles", [])
    if not applicable:
        await inter.response.send_message(
            "Список применимых ролей пуст (применяются все участники).", ephemeral=True
        )
        return
    roles_names = []
    for role_id in applicable:
        role = inter.guild.get_role(role_id)
        if role:
            roles_names.append(role.name)
        else:
            roles_names.append(str(role_id))
    await inter.response.send_message(
        "Применимые роли: " + ", ".join(roles_names), ephemeral=True
    )


async def generate_report(report_channel: disnake.TextChannel, period: float) -> str:
    now = datetime.now()
    after_time = now - timedelta(hours=period)
    messages = await report_channel.history(after=after_time).flatten()
    work_times = {}
    pattern = r"(\d+(?:[.,]\d+)?)"
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


@bot.slash_command(
    name="check_reports", description="Проверяет отчетность в указанном канале."
)
@commands.check(allowed_check)
async def check_reports(
    inter: disnake.ApplicationCommandInteraction,
    report_channel: disnake.TextChannel,
    period: Optional[float] = None,
):
    await inter.response.defer(ephemeral=True)
    if period is None:
        period = config.get("report_check_period_hours", 24)
    report = await generate_report(report_channel, period)
    await inter.response.send_message(report)


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


@bot.slash_command(
    name="enable_auto_report", description="Включает автоотчет в указанном канале."
)
@commands.check(allowed_check)
async def enable_auto_report(
    inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel
):
    config["auto_report_enabled"] = True
    config["auto_report_channel"] = channel.id
    save_config(config)
    global auto_report_task
    if auto_report_task is None or auto_report_task.done():
        auto_report_task = bot.loop.create_task(auto_report_task_func())
    await inter.response.send_message(
        f"Автоотчет включен. Отчеты будут публиковаться в {channel.mention} каждые {config.get('report_check_period_hours', 24)} часов.",
        ephemeral=True,
    )


@bot.slash_command(name="disable_auto_report", description="Отключает автоотчет.")
@commands.check(allowed_check)
async def disable_auto_report(inter: disnake.ApplicationCommandInteraction):
    config["auto_report_enabled"] = False
    save_config(config)
    global auto_report_task
    if auto_report_task is not None:
        auto_report_task.cancel()
        auto_report_task = None
    await inter.response.send_message("Автоотчет отключен.", ephemeral=True)


@bot.slash_command(
    name="echo",
    description="Отправляет сообщение от лица бота в указанный текстовый канал.",
)
@commands.check(allowed_check)
async def echo(
    inter: disnake.ApplicationCommandInteraction,
    channel: disnake.TextChannel,
    *,
    message: str,
):
    await channel.send(message)
    await inter.response.send_message("Сообщение отправлено.", ephemeral=True)

@bot.slash_command(name="grant_access_user", description="Выдает доступ к командам указанному пользователю.")
@commands.check(allowed_check)
async def grant_access_user(inter: disnake.ApplicationCommandInteraction, member: disnake.Member):
    allowed_users = config.get("command_access_users", [])
    if member.id not in allowed_users:
        allowed_users.append(member.id)
        config["command_access_users"] = allowed_users
        save_config(config)
        await inter.response.send_message(f"{member.mention} теперь имеет доступ к командам.", ephemeral=True)
    else:
        await inter.response.send_message(f"{member.mention} уже имеет доступ.", ephemeral=True)


@bot.slash_command(name="revoke_access_user", description="Отзывает доступ к командам у указанного пользователя.")
@commands.check(allowed_check)
async def revoke_access_user(inter: disnake.ApplicationCommandInteraction, member: disnake.Member):
    allowed_users = config.get("command_access_users", [])
    if member.id in allowed_users:
        allowed_users.remove(member.id)
        config["command_access_users"] = allowed_users
        save_config(config)
        await inter.response.send_message(f"Доступ для {member.mention} отозван.", ephemeral=True)
    else:
        await inter.response.send_message(f"{member.mention} не имеет доступа.", ephemeral=True)


@bot.slash_command(name="list_access_users", description="Выводит список пользователей, имеющих доступ к командам.")
@commands.check(allowed_check)
async def list_access_users(inter: disnake.ApplicationCommandInteraction):
    allowed_users = config.get("command_access_users", [])
    if not allowed_users:
        await inter.response.send_message("Список доверенных пользователей пуст.", ephemeral=True)
        return
    users = []
    for user_id in allowed_users:
        member = inter.guild.get_member(user_id)
        if member:
            users.append(member.mention)
        else:
            users.append(str(user_id))
    await inter.response.send_message("Доверенные пользователи: " + ", ".join(users), ephemeral=True)


@bot.slash_command(name="grant_access_role", description="Выдает доступ к командам указанной роли.")
@commands.check(allowed_check)
async def grant_access_role(inter: disnake.ApplicationCommandInteraction, role: disnake.Role):
    allowed_roles = config.get("command_access_roles", [])
    if role.id not in allowed_roles:
        allowed_roles.append(role.id)
        config["command_access_roles"] = allowed_roles
        save_config(config)
        await inter.response.send_message(f"Роль {role.name} теперь имеет доступ к командам.", ephemeral=True)
    else:
        await inter.response.send_message(f"Роль {role.name} уже имеет доступ.", ephemeral=True)


@bot.slash_command(name="revoke_access_role", description="Отзывает доступ к командам у указанной роли.")
@commands.check(allowed_check)
async def revoke_access_role(inter: disnake.ApplicationCommandInteraction, role: disnake.Role):
    allowed_roles = config.get("command_access_roles", [])
    if role.id in allowed_roles:
        allowed_roles.remove(role.id)
        config["command_access_roles"] = allowed_roles
        save_config(config)
        await inter.response.send_message(f"Доступ для роли {role.name} отозван.", ephemeral=True)
    else:
        await inter.response.send_message(f"Роль {role.name} не имеет доступа.", ephemeral=True)


@bot.slash_command(name="list_access_roles", description="Выводит список ролей, имеющих доступ к командам.")
@commands.check(allowed_check)
async def list_access_roles(inter: disnake.ApplicationCommandInteraction):
    allowed_roles = config.get("command_access_roles", [])
    if not allowed_roles:
        await inter.response.send_message("Список доверенных ролей пуст.", ephemeral=True)
        return
    roles = []
    for role_id in allowed_roles:
        role = inter.guild.get_role(role_id)
        if role:
            roles.append(role.name)
        else:
            roles.append(str(role_id))
    await inter.response.send_message("Доверенные роли: " + ", ".join(roles), ephemeral=True)

@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: commands.CommandError):
    try:
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)
        await inter.followup.send(f"Ошибка: {error}", ephemeral=True)
    except NotFound:  # Игнорируем недействительные взаимодействия
        pass
    except HTTPException as e:
        print(f"Не удалось отправить сообщение об ошибке: {e}")


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")
    if config.get("auto_report_enabled", False):
        global auto_report_task
        if auto_report_task is None or auto_report_task.done():
            auto_report_task = bot.loop.create_task(auto_report_task_func())


bot.run(TOKEN)
