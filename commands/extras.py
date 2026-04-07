"""
commands/extras.py — Extra commands for RoseBot
!mal <title>     — MyAnimeList search via Jikan API
!imagine <prompt> — AI image generation via Pollinations.ai
!remind <time> <message> — Set a reminder
!reminders       — List your pending reminders
!cancelremind <id> — Cancel a reminder
!poll <question> | <opt1> | <opt2> ...  — Create a poll
!vote <poll_id> <option_number>         — Vote in a poll
!pollresults [poll_id]                  — Show poll results
!endpoll [poll_id]                      — Close a poll (creator/admin)
"""

import re
import time
import urllib.parse
import aiohttp
import db

JIKAN_API = "https://api.jikan.moe/v4"
POLLINATIONS = "https://image.pollinations.ai/prompt/{prompt}?width=768&height=768&nologo=true&seed={seed}"


# ─── MAL search via Jikan ─────────────────────────────────────────────────────

async def cmd_mal(args: str) -> tuple[str, str | None]:
    """Returns (text, image_url|None)"""
    if not args.strip():
        return "Usage: !mal <anime/manga title>", None

    query = args.strip()
    url   = f"{JIKAN_API}/anime?q={urllib.parse.quote(query)}&limit=1&sfw=true"

    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
            if r.status != 200:
                return f"❌ MyAnimeList search failed (HTTP {r.status}).", None
            data = await r.json()

    results = data.get("data", [])
    if not results:
        return f'❌ No MAL results for "{query}".', None

    m       = results[0]
    title   = m.get("title", "Unknown")
    title_en = m.get("title_english") or ""
    title_jp = m.get("title_japanese") or ""
    mal_id  = m.get("mal_id", "")
    url_mal = m.get("url", "")
    score   = m.get("score") or "N/A"
    scored_by = f'{m.get("scored_by", 0):,}'
    rank    = m.get("rank") or "N/A"
    popularity = m.get("popularity") or "N/A"
    members = f'{m.get("members", 0):,}'
    status  = (m.get("status") or "N/A")
    episodes = m.get("episodes") or "?"
    duration = m.get("duration") or "N/A"
    aired   = (m.get("aired") or {}).get("string") or "N/A"
    rating  = m.get("rating") or "N/A"
    source  = m.get("source") or "N/A"
    fmt     = m.get("type") or "N/A"
    genres  = ", ".join(g["name"] for g in (m.get("genres") or [])[:6]) or "N/A"
    themes  = ", ".join(t["name"] for t in (m.get("themes") or [])[:4]) or ""
    studios = ", ".join(s["name"] for s in (m.get("studios") or [])[:3]) or "N/A"
    synopsis_raw = m.get("synopsis") or ""
    synopsis = synopsis_raw[:300] + "…" if len(synopsis_raw) > 300 else synopsis_raw
    synopsis = synopsis.replace("[Written by MAL Rewrite]", "").strip()

    cover = (m.get("images") or {}).get("jpg", {}).get("large_image_url") or \
            (m.get("images") or {}).get("jpg", {}).get("image_url")

    text = (
        f"📋 MAL — {title}\n"
        + (f"📛 {title_en}" + (f" / {title_jp}" if title_jp else "") + "\n" if title_en else
           (f"📛 {title_jp}\n" if title_jp else ""))
        + f"━━━━━━━━━━━━━━\n"
        f"📺 Type: {fmt}   📊 Status: {status}\n"
        f"🎞 Episodes: {episodes}   ⏱ Duration: {duration}\n"
        f"📅 Aired: {aired}\n"
        f"⭐ Score: {score} ({scored_by} votes)\n"
        f"🏆 Rank: #{rank}   📈 Popularity: #{popularity}\n"
        f"👥 Members: {members}\n"
        f"🔞 Rating: {rating}   📖 Source: {source}\n"
        f"🏢 Studios: {studios}\n"
        f"🏷 Genres: {genres}\n"
        + (f"🎭 Themes: {themes}\n" if themes else "")
        + f"━━━━━━━━━━━━━━\n"
        f"{synopsis}\n"
        f"🔗 {url_mal}"
    )
    return text, cover


# ─── Image generation via Pollinations.ai ─────────────────────────────────────

import random as _random


async def cmd_imagine(args: str) -> tuple[bytes | None, str, str]:
    """Returns (image_bytes, mimetype, error_msg)"""
    prompt = args.strip()
    if not prompt:
        return None, "", "Usage: !imagine <your prompt>\nExample: !imagine a samurai cat at sunset, anime style"

    seed = _random.randint(1, 999999)
    url  = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?width=768&height=768&nologo=true&seed={seed}"

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status != 200:
                    return None, "", f"❌ Image generation failed (HTTP {r.status})."
                data = await r.read()
                mime = r.content_type or "image/jpeg"
                return data, mime, ""
    except asyncio.TimeoutError:
        return None, "", "❌ Image generation timed out (60s). Try a simpler prompt."
    except Exception as e:
        return None, "", f"❌ Image generation error: {e}"


# ─── Reminders ────────────────────────────────────────────────────────────────

_TIME_RE = re.compile(
    r"(?:(\d+)\s*d(?:ays?)?)?\s*"
    r"(?:(\d+)\s*h(?:ours?)?)?\s*"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*"
    r"(?:(\d+)\s*s(?:ec(?:onds?)?)?)?",
    re.IGNORECASE
)


def _parse_duration(s: str) -> int | None:
    """Parse '1h30m', '2d', '45m', '90s' etc → seconds. Returns None if unparseable."""
    m = _TIME_RE.match(s.strip())
    if not m or not any(m.groups()):
        return None
    d = int(m.group(1) or 0)
    h = int(m.group(2) or 0)
    mi = int(m.group(3) or 0)
    sc = int(m.group(4) or 0)
    total = d * 86400 + h * 3600 + mi * 60 + sc
    return total if total > 0 else None


def _fmt_duration(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    parts  = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts) or "0s"


def cmd_remind(mxid: str, room_id: str, args: str) -> str:
    """
    !remind 1h30m Take your meds
    !remind 2d Check the oven
    """
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        return (
            "Usage: !remind <time> <message>\n"
            "Examples:\n"
            "  !remind 30m Check the oven\n"
            "  !remind 1h30m Take your meds\n"
            "  !remind 2d Weekly review\n"
            "Supports: d(ays) h(ours) m(in) s(ec)"
        )
    duration_str, message = parts[0], parts[1].strip()
    if not message:
        return "❌ Reminder message cannot be empty."

    secs = _parse_duration(duration_str)
    if secs is None:
        return f'❌ Could not parse time "{duration_str}". Try: 30m, 1h, 2d, 1h30m'
    if secs < 10:
        return "❌ Minimum reminder time is 10 seconds."
    if secs > 30 * 86400:
        return "❌ Maximum reminder time is 30 days."

    fire_at = int(time.time()) + secs
    rid = db.add_reminder(mxid, room_id, message, fire_at)
    return (
        f"⏰ Reminder set! (ID: {rid})\n"
        f"⏱ In: {_fmt_duration(secs)}\n"
        f"📝 Message: {message}"
    )


def cmd_reminders(mxid: str) -> str:
    rows = db.get_user_reminders(mxid)
    if not rows:
        return "📭 You have no pending reminders."
    now = int(time.time())
    lines = []
    for r in rows:
        remaining = r["fire_at"] - now
        lines.append(f"[{r['id']}] In {_fmt_duration(remaining)}: {r['message']}")
    return f"⏰ Your Reminders ({len(rows)})\n━━━━━━━━━━━━━━\n" + "\n".join(lines)


def cmd_cancelremind(mxid: str, args: str) -> str:
    rid_str = args.strip()
    if not rid_str.isdigit():
        return "Usage: !cancelremind <reminder_id>  (get IDs from !reminders)"
    ok = db.cancel_reminder(int(rid_str), mxid)
    return f"✅ Reminder {rid_str} cancelled." if ok else f"❌ No reminder #{rid_str} found for you."


# ─── Polls ────────────────────────────────────────────────────────────────────

def cmd_poll(room_id: str, creator: str, args: str) -> str:
    """
    !poll Best anime? | Re:Zero | Steins;Gate | HxH
    """
    if not args.strip():
        return (
            "Usage: !poll <question> | <option1> | <option2> | ...\n"
            "Example: !poll Best anime? | Re:Zero | Steins;Gate | HxH\n"
            "Min 2 options, max 8 options."
        )

    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 3:
        return "❌ Need at least 2 options. Separate with | like: !poll Question? | A | B"
    if len(parts) > 9:
        return "❌ Maximum 8 options."

    question = parts[0]
    options  = parts[1:]

    # close any existing open poll in room
    existing = db.get_active_poll(room_id)
    if existing:
        db.close_poll(existing["id"])

    pid = db.create_poll(room_id, creator, question, options)
    opts_display = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))
    return (
        f"📊 Poll #{pid} created!\n"
        f"━━━━━━━━━━━━━━\n"
        f"❓ {question}\n"
        f"{opts_display}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Vote with: !vote {pid} <number>"
    )


def cmd_vote(room_id: str, mxid: str, args: str) -> str:
    parts = args.strip().split()
    # If only one arg, assume it's the option and use the active poll
    if len(parts) == 1 and parts[0].isdigit():
        poll = db.get_active_poll(room_id)
        if not poll:
            return "❌ No active poll in this room. Use: !vote <poll_id> <option_number>"
        poll_id    = poll["id"]
        option_num = int(parts[0])
    elif len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        poll_id    = int(parts[0])
        option_num = int(parts[1])
    else:
        return "Usage: !vote <option_number>  OR  !vote <poll_id> <option_number>"

    ok, err = db.vote_poll(poll_id, mxid, option_num - 1)
    if not ok:
        return f"❌ {err}"
    results = db.get_poll_results(poll_id)
    return (
        f"✅ Vote recorded for poll #{poll_id}!\n"
        f"❓ {results['question']}\n"
        f"Total votes so far: {results['total']}"
    )


def _render_results(pid: int) -> str:
    r = db.get_poll_results(pid)
    if not r:
        return f"❌ Poll #{pid} not found."
    total  = r["total"] or 1  # avoid div/0
    status = "🔒 Closed" if r["closed"] else "🟢 Open"
    lines  = [f"📊 Poll #{pid} — {status}", f"━━━━━━━━━━━━━━", f"❓ {r['question']}", ""]
    for i, opt in enumerate(r["options"]):
        votes  = r["counts"].get(i, 0)
        pct    = int(votes / total * 100)
        bar    = "█" * (pct // 5) + "░" * (20 - pct // 5)
        lines.append(f"{i+1}. {opt}")
        lines.append(f"   [{bar}] {votes} votes ({pct}%)")
    lines.append(f"\n👥 Total votes: {r['total']}")
    return "\n".join(lines)


def cmd_pollresults(room_id: str, args: str) -> str:
    if args.strip().isdigit():
        return _render_results(int(args.strip()))
    poll = db.get_active_poll(room_id)
    if not poll:
        return "❌ No active poll. Use: !pollresults <poll_id>"
    return _render_results(poll["id"])


def cmd_endpoll(room_id: str, sender: str, args: str, sender_power: int = 0) -> str:
    from commands.admin import is_admin
    if args.strip().isdigit():
        pid = int(args.strip())
    else:
        poll = db.get_active_poll(room_id)
        if not poll:
            return "❌ No active poll in this room."
        pid = poll["id"]

    poll = db.get_poll(pid)
    if not poll:
        return f"❌ Poll #{pid} not found."
    if poll["closed"]:
        return f"❌ Poll #{pid} is already closed."
    if poll["creator"] != sender and not is_admin(sender, sender_power):
        return "❌ Only the poll creator or an admin can close it."

    db.close_poll(pid)
    return f"🔒 Poll #{pid} closed!\n\n" + _render_results(pid)


# ─── !config command ──────────────────────────────────────────────────────────

_CONFIGURABLE = {
    "command_prefix":   ("Command prefix character", "1 char, e.g. ! or ~"),
    "daily_reward":     ("Daily coin reward", "number, e.g. 500"),
    "bank_starting":    ("Starting balance for new users", "number, e.g. 1000"),
    "crash_max_bet":    ("Max bet in crash game", "number, e.g. 10000"),
    "command_rate_limit": ("Max commands per user during rate window", "number, e.g. 10"),
    "command_rate_window": ("Rate window duration in seconds", "number, e.g. 60"),
    "mention_required": ("Bot only responds when mentioned", "true or false"),
    "welcome_message":  ("Message sent when bot joins a room", "text or empty"),
    "banned_words":     ("Comma-separated banned words (bot ignores msgs with these)", "word1,word2"),
    "cleanup_temp_messages": ("Auto-redact temporary status messages after 30 seconds", "true or false"),
    "max_download_mb":  ("Max file size for media downloads (MB)", "number, e.g. 50"),
}


def cmd_config(sender: str, sender_power: int, args: str) -> str:
    from commands.admin import is_admin
    if not is_admin(sender, sender_power):
        return "❌ Only admins can change bot config."

    args = args.strip()

    # !config — list all
    if not args:
        current = db.config_list()
        lines   = ["⚙️ Bot Configuration\n━━━━━━━━━━━━━━"]
        for key, (desc, hint) in _CONFIGURABLE.items():
            val = current.get(key, "")
            lines.append(f"• {key} = {val!r}\n  {desc}")
        lines.append("\nSet with: !config <key> <value>")
        return "\n".join(lines)

    parts = args.split(None, 1)
    key   = parts[0].lower()

    # !config <key> — show current value
    if len(parts) == 1:
        if key not in _CONFIGURABLE:
            valid = ", ".join(_CONFIGURABLE.keys())
            return f"❌ Unknown config key: {key}\nValid keys: {valid}"
        val  = db.config_get(key)
        desc, hint = _CONFIGURABLE[key]
        return f"⚙️ {key} = {val!r}\n{desc}\nFormat: {hint}"

    # !config <key> <value> — set value
    value = parts[1].strip()
    if key not in _CONFIGURABLE:
        valid = ", ".join(_CONFIGURABLE.keys())
        return f"❌ Unknown config key: {key}\nValid keys: {valid}"

    # Validate
    if key in ("daily_reward", "bank_starting", "crash_max_bet", "max_download_mb", "command_rate_limit", "command_rate_window"):
        if not value.isdigit() or int(value) < 0:
            return f"❌ {key} must be a positive number."
    elif key == "mention_required":
        if value.lower() not in ("true", "false"):
            return f"❌ mention_required must be 'true' or 'false'."
        value = value.lower()
    elif key == "cleanup_temp_messages":
        if value.lower() not in ("true", "false"):
            return f"❌ cleanup_temp_messages must be 'true' or 'false'."
        value = value.lower()
    elif key == "command_prefix":
        if len(value) != 1:
            return "❌ command_prefix must be exactly 1 character."

    db.config_set(key, value, sender)
    desc, _ = _CONFIGURABLE[key]
    return f"✅ Config updated!\n{key} = {value!r}\n{desc}"


# need asyncio for imagine timeout handling
import asyncio
