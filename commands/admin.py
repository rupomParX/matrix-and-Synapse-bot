"""
commands/admin.py — Admin/moderation commands for RoseBot
!kick  !ban  !unban  !banlist
Admins = configured in .env BOT_ADMINS or room power level >= 50
"""

import os
import db

ADMIN_MXIDS: set[str] = set(
    x.strip() for x in os.getenv("BOT_ADMINS", "").split(",") if x.strip()
)

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


def is_admin(mxid: str, power_level: int = 0) -> bool:
    return mxid in ADMIN_MXIDS or power_level >= 50


def cmd_kick_check(sender: str, sender_power: int, args: str) -> tuple[str | None, str | None, str | None]:
    """Returns (target_mxid, reason, error)"""
    if not is_admin(sender, sender_power):
        return None, None, "❌ You don't have permission to kick users."
    parts = args.strip().split(None, 1)
    if not parts or not parts[0].startswith("@"):
        return None, None, "Usage: !kick <@user:server> [reason]"
    target = normalize_mxid(parts[0])
    reason = parts[1] if len(parts) > 1 else "No reason provided"
    return target, reason, None


def cmd_ban_check(sender: str, sender_power: int, args: str, room_id: str) -> tuple[str | None, str | None, str | None]:
    """Returns (target_mxid, reason, error)"""
    if not is_admin(sender, sender_power):
        return None, None, "❌ You don't have permission to ban users."
    parts = args.strip().split(None, 1)
    if not parts or not parts[0].startswith("@"):
        return None, None, "Usage: !ban <@user:server> [reason]"
    target = normalize_mxid(parts[0])
    reason = parts[1] if len(parts) > 1 else "No reason provided"
    db.add_ban(target, room_id, reason, sender)
    return target, reason, None


def cmd_unban_check(sender: str, sender_power: int, args: str, room_id: str) -> tuple[str | None, str | None]:
    """Returns (target_mxid, error)"""
    if not is_admin(sender, sender_power):
        return None, "❌ You don't have permission to unban users."
    target = args.strip()
    if not target.startswith("@"):
        return None, "Usage: !unban <@user:server>"
    target = normalize_mxid(target)
    db.remove_ban(target, room_id)
    return target, None


def cmd_banlist(sender: str, sender_power: int, room_id: str) -> str:
    if not is_admin(sender, sender_power):
        return "❌ You don't have permission to view bans."
    rows = db.get_ban_list(room_id)
    if not rows:
        return "✅ No bans in this room."
    from datetime import datetime
    lines = []
    for row in rows:
        ts = datetime.utcfromtimestamp(row["banned_at"]).strftime("%Y-%m-%d")
        lines.append(f"• {row['mxid']}\n  Reason: {row['reason']}\n  By: {row['banned_by']} on {ts}")
    return f"🚫 Ban List ({len(rows)})\n━━━━━━━━━━━━━━\n" + "\n\n".join(lines)
