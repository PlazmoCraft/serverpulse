import asyncio
import base64
import io
import json
import logging
import os
import re
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import tasks

import config
import mcping
from motd_image import flatten_chat, render_motd

logging.basicConfig(level=logging.INFO)

API_URL = "https://api.mcsrvstat.us/3/{host}"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "status_message.json")


def clean_text(text: str) -> str:
    return re.sub(r"\u00a7[0-9a-fk-orx]|\u00a7#[0-9a-fA-F]{6}", "", text).replace("\u00a7", "").strip()


def extract_version(motd_raw: str, fallback: str = "неизвестно") -> str:
    cleaned = clean_text(motd_raw)
    match = re.search(r"\d+\.\d+(?:\.\d+)?", cleaned)
    return match.group(0) if match else clean_text(fallback)


async def fetch_status() -> dict:
    last_error = None
    try:
        raw = await mcping.fetch_status(config.SERVER_ADDRESS, 25565)
        description = raw.get("description")
        motd_raw = flatten_chat(description) if isinstance(description, dict) else str(description or "")
        icon = None
        favicon = raw.get("favicon")
        if favicon and "," in favicon:
            try:
                icon = base64.b64decode(favicon.split(",", 1)[1])
            except Exception:
                icon = None
        version = raw.get("version", {})
        if isinstance(version, dict):
            version_name = version.get("name", "неизвестно")
        else:
            version_name = str(version)
        players = raw.get("players", {})
        return {
            "online": True,
            "motd_raw": motd_raw,
            "version": version_name,
            "online_players": players.get("online", 0),
            "max_players": players.get("max", 0),
            "icon": icon,
            "hostname": config.SERVER_ADDRESS,
            "port": 25565,
            "software": None,
            "mods": None,
            "plugins": None,
        }
    except Exception as exc:
        last_error = exc
        logging.warning("Прямой пинг не удался (%s), пробую API", exc)

    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL.format(host=config.SERVER_ADDRESS), timeout=15) as resp:
            resp.raise_for_status()
            data = await resp.json()

    if not data.get("online"):
        return {
            "online": False,
            "motd_raw": "",
            "version": "неизвестно",
            "online_players": 0,
            "max_players": 0,
            "icon": None,
            "hostname": config.SERVER_ADDRESS,
            "port": 25565,
            "software": None,
            "mods": None,
            "plugins": None,
        }

    motd_raw = "\n".join(data.get("motd", {}).get("raw", []))
    icon = None
    icon_data = data.get("icon")
    if icon_data and "," in icon_data:
        try:
            icon = base64.b64decode(icon_data.split(",", 1)[1])
        except Exception:
            icon = None
    version = data.get("version")
    if isinstance(version, dict):
        version_name = version.get("name", "неизвестно")
    else:
        version_name = str(version or "неизвестно")
    players = data.get("players", {})
    return {
        "online": True,
        "motd_raw": motd_raw,
        "version": version_name,
        "online_players": players.get("online", 0),
        "max_players": players.get("max", 0),
        "icon": icon,
        "hostname": data.get("hostname") or config.SERVER_ADDRESS,
        "port": data.get("port", 25565),
        "software": data.get("software"),
        "mods": data.get("mods"),
        "plugins": data.get("plugins"),
    }


def build_embed(status: dict):
    online = status["online"]
    color = discord.Color.green() if online else discord.Color.red()

    embed = discord.Embed(
        title="\U0001f3ae Статус сервера Minecraft",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=config.SERVER_ADDRESS)

    if not online:
        embed.description = ":red_circle: **Сервер недоступен (offline)**"
        return embed, None

    version = extract_version(status["motd_raw"], status["version"])
    embed.add_field(name=":satellite: Версия", value=version, inline=True)
    embed.add_field(name=":busts_in_silhouette: Игроки", value=f"**{status['online_players']} / {status['max_players']}**", inline=True)
    embed.add_field(name=":link: Адрес", value=f"`{status['hostname']}:{status['port']}`", inline=True)

    file = None
    if status["motd_raw"]:
        try:
            img = render_motd(status["motd_raw"], icon_bytes=status["icon"])
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            file = discord.File(buffer, filename="motd.png")
            embed.set_image(url="attachment://motd.png")
        except Exception as exc:
            logging.error("Не удалось отрисовать MOTD: %s", exc)
            embed.description = "\n".join(clean_text(l) for l in status["motd_raw"].split("\n") if clean_text(l))

    return embed, file


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(message_id, channel_id):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"message_id": message_id, "channel_id": channel_id}, f, ensure_ascii=False)
    except OSError as exc:
        logging.error("Не удалось сохранить состояние: %s", exc)


class StatusBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.channel = None

    async def setup_hook(self):
        self.check_loop.start()

    async def on_ready(self):
        logging.info("Бот запущен как %s", self.user)
        guild = self.get_guild(config.GUILD_ID)
        if guild:
            self.channel = guild.get_channel(config.CHANNEL_ID)
        if not self.channel:
            self.channel = self.get_channel(config.CHANNEL_ID)
        if not self.channel:
            logging.error("Канал %s не найден", config.CHANNEL_ID)
            return
        await self.post_status()

    @tasks.loop(minutes=config.UPDATE_INTERVAL_MINUTES)
    async def check_loop(self):
        if self.channel:
            await self.post_status()

    async def post_status(self):
        try:
            status = await fetch_status()
        except Exception as exc:
            logging.error("Не удалось получить статус: %s", exc)
            embed = discord.Embed(
                title="\u26a0\ufe0f Ошибка",
                description=f"Не удалось получить статус сервера: `{exc}`",
                color=discord.Color.dark_red(),
            )
            file = None
        else:
            embed, file = build_embed(status)

        state = load_state()
        msg_id = state.get("message_id")
        state_channel = state.get("channel_id")
        edited = False
        if msg_id and state_channel == self.channel.id:
            try:
                msg = self.channel.get_partial_message(msg_id)
                await msg.edit(embed=embed, attachments=[file] if file else [])
                edited = True
            except discord.NotFound:
                edited = False
            except Exception as exc:
                logging.warning("Не удалось обновить сообщение %s: %s", msg_id, exc)
                edited = False

        if not edited:
            msg = await self.channel.send(embed=embed, file=file)
            save_state(msg.id, self.channel.id)


if __name__ == "__main__":
    bot = StatusBot()
    bot.run(config.TOKEN)