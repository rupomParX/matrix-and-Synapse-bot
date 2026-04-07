"""
ws_server.py — WebSocket server for RoseBot dashboard
Runs on ws://localhost:7842
Streams: log lines, live stats, room list updates

Protocol (JSON messages):
  Server → Client:
    { "type": "log",   "level": "INFO"|"WARN"|"ERROR"|"OK", "msg": "..." }
    { "type": "stats", "data": { user_count, cmd_count, coins, rooms, rich, top_users, top_cmds } }
    { "type": "rooms", "data": [ { room_id, name, member_count } ] }
    { "type": "pong" }

  Client → Server:
    { "type": "ping" }
    { "type": "get_stats" }
    { "type": "get_rooms" }
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

log = logging.getLogger("rosebot.ws")
DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
AUTH_COOKIE_NAME = "rosebot_dashboard_auth"

# All connected dashboard clients
_clients: set = set()

# Reference to the nio bot client (set by bot.py after init)
_bot_ref = None


def set_bot_ref(bot):
    global _bot_ref
    _bot_ref = bot


def _build_stats() -> dict:
    try:
        import db
        stats = db.get_dashboard_stats()
        stats["rooms"] = len(_bot_ref.rooms) if _bot_ref else 0
        return stats
    except Exception as e:
        return {"error": str(e)}


def _build_rooms() -> list:
    if not _bot_ref:
        return []
    try:
        rooms = []
        for room_id, room in _bot_ref.rooms.items():
            rooms.append({
                "room_id": room_id,
                "name": room.display_name or room_id,
                "member_count": room.member_count,
                "encrypted": room_id in _bot_ref.encrypted_rooms,
            })
        return sorted(rooms, key=lambda r: r["name"])
    except Exception:
        return []


async def _send(ws, msg: dict):
    try:
        await ws.send_str(json.dumps(msg))
    except Exception:
        pass


async def broadcast(msg: dict):
    """Broadcast a message to all connected dashboard clients."""
    dead = set()
    for ws in _clients:
        try:
            await ws.send_str(json.dumps(msg))
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def broadcast_log(level: str, message: str):
    """Shorthand to broadcast a log line."""
    await broadcast({
        "type": "log",
        "level": level,
        "msg":   message,
        "ts":    int(time.time()),
    })


async def _stats_loop():
    """Push stats to all clients every 5 seconds."""
    while True:
        await asyncio.sleep(5)
        if _clients:
            await broadcast({"type": "stats", "data": _build_stats()})


async def _ws_handler(request):
    from aiohttp import web, WSMsgType
    if DASHBOARD_PASSWORD and request.cookies.get(AUTH_COOKIE_NAME) != "1":
        return web.Response(status=401, text="Unauthorized")
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    _clients.add(ws)
    log.info(f"Dashboard client connected. Total: {len(_clients)}")

    # Send initial data burst
    await _send(ws, {"type": "stats", "data": _build_stats()})
    await _send(ws, {"type": "rooms", "data": _build_rooms()})

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    t = data.get("type")
                    if t == "ping":
                        await _send(ws, {"type": "pong"})
                    elif t == "get_stats":
                        await _send(ws, {"type": "stats", "data": _build_stats()})
                    elif t == "get_rooms":
                        await _send(ws, {"type": "rooms", "data": _build_rooms()})
                except Exception:
                    pass
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        _clients.discard(ws)
        log.info(f"Dashboard client disconnected. Total: {len(_clients)}")
    return ws


async def _login(request):
    from aiohttp import web
    data = await request.json()
    password = data.get("password", "")
    if DASHBOARD_PASSWORD and password == DASHBOARD_PASSWORD:
        response = web.json_response({"success": True})
        response.set_cookie(AUTH_COOKIE_NAME, "1", httponly=True, path="/")
        return response
    return web.json_response({"success": False}, status=401)


async def _auth_status(request):
    from aiohttp import web
    authenticated = not DASHBOARD_PASSWORD or request.cookies.get(AUTH_COOKIE_NAME) == "1"
    return web.json_response({"authenticated": authenticated})


async def _get_users(request):
    from aiohttp import web
    import db
    room_id = request.query.get("room_id", "")
    users = [dict(r) for r in db.get_all_users(200)]
    banned = db.get_banned_mxids(room_id) if room_id else []
    return web.json_response({"users": users, "banned": banned, "room_id": room_id})


async def _get_rooms(request):
    from aiohttp import web
    return web.json_response(_build_rooms())


async def _ban_user(request):
    from aiohttp import web
    import db
    data = await request.json()
    mxid = str(data.get("mxid", "")).strip()
    room_id = str(data.get("room_id", "")).strip()
    reason = str(data.get("reason", "Manual dashboard ban")).strip() or "Manual dashboard ban"
    if not mxid or not room_id:
        return web.json_response({"error": "mxid and room_id are required"}, status=400)
    db.add_ban(mxid, room_id, reason, "dashboard")
    return web.json_response({"success": True, "mxid": mxid, "room_id": room_id})


async def _unban_user(request):
    from aiohttp import web
    import db
    data = await request.json()
    mxid = str(data.get("mxid", "")).strip()
    room_id = str(data.get("room_id", "")).strip()
    if not mxid or not room_id:
        return web.json_response({"error": "mxid and room_id are required"}, status=400)
    db.remove_ban(mxid, room_id)
    return web.json_response({"success": True, "mxid": mxid, "room_id": room_id})


async def _get_config(request):
    from aiohttp import web
    import db
    return web.json_response(db.config_list())


async def _set_config(request):
    from aiohttp import web
    import db
    data = await request.json()
    key = str(data.get("key", "")).strip()
    value = str(data.get("value", "")).strip()
    if not key:
        return web.json_response({"error": "Missing key"}, status=400)
    db.config_set(key, value, updated_by="dashboard")
    return web.json_response({"success": True, "key": key, "value": value})


async def start_ws_server(host: str = "0.0.0.0", port: int = 7842):
    """Start the aiohttp WebSocket server. Call with asyncio.create_task()."""
    from aiohttp import web

    async def auth_middleware(app, handler):
        async def middleware_handler(request):
            if request.path in ("/", "/api/login", "/api/auth-status", "/ws"):
                return await handler(request)
            if DASHBOARD_PASSWORD and request.cookies.get(AUTH_COOKIE_NAME) != "1":
                return web.Response(status=401, text="Unauthorized")
            return await handler(request)
        return middleware_handler

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", lambda request: web.FileResponse(DASHBOARD_HTML))
    app.router.add_post("/api/login", _login)
    app.router.add_get("/api/auth-status", _auth_status)
    app.router.add_get("/api/users", _get_users)
    app.router.add_get("/api/rooms", _get_rooms)
    app.router.add_post("/api/ban", _ban_user)
    app.router.add_post("/api/unban", _unban_user)
    app.router.add_get("/api/config", _get_config)
    app.router.add_post("/api/config", _set_config)
    app.router.add_get("/ws", _ws_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info(f"Dashboard HTTP server on http://{host}:{port}/")
    log.info(f"Dashboard WebSocket server on ws://{host}:{port}/ws")

    asyncio.create_task(_stats_loop())


# ─── Log handler that forwards to WebSocket ───────────────────────────────────

class WSLogHandler(logging.Handler):
    """Attach to any logger to forward records to dashboard clients."""

    LEVEL_MAP = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO",
        logging.WARNING:  "WARN",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "ERROR",
    }

    def __init__(self):
        super().__init__()
        self._loop = None

    def set_loop(self, loop):
        self._loop = loop

    def emit(self, record: logging.LogRecord):
        if self._loop is None or self._loop.is_closed():
            return
        level = self.LEVEL_MAP.get(record.levelno, "INFO")
        msg   = self.format(record)
        # schedule async broadcast without blocking
        asyncio.run_coroutine_threadsafe(
            broadcast({"type": "log", "level": level, "msg": msg, "ts": int(time.time())}),
            self._loop,
        )
