"""
bot.py — RoseBot: Standalone Matrix bot
Run: python bot.py
"""

import asyncio
import logging
import os
import re
import sys
import time
from collections import deque
from pathlib import Path

import aiohttp
import nio
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))

import db
from commands import anilist as al
from commands import games
from commands import utils
from commands import admin as adm
from commands import extras
import ws_server

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rosebot")

_ws_log_handler = ws_server.WSLogHandler()
_ws_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))
logging.getLogger().addHandler(_ws_log_handler)

# ─── Credentials (always from env, never DB) ──────────────────────────────────
HOMESERVER   = os.getenv("MATRIX_HOMESERVER", "http://private.animebd.xyz")
USER_ID      = os.getenv("MATRIX_USER",        "@rose:private.animebd.xyz")
DEVICE_ID    = os.getenv("MATRIX_DEVICE_ID",   "")
TOKEN        = os.getenv("MATRIX_TOKEN",        "")
DEVICE_NAME  = os.getenv("MATRIX_DEVICE_NAME", "RoseBot")
MEGOLM_PASS  = os.getenv("MATRIX_MEGOLM_PASSPHRASE", "")

STORE_PATH        = Path(__file__).parent / "store"
STORE_PATH_NIO    = STORE_PATH / "nio"
STORE_PATH_MEGOLM = STORE_PATH / "megolm"
NEXT_BATCH_FILE   = STORE_PATH / "next_batch.txt"
DOWNLOADS_DIR     = Path(__file__).parent / "downloads"

for p in (STORE_PATH, STORE_PATH_NIO, STORE_PATH_MEGOLM, DOWNLOADS_DIR):
    p.mkdir(parents=True, exist_ok=True)

# ─── Matrix client ────────────────────────────────────────────────────────────
bot = nio.AsyncClient(
    HOMESERVER, USER_ID,
    store_path=str(STORE_PATH_NIO),
    config=nio.AsyncClientConfig(store_sync_tokens=True),
)
bot.restore_login(USER_ID, DEVICE_ID, TOKEN)
log.info("E2EE available." if bot.olm else "E2EE NOT available.")

db.init_db()

MENTION_RE = re.compile(r"@[\w.\-]+:[A-Za-z0-9.\-]+")
_command_history: dict[str, deque[float]] = {}
_bot_message_history: dict[str, deque[tuple[int, str]]] = {}
HISTORY_LIMIT = 500

# ─── Live config helpers ──────────────────────────────────────────────────────

def cfg(key: str) -> str:
    return db.config_get(key)

def cfg_int(key: str, default: int = 0) -> int:
    try:
        return int(db.config_get(key) or default)
    except (ValueError, TypeError):
        return default

def is_command_allowed(mxid: str) -> bool:
    limit  = cfg_int("command_rate_limit", 10)
    window = cfg_int("command_rate_window", 60)
    if limit <= 0 or window <= 0:
        return True
    now = time.time()
    dq = _command_history.setdefault(mxid, deque())
    while dq and dq[0] <= now - window:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True

# ─── Matrix send helpers ──────────────────────────────────────────────────────

def _record_bot_text(room_id: str, event_id: str):
    if not event_id:
        return
    dq = _bot_message_history.setdefault(room_id, deque())
    dq.append((int(time.time()), event_id))
    if len(dq) > HISTORY_LIMIT:
        dq.popleft()


async def clear_recent_bot_text(room_id: str, seconds: int) -> int:
    cutoff = int(time.time()) - seconds
    dq = _bot_message_history.get(room_id)
    if not dq:
        return 0
    remaining = deque()
    deleted = 0
    while dq:
        ts, eid = dq.popleft()
        if ts >= cutoff:
            await redact_message(room_id, eid, reason="clear command")
            deleted += 1
        else:
            remaining.append((ts, eid))
    _bot_message_history[room_id] = remaining
    return deleted


async def send_text(room_id: str, text: str, reply_to: str = None):
    content: dict = {"msgtype": "m.notice", "body": text}
    if reply_to:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
    resp = await bot.room_send(room_id, "m.room.message", content,
                               ignore_unverified_devices=True)
    if hasattr(resp, "status_code"):
        log.error(f"send_text error: {resp}")
    event_id = getattr(resp, "event_id", None)
    _record_bot_text(room_id, event_id)
    return resp


async def _redact_after_delay(room_id: str, event_id: str, delay: int):
    await asyncio.sleep(delay)
    await redact_message(room_id, event_id)


async def send_temp_text(room_id: str, text: str, reply_to: str = None, ttl: int = 30):
    cleanup = cfg("cleanup_temp_messages").lower() not in ("false", "0", "off", "no")
    resp = await send_text(room_id, text, reply_to)
    if not cleanup:
        return resp
    event_id = getattr(resp, "event_id", None)
    if event_id:
        asyncio.create_task(_redact_after_delay(room_id, event_id, ttl))
    return resp


async def redact_message(room_id: str, event_id: str, reason: str = "cleanup"):
    if not event_id:
        return
    try:
        resp = await bot.room_redact(room_id, event_id, reason=reason)
        if hasattr(resp, "status_code"):
            log.error(f"redact_message error: {resp}")
    except Exception:
        log.exception("Failed to redact message")


async def send_image(room_id: str, image_bytes: bytes, mimetype: str,
                     filename: str = "image.jpg", reply_to: str = None):
    import io
    encrypted = room_id in bot.encrypted_rooms
    upload, enc_info = await bot.upload(
        io.BytesIO(image_bytes), content_type=mimetype,
        filename=filename, encrypt=encrypted, filesize=len(image_bytes),
    )
    if hasattr(upload, "status_code"):
        log.error(f"Image upload error: {upload}")
        return
    if not encrypted:
        content = {"msgtype": "m.image", "url": upload.content_uri,
                   "body": filename, "info": {"size": len(image_bytes), "mimetype": mimetype}}
    else:
        enc_info["url"] = upload.content_uri
        content = {"msgtype": "m.image", "body": filename, "file": enc_info,
                   "info": {"size": len(image_bytes), "mimetype": mimetype}}
    if reply_to:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
    await bot.room_send(room_id, "m.room.message", content,
                        ignore_unverified_devices=True)


async def upload_file(room_id: str, path: Path, mimetype: str, reply_to: str = None):
    filesize  = path.stat().st_size
    encrypted = room_id in bot.encrypted_rooms
    with open(path, "rb") as f:
        upload, enc_info = await bot.upload(
            f, content_type=mimetype, filename=path.name,
            encrypt=encrypted, filesize=filesize,
        )
    if hasattr(upload, "status_code"):
        await send_text(room_id, f"❌ Upload failed: {upload}", reply_to)
        return
    msgtype = ("m.video" if mimetype.startswith("video")
               else "m.audio" if mimetype.startswith("audio") else "m.file")
    if not encrypted:
        content = {"msgtype": msgtype, "url": upload.content_uri,
                   "body": path.name, "info": {"size": filesize, "mimetype": mimetype}}
    else:
        enc_info["url"] = upload.content_uri
        content = {"msgtype": msgtype, "body": path.name, "file": enc_info,
                   "info": {"size": filesize, "mimetype": mimetype}}
    if reply_to:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
    await bot.room_send(room_id, "m.room.message", content,
                        ignore_unverified_devices=True)
    try:
        path.unlink()
    except Exception:
        pass


async def fetch_image_bytes(url: str) -> tuple[bytes | None, str]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    return await r.read(), r.content_type or "image/jpeg"
    except Exception as e:
        log.warning(f"fetch_image_bytes: {e}")
    return None, ""


async def get_power_level(room_id: str, mxid: str) -> int:
    resp = await bot.room_get_state_event(room_id, "m.room.power_levels")
    if hasattr(resp, "status_code"):
        return 0
    pl = resp.content
    return int(pl.get("users", {}).get(mxid, pl.get("users_default", 0)))


def has_banned_word(text: str) -> bool:
    raw = cfg("banned_words")
    if not raw:
        return False
    words = [w.strip().lower() for w in raw.split(",") if w.strip()]
    lower = text.lower()
    return any(w in lower for w in words)


# ─── Reminder background loop ─────────────────────────────────────────────────

async def reminder_loop():
    await asyncio.sleep(15)
    while True:
        try:
            for r in db.get_due_reminders():
                text = f"⏰ Reminder for {r['mxid']}:\n{r['message']}"
                await send_text(r["room_id"], text)
                db.mark_reminder_fired(r["id"])
                log.info(f"Fired reminder #{r['id']} for {r['mxid']}")
        except Exception:
            log.exception("reminder_loop error")
        await asyncio.sleep(15)


# ─── Command dispatcher ───────────────────────────────────────────────────────

HELP_EXTRA = (
    "\n  !mal <title>           — MyAnimeList search"
    "\n  !imagine <prompt>      — AI image generation"
    "\n  !remind <time> <msg>   — Set a reminder (e.g. 1h30m)"
    "\n  !reminders             — List your reminders"
    "\n  !cancelremind <id>     — Cancel a reminder"
    "\n  !poll Q | A | B | C   — Create a poll"
    "\n  !vote [id] <n>         — Vote in poll"
    "\n  !pollresults [id]      — Show poll results"
    "\n  !endpoll [id]          — Close a poll"    '\n  !clear <duration>      — Delete recent bot text messages (e.g. 5m, 1h)'    "\n  !config [key] [val]    — (Admin) Bot settings"
)


async def handle_command(room: nio.MatrixRoom, event: nio.RoomMessageText):
    body    = event.body.strip()
    room_id = room.room_id
    sender  = event.sender
    ev_id   = event.event_id
    ts_ms   = event.server_timestamp

    prefix = cfg("command_prefix") or "!"
    if not body.startswith(prefix):
        return

    raw   = body[len(prefix):]
    parts = raw.split(None, 1)
    if not parts:
        return
    cmd  = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    log.info(f"CMD [{room_id}] {sender}: {prefix}{cmd} {args[:80]}")
    if db.is_banned(sender, room_id):
        await send_text(room_id, "🚫 You are banned from using the bot in this room.", ev_id)
        return
    if not is_command_allowed(sender):
        await send_temp_text(room_id, "❌ Rate limit exceeded. Slow down a little before sending more commands.", ev_id)
        return
    db.log_command(sender, cmd)
    await ws_server.broadcast_log("INFO", f"CMD [{room_id[:24]}] {sender}: {prefix}{cmd} {args[:50]}")

    try:
        # ── Help ──────────────────────────────────────────────────
        if cmd == "help":
            await send_text(room_id, utils.HELP_TEXT + HELP_EXTRA, ev_id)

        # ── Utilities ─────────────────────────────────────────────
        elif cmd == "ping":
            await send_text(room_id, await utils.cmd_ping(ts_ms), ev_id)

        elif cmd == "weather":
            await send_temp_text(room_id, "⏳ Fetching weather…", ev_id)
            await send_text(room_id, await utils.cmd_weather(args), ev_id)

        elif cmd == "translate":
            await send_temp_text(room_id, "⏳ Translating…", ev_id)
            await send_text(room_id, await utils.cmd_translate(args), ev_id)

        elif cmd == "urban":
            await send_temp_text(room_id, "⏳ Looking up…", ev_id)
            await send_text(room_id, await utils.cmd_urban(args), ev_id)

        elif cmd == "yts":
            await send_temp_text(room_id, "⏳ Searching YouTube…", ev_id)
            await send_text(room_id, await utils.cmd_yts(args), ev_id)

        elif cmd == "whoami":
            r2    = await bot.get_displayname(sender)
            dname = r2.displayname if isinstance(r2, nio.ProfileGetDisplayNameResponse) else ""
            await send_text(room_id, utils.cmd_whoami(sender, dname, room_id), ev_id)

        elif cmd == "id":
            await send_text(room_id, utils.cmd_id(args.strip() or sender), ev_id)

        elif cmd == "stats":
            await send_text(room_id, utils.cmd_stats(), ev_id)

        elif cmd == "rank":
            await send_text(room_id, utils.cmd_rank(room_id), ev_id)

        # ── AniList ───────────────────────────────────────────────
        elif cmd == "anime":
            await send_temp_text(room_id, "⏳ Searching AniList…", ev_id)
            text, cover = await al.cmd_anime(args)
            if cover:
                ib, mime = await fetch_image_bytes(cover)
                if ib:
                    await send_image(room_id, ib, mime, "cover.jpg", ev_id)
            await send_text(room_id, text, ev_id)

        elif cmd == "manga":
            await send_temp_text(room_id, "⏳ Searching AniList…", ev_id)
            text, cover = await al.cmd_manga(args)
            if cover:
                ib, mime = await fetch_image_bytes(cover)
                if ib:
                    await send_image(room_id, ib, mime, "cover.jpg", ev_id)
            await send_text(room_id, text, ev_id)

        elif cmd == "character":
            await send_temp_text(room_id, "⏳ Searching AniList…", ev_id)
            text, img_url = await al.cmd_character(args)
            if img_url:
                ib, mime = await fetch_image_bytes(img_url)
                if ib:
                    await send_image(room_id, ib, mime, "character.jpg", ev_id)
            await send_text(room_id, text, ev_id)

        elif cmd == "airing":
            await send_temp_text(room_id, "⏳ Checking schedule…", ev_id)
            text, cover = await al.cmd_airing(args)
            if cover:
                ib, mime = await fetch_image_bytes(cover)
                if ib:
                    await send_image(room_id, ib, mime, "cover.jpg", ev_id)
            await send_text(room_id, text, ev_id)

        elif cmd == "top":
            await send_temp_text(room_id, "⏳ Fetching top anime…", ev_id)
            text, _ = await al.cmd_top(args)
            await send_text(room_id, text, ev_id)

        elif cmd == "studio":
            await send_temp_text(room_id, "⏳ Searching studio…", ev_id)
            text, _ = await al.cmd_studio(args)
            await send_text(room_id, text, ev_id)

        # ── MAL ───────────────────────────────────────────────────
        elif cmd == "mal":
            await send_temp_text(room_id, "⏳ Searching MyAnimeList…", ev_id)
            text, cover = await extras.cmd_mal(args)
            if cover:
                ib, mime = await fetch_image_bytes(cover)
                if ib:
                    await send_image(room_id, ib, mime, "mal_cover.jpg", ev_id)
            await send_text(room_id, text, ev_id)

        # ── Image gen ─────────────────────────────────────────────
        elif cmd == "imagine":
            await send_temp_text(room_id, "🎨 Generating image (~10-20s)…", ev_id)
            ib, mime, err = await extras.cmd_imagine(args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                await send_image(room_id, ib, mime, "generated.jpg", ev_id)
                await send_text(room_id, f"✨ {args[:100]}", ev_id)

        # ── Reminders ─────────────────────────────────────────────
        elif cmd == "remind":
            await send_text(room_id, extras.cmd_remind(sender, room_id, args), ev_id)

        elif cmd == "reminders":
            await send_text(room_id, extras.cmd_reminders(sender), ev_id)

        elif cmd == "cancelremind":
            await send_text(room_id, extras.cmd_cancelremind(sender, args), ev_id)

        # ── Polls ─────────────────────────────────────────────────
        elif cmd == "poll":
            await send_text(room_id, extras.cmd_poll(room_id, sender, args), ev_id)

        elif cmd == "vote":
            await send_text(room_id, extras.cmd_vote(room_id, sender, args), ev_id)

        elif cmd in ("pollresults", "results"):
            await send_text(room_id, extras.cmd_pollresults(room_id, args), ev_id)

        elif cmd == "endpoll":
            pl  = await get_power_level(room_id, sender)
            await send_text(room_id, extras.cmd_endpoll(room_id, sender, args, pl), ev_id)

        # ── Admin config ──────────────────────────────────────────
        elif cmd == "config":
            pl  = await get_power_level(room_id, sender)
            await send_text(room_id, extras.cmd_config(sender, pl, args), ev_id)

        # ── Media Downloads ───────────────────────────────────────
        elif cmd == "ytdl":
            status = await send_temp_text(room_id, f"⬇️ Downloading video (max {cfg_int('max_download_mb',50)}MB)…", ev_id)
            from commands.media import cmd_ytdl
            path, mime, err = await cmd_ytdl(args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                upload_status = await send_temp_text(room_id, "📤 Uploading…", ev_id)
                await upload_file(room_id, path, mime, ev_id)

        elif cmd == "mp3":
            extract_status = await send_temp_text(room_id, "⬇️ Extracting audio…", ev_id)
            from commands.media import cmd_mp3
            path, mime, err = await cmd_mp3(args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                upload_status = await send_temp_text(room_id, "📤 Uploading audio…", ev_id)
                await upload_file(room_id, path, mime, ev_id)

        elif cmd == "igdl":
            status = await send_temp_text(room_id, "⬇️ Downloading from Instagram…", ev_id)
            from commands.media import cmd_igdl
            path, mime, err = await cmd_igdl(args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                upload_status = await send_temp_text(room_id, "📤 Uploading…", ev_id)
                await upload_file(room_id, path, mime, ev_id)

        elif cmd == "fbdl":
            status = await send_temp_text(room_id, "⬇️ Downloading from Facebook…", ev_id)
            from commands.media import cmd_fbdl
            path, mime, err = await cmd_fbdl(args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                upload_status = await send_temp_text(room_id, "📤 Uploading…", ev_id)
                await upload_file(room_id, path, mime, ev_id)

        elif cmd == "xdl":
            status = await send_temp_text(room_id, "⬇️ Downloading from X/Twitter…", ev_id)
            from commands.media import cmd_xdl
            path, mime, err = await cmd_xdl(args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                upload_status = await send_temp_text(room_id, "📤 Uploading…", ev_id)
                await upload_file(room_id, path, mime, ev_id)

        elif cmd == "pixiv":
            status = await send_temp_text(room_id, "🖼 Fetching from Pixiv…", ev_id)
            from commands.media import cmd_pixiv
            results, err = await cmd_pixiv(args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                for i, (ib, mime) in enumerate(results):
                    await send_image(room_id, ib, mime, f"pixiv_{i}.jpg",
                                     ev_id if i == 0 else None)
                await send_text(room_id, f"✅ Sent {len(results)} image(s).", ev_id)

        # ── Economy & Games ───────────────────────────────────────
        elif cmd == "bank":
            pl = await get_power_level(room_id, sender)
            db.upsert_user(sender)
            await send_text(room_id, games.cmd_bank(sender, args, pl), ev_id)

        elif cmd == "daily":
            db.upsert_user(sender)
            await send_text(room_id, games.cmd_daily(sender), ev_id)

        elif cmd == "crash":
            db.upsert_user(sender)
            msg, _ = await games.cmd_crash(sender, room_id, args)
            await send_text(room_id, msg, ev_id)

        elif cmd == "give":
            db.upsert_user(sender)
            await send_text(room_id, games.cmd_give(sender, args), ev_id)

        elif cmd == "richlist":
            await send_text(room_id, games.cmd_richlist(), ev_id)

        elif cmd == "gamestats":
            await send_text(room_id, games.cmd_gamestats(sender), ev_id)

        elif cmd == "loan":
            db.upsert_user(sender)
            await send_text(room_id, games.cmd_loan(sender, args), ev_id)

        # ── Admin ─────────────────────────────────────────────────
        elif cmd == "kick":
            pl = await get_power_level(room_id, sender)
            target, reason, err = adm.cmd_kick_check(sender, pl, args)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                r2 = await bot.room_kick(room_id, target, reason)
                msg = f"👢 Kicked {target}. Reason: {reason}" if not hasattr(r2, "status_code") else f"❌ Kick failed: {r2}"
                await send_text(room_id, msg, ev_id)

        elif cmd == "clear":
            duration = args.strip()
            if not duration:
                await send_text(room_id, "Usage: !clear <duration> (e.g. 5m, 10min, 1h)", ev_id)
            else:
                secs = extras._parse_duration(duration)
                if secs is None:
                    await send_text(room_id, f'❌ Could not parse duration "{duration}". Use 5m, 30min, 1h, etc.', ev_id)
                elif secs > 86400:
                    await send_text(room_id, "❌ Maximum clear window is 24h.", ev_id)
                else:
                    deleted = await clear_recent_bot_text(room_id, secs)
                    await send_text(room_id, f"🧹 Deleted {deleted} recent bot text message(s) from the last {duration}.", ev_id)

        elif cmd == "ban":
            pl = await get_power_level(room_id, sender)
            target, reason, err = adm.cmd_ban_check(sender, pl, args, room_id)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                r2 = await bot.room_ban(room_id, target, reason)
                msg = f"🚫 Banned {target}. Reason: {reason}" if not hasattr(r2, "status_code") else f"❌ Ban failed: {r2}"
                await send_text(room_id, msg, ev_id)

        elif cmd == "unban":
            pl = await get_power_level(room_id, sender)
            target, err = adm.cmd_unban_check(sender, pl, args, room_id)
            if err:
                await send_text(room_id, err, ev_id)
            else:
                r2 = await bot.room_unban(room_id, target)
                msg = f"✅ Unbanned {target}." if not hasattr(r2, "status_code") else f"❌ Unban failed: {r2}"
                await send_text(room_id, msg, ev_id)

        elif cmd == "banlist":
            pl = await get_power_level(room_id, sender)
            await send_text(room_id, adm.cmd_banlist(sender, pl, room_id), ev_id)

    except Exception:
        log.exception(f"Error in command !{cmd} from {sender}")
        await send_text(room_id, "❌ An internal error occurred.", ev_id)


# ─── Event callbacks ──────────────────────────────────────────────────────────

async def on_sync(resp: nio.SyncResponse):
    NEXT_BATCH_FILE.write_text(resp.next_batch)


async def on_invite(room: nio.MatrixRoom, event: nio.InviteEvent):
    log.info(f"Invited to {room.room_id} by {event.sender}")
    await bot.join(room.room_id)
    log.info(f"Joined {room.room_id}")
    welcome = cfg("welcome_message")
    if welcome:
        await asyncio.sleep(1.5)
        await send_text(room.room_id, welcome)


async def on_room_member(room: nio.MatrixRoom, event: nio.RoomMemberEvent):
    if room.member_count == 1 and event.membership == "leave":
        await bot.room_leave(room.room_id)
        log.info(f"Left empty room {room.room_id}")


async def on_message(room: nio.MatrixRoom, event: nio.RoomMessageText):
    if event.sender != bot.user_id and bot.olm:
        for did, device in bot.device_store[event.sender].items():
            if not bot.olm.is_device_verified(device):
                bot.verify_device(device)

    if event.sender == bot.user_id:
        return
    if event.source.get("content", {}).get("msgtype") == "m.notice":
        return
    if event.source.get("content", {}).get("m.relates_to", {}).get("rel_type") == "m.replace":
        return
    if has_banned_word(event.body):
        return

    sender  = event.sender
    room_id = room.room_id

    r2    = await bot.get_displayname(sender)
    dname = r2.displayname if isinstance(r2, nio.ProfileGetDisplayNameResponse) else None
    db.upsert_user(sender, dname)
    db.increment_message(sender, room_id)

    for m in MENTION_RE.findall(event.body):
        if m != bot.user_id:
            db.upsert_user(m)

    if cfg("mention_required") == "true" and bot.user_id not in event.body:
        return

    await handle_command(room, event)


async def on_reaction(room: nio.MatrixRoom, event: nio.ReactionEvent):
    pass


async def on_verify(event: nio.KeyVerificationEvent):
    try:
        if isinstance(event, nio.KeyVerificationStart):
            await bot.accept_key_verification(event.transaction_id)
            await bot.to_device(bot.key_verifications[event.transaction_id].share_key())
        elif isinstance(event, nio.KeyVerificationKey):
            await bot.confirm_short_auth_string(event.transaction_id)
        elif isinstance(event, nio.KeyVerificationMac):
            await bot.to_device(bot.key_verifications[event.transaction_id].get_mac())
    except Exception:
        log.exception("Key verification error")


# ─── Startup ──────────────────────────────────────────────────────────────────

async def start():
    log.info(f"Starting RoseBot as {USER_ID} on {HOMESERVER}")

    loop = asyncio.get_running_loop()
    _ws_log_handler.set_loop(loop)
    ws_server.set_bot_ref(bot)
    await ws_server.start_ws_server("0.0.0.0", 7842)
    log.info("Dashboard WS server started on ws://0.0.0.0:7842/ws")

    try:
        stored = NEXT_BATCH_FILE.read_text().strip()
        if stored:
            bot.next_batch = stored
    except FileNotFoundError:
        bot.next_batch = None

    if bot.olm and bot.should_upload_keys:
        log.info("Uploading E2EE keys…")
        resp = await bot.keys_upload()
        if isinstance(resp, nio.KeysUploadError):
            log.error(f"Key upload failed: {resp}")

    if bot.olm and MEGOLM_PASS:
        restore = STORE_PATH_MEGOLM / "restore.txt"
        if restore.exists():
            log.info("Importing megolm backup…")
            await bot.import_keys(str(restore), MEGOLM_PASS)

    if DEVICE_NAME and DEVICE_ID:
        asyncio.create_task(bot.update_device(DEVICE_ID, {"display_name": DEVICE_NAME}))

    log.info("Initial sync (discarding old messages)…")
    resp = await bot.sync(timeout=10000, since=bot.next_batch,
                          full_state=True, set_presence="unavailable")
    if isinstance(resp, nio.SyncError):
        log.warning(f"Sync failed ({resp.status_code}), retrying fresh…")
        bot.next_batch = None
        resp = await bot.sync(timeout=10000, since=None, full_state=True, set_presence="unavailable")
        if isinstance(resp, nio.SyncError):
            log.error(f"Sync failed: {resp.message}")
            return

    bot.add_response_callback(on_sync, nio.SyncResponse)
    bot.add_event_callback(on_invite, nio.InviteEvent)
    bot.add_event_callback(on_room_member, nio.RoomMemberEvent)
    bot.add_event_callback(on_message, nio.RoomMessageText)
    bot.add_event_callback(on_reaction, nio.ReactionEvent)
    bot.add_to_device_callback(on_verify, nio.KeyVerificationEvent)

    asyncio.create_task(reminder_loop())

    log.info("RoseBot online. Sync loop starting…")
    await bot.set_presence("online", "RoseBot 🌹")

    try:
        await bot.sync_forever(timeout=30000, full_state=False)
    finally:
        log.info("Shutdown.")
        if bot.olm and MEGOLM_PASS:
            from time import strftime
            backup = STORE_PATH_MEGOLM / f"backup-{strftime('%Y-%m')}.txt"
            if backup.exists():
                backup.unlink()
            await bot.export_keys(str(backup), MEGOLM_PASS)
        await bot.set_presence("offline")
        await bot.close()


if __name__ == "__main__":
    asyncio.run(start())
