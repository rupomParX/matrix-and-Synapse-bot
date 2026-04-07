"""
commands/utils.py — Utility commands for RoseBot
!ping  !weather <city>  !translate <lang> <text>  !urban <term>
!yts <query>  !whoami  !id @user  !stats  !rank  !help
"""

import os
import time
import re
from urllib.parse import quote_plus

import aiohttp
import db

HOMESERVER = os.getenv("MATRIX_HOMESERVER", "")
HOMESERVER_DOMAIN = HOMESERVER.split("://")[-1] if HOMESERVER else "matrix.org"


def normalize_mxid(mention: str) -> str:
    """Convert @user or @user.name to @user:domain format."""
    if not mention.startswith("@"):
        return mention
    if ":" in mention:
        return mention  # already full MXID
    # Add server domain
    return f"{mention}:{HOMESERVER_DOMAIN}"


WTTR_URL     = "https://wttr.in/{city}?format=j1"
LIBRETRANS   = "https://libretranslate.de/translate"
URBAN_URL    = "https://api.urbandictionary.com/v0/define?term={term}"
YT_SEARCH    = "https://www.youtube.com/results?search_query={q}"
INNERTUBE    = "https://www.youtube.com/youtubei/v1/search"


# ─── PING ─────────────────────────────────────────────────────────────────────

async def cmd_ping(send_ts_ms: float) -> str:
    latency = int((time.time() * 1000) - send_ts_ms)
    bar = "▓" * min(20, max(1, latency // 25)) + "░" * max(0, 20 - latency // 25)
    quality = "🟢 Excellent" if latency < 200 else "🟡 Good" if latency < 500 else "🔴 Poor"
    return (
        f"🏓 Pong!\n"
        f"━━━━━━━━━━━━━━\n"
        f"⚡ Latency: {latency} ms\n"
        f"[{bar}]\n"
        f"📶 Quality: {quality}"
    )


# ─── WEATHER ──────────────────────────────────────────────────────────────────

async def cmd_weather(args: str) -> str:
    city = args.strip()
    if not city:
        return "Usage: !weather <city name>"
    url = WTTR_URL.format(city=city.replace(" ", "+"))
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return f"❌ Could not find weather for \"{city}\"."
            data = await resp.json(content_type=None)

    try:
        cur   = data["current_condition"][0]
        area  = data["nearest_area"][0]
        city_name   = area["areaName"][0]["value"]
        country      = area["country"][0]["value"]
        temp_c       = cur["temp_C"]
        temp_f       = cur["temp_F"]
        feels_c      = cur["FeelsLikeC"]
        desc         = cur["weatherDesc"][0]["value"]
        humidity     = cur["humidity"]
        wind_kmph    = cur["windspeedKmph"]
        wind_dir     = cur["winddir16Point"]
        uv           = cur.get("uvIndex", "N/A")
        visibility   = cur.get("visibility", "N/A")

        today = data["weather"][0]
        max_c  = today["maxtempC"]
        min_c  = today["mintempC"]
        sunrise = today["astronomy"][0]["sunrise"]
        sunset  = today["astronomy"][0]["sunset"]

        return (
            f"🌤 Weather — {city_name}, {country}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🌡 Temp:     {temp_c}°C / {temp_f}°F (feels {feels_c}°C)\n"
            f"📊 High/Low: {max_c}°C / {min_c}°C\n"
            f"☁️ Condition: {desc}\n"
            f"💧 Humidity: {humidity}%\n"
            f"💨 Wind:     {wind_kmph} km/h {wind_dir}\n"
            f"👁 Visibility: {visibility} km\n"
            f"☀️ UV Index: {uv}\n"
            f"🌅 Sunrise: {sunrise}  🌇 Sunset: {sunset}"
        )
    except (KeyError, IndexError):
        return f"❌ Could not parse weather data for \"{city}\"."


# ─── TRANSLATE ────────────────────────────────────────────────────────────────

async def cmd_translate(args: str) -> str:
    """!translate <lang_code> <text>  e.g. !translate ja Hello world"""
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        return (
            "Usage: !translate <lang> <text>\n"
            "Example: !translate ja Hello world\n"
            "Codes: en, ja, ko, zh, fr, de, es, ar, bn, hi, ru, pt, it, nl"
        )
    target_lang, text = parts[0].lower(), parts[1]

    # Use MyMemory (free, no key needed)
    url = f"https://api.mymemory.translated.net/get?q={quote_plus(text)}&langpair=en|{target_lang}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return "❌ Translation service unavailable."
            data = await resp.json()

    translated = data.get("responseData", {}).get("translatedText", "")
    if not translated or translated.lower() == text.lower():
        return f"❌ Could not translate to \"{target_lang}\". Check the language code."

    return (
        f"🌐 Translation → {target_lang.upper()}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📝 Original:   {text}\n"
        f"✅ Translated: {translated}"
    )


# ─── URBAN DICTIONARY ─────────────────────────────────────────────────────────

def _clean_ud(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]", r"\1", text)


async def cmd_urban(args: str) -> str:
    term = args.strip()
    if not term:
        return "Usage: !urban <term>"
    url = f"https://api.urbandictionary.com/v0/define?term={quote_plus(term)}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return "❌ Urban Dictionary unavailable."
            data = await resp.json()

    entries = data.get("list", [])
    if not entries:
        return f"❌ No definition found for \"{term}\"."

    e       = entries[0]
    word    = e.get("word", term)
    defn    = _clean_ud(e.get("definition", "")).strip()[:400]
    example = _clean_ud(e.get("example", "")).strip()[:200]
    thumbs_up   = e.get("thumbs_up", 0)
    thumbs_down = e.get("thumbs_down", 0)
    link    = e.get("permalink", "")

    return (
        f"📖 Urban Dictionary — {word}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{defn}\n"
        + (f"\n💬 Example:\n{example}\n" if example else "")
        + f"\n👍 {thumbs_up:,}  👎 {thumbs_down:,}\n"
        f"🔗 {link}"
    )


# ─── YOUTUBE SEARCH ───────────────────────────────────────────────────────────

async def cmd_yts(args: str) -> str:
    query = args.strip()
    if not query:
        return "Usage: !yts <search query>"

    search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return "❌ YouTube search failed."
            html = await resp.text()

    # Extract video IDs from the initial data
    ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
    # Extract titles near those IDs
    titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"', html)

    if not ids:
        return f"❌ No results found for \"{query}\"."

    results = []
    seen = set()
    for vid_id, title in zip(ids, titles):
        if vid_id in seen:
            continue
        seen.add(vid_id)
        results.append((vid_id, title))
        if len(results) >= 5:
            break

    lines = [f"📺 YouTube — \"{query}\"\n━━━━━━━━━━━━━━"]
    for i, (vid_id, title) in enumerate(results, 1):
        lines.append(f"{i}. {title}\n   🔗 https://youtu.be/{vid_id}")

    return "\n".join(lines)


# ─── WHOAMI / ID ──────────────────────────────────────────────────────────────

def cmd_whoami(mxid: str, display_name: str, room_id: str) -> str:
    user = db.get_user(mxid)
    msg_count = user["message_count"] if user else 0
    first_seen = user["first_seen"] if user else 0
    from datetime import datetime
    fs = datetime.utcfromtimestamp(first_seen).strftime("%Y-%m-%d") if first_seen else "Unknown"
    cmds = db.get_user_commands(mxid, 3)
    top_cmds = ", ".join(f"!{r['command']} ({r['count']})" for r in cmds) or "None yet"

    return (
        f"👤 Who Am I?\n"
        f"━━━━━━━━━━━━━━\n"
        f"🪪 MXID:         {mxid}\n"
        f"📛 Display name: {display_name or 'N/A'}\n"
        f"🏠 Room:         {room_id}\n"
        f"💬 Messages:     {msg_count:,}\n"
        f"📅 First seen:   {fs}\n"
        f"🎯 Top commands: {top_cmds}"
    )


def cmd_id(args: str) -> str:
    """!id @user:server — look up a user's tracked info"""
    mxid = args.strip()
    if not mxid.startswith("@"):
        return "Usage: !id <@user:server>"
    mxid = normalize_mxid(mxid)
    user = db.get_user(mxid)
    if not user:
        return f"❓ No data tracked for {mxid} yet."
    from datetime import datetime
    fs = datetime.utcfromtimestamp(user["first_seen"]).strftime("%Y-%m-%d %H:%M UTC")
    ls = datetime.utcfromtimestamp(user["last_seen"]).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🔍 User Info — {mxid}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📛 Display name: {user['display_name'] or 'Unknown'}\n"
        f"💬 Total messages: {user['message_count']:,}\n"
        f"📅 First seen: {fs}\n"
        f"🕐 Last seen:  {ls}"
    )


# ─── STATS ────────────────────────────────────────────────────────────────────

def cmd_stats() -> str:
    top_cmds  = db.get_top_commands(5)
    top_users = db.get_global_top(5)

    cmd_lines  = [f"  {i+1}. !{r['command']} ({r['total']}x)" for i, r in enumerate(top_cmds)]
    user_lines = [
        f"  {i+1}. {r['display_name'] or r['mxid']} ({r['message_count']:,} msgs)"
        for i, r in enumerate(top_users)
    ]

    return (
        f"📊 Bot Stats\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔥 Top Commands:\n" + ("\n".join(cmd_lines) or "  None yet") + "\n"
        f"━━━━━━━━━━━━━━\n"
        f"👥 Top Users (global):\n" + ("\n".join(user_lines) or "  None yet")
    )


# ─── RANKING ──────────────────────────────────────────────────────────────────

def cmd_rank(room_id: str) -> str:
    rows = db.get_top_users(room_id, 10)
    if not rows:
        return "📊 No messages tracked in this room yet."
    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name  = row["display_name"] or row["mxid"]
        lines.append(f"{medal} {name} — {row['message_count']:,} messages")
    return f"🏆 Room Ranking\n━━━━━━━━━━━━━━\n" + "\n".join(lines)


# ─── HELP ─────────────────────────────────────────────────────────────────────

HELP_TEXT = """🤖 RoseBot — Command List
━━━━━━━━━━━━━━
📺 AniList
  !anime <title|id>      — Anime info
  !manga <title|id>      — Manga info
  !character <name|id>   — Character info
  !airing <title|id>     — Airing schedule
  !top [genre]           — Top rated anime
  !studio <name>         — Studio info

💰 Economy & Games
  !bank                  — Your bank balance
  !bank add <@user> <amt> — (Admin) Add coins to another user
  !daily                 — Claim daily coins
  !loan <amt>            — Take a loan (max 10k)
  !crash <bet> <mult>    — Crash gambling game
  !give <@user> <amt>    — Transfer coins
  !richlist              — Top balances
  !gamestats             — Your game stats

📥 Media Downloads
  !ytdl <url>            — Download YouTube video
  !mp3 <url>             — YouTube → MP3
  !igdl <url>            — Instagram video/image
  !fbdl <url>            — Facebook video
  !xdl <url>             — X/Twitter video
  !pixiv <url|id>        — Pixiv artwork

🔧 Utilities
  !ping                  — Latency check
  !weather <city>        — Weather info
  !translate <lang> <text> — Translate text
  !urban <term>          — Urban Dictionary
  !yts <query>           — YouTube search

👤 User Info
  !whoami                — Your profile & stats
  !id <@user:server>     — Look up a user
  !stats                 — Bot-wide stats
  !rank                  — Room message ranking

🛡 Admin (admins only)
  !kick <@user> [reason] — Kick a user
  !ban <@user> [reason]  — Ban a user
  !unban <@user>         — Unban a user
  !banlist               — List room bans"""
