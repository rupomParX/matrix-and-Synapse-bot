"""
commands/games.py — Crash game + Bank economy for RoseBot
!bank  !bank add <@user> <amount>  !daily  !crash <bet> <multiplier>  !give <@user> <amount>
!loan <amount>  !richlist  !gamestats
"""

import asyncio
import os
import random
import time
from typing import TYPE_CHECKING

import db
from commands.admin import is_admin

DAILY_REWARD     = int(__import__("os").getenv("DAILY_REWARD", 500))
CRASH_MAX_BET    = int(__import__("os").getenv("CRASH_MAX_BET", 10000))
STARTING_BALANCE = int(__import__("os").getenv("BANK_STARTING_BALANCE", 1000))

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


# ─── Active crash games per room (room_id → set of player mxids) ──────────────
_active_crashes: dict[str, bool] = {}


def _seconds_to_hms(s: int) -> str:
    h, rem = divmod(s, 3600)
    m, s   = divmod(rem, 60)
    parts  = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ─── BANK ─────────────────────────────────────────────────────────────────────

def cmd_bank(mxid: str, args: str = "", power_level: int = 0) -> str:
    if args.startswith("add"):
        if not is_admin(mxid, power_level):
            return "❌ Admin only."
        parts = args.split(None, 2)
        if len(parts) == 2:
            target = mxid
            amount_str = parts[1]
        elif len(parts) == 3:
            target = parts[1]
            amount_str = parts[2]
        else:
            return "Usage: !bank add <@user> <amount>"

        if target.startswith("@"):
            target = normalize_mxid(target)
        else:
            return "❌ Target must be a Matrix ID like @user or @user:server."

        try:
            amount = int(amount_str)
        except ValueError:
            return "❌ Invalid amount."
        if amount <= 0:
            return "❌ Amount must be positive."

        db.upsert_user(target)
        new_bal = db.update_balance(target, amount, f"admin add from {mxid}")
        return f"✅ Added {amount:,} coins to {target}. New balance: {new_bal:,} coins"

    # Show balance
    row = db.get_or_create_bank(mxid, STARTING_BALANCE)
    bal    = f'{row["balance"]:,}'
    won    = f'{row["total_won"]:,}'
    lost   = f'{row["total_lost"]:,}'
    played = row["games_played"]
    net    = row["total_won"] - row["total_lost"]
    net_s  = f'+{net:,}' if net >= 0 else f'{net:,}'
    return (
        f"🏦 Bank — {mxid}\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 Balance:    {bal} coins\n"
        f"🎮 Games:      {played}\n"
        f"✅ Total Won:  {won} coins\n"
        f"❌ Total Lost: {lost} coins\n"
        f"📈 Net:        {net_s} coins"
    )


def cmd_daily(mxid: str) -> str:
    db.upsert_user(mxid)
    ok, remaining = db.claim_daily(mxid, DAILY_REWARD)
    if ok:
        new_bal = db.get_balance(mxid)
        return (
            f"🎁 Daily reward claimed!\n"
            f"+{DAILY_REWARD:,} coins added.\n"
            f"💰 New balance: {new_bal:,} coins"
        )
    else:
        return f"⏳ Daily already claimed. Come back in {_seconds_to_hms(remaining)}."


def cmd_give(sender: str, args: str) -> str:
    """!give @user:server <amount>"""
    parts = args.strip().split()
    if len(parts) < 2:
        return "Usage: !give <@user:server> <amount>"
    target, amount_str = parts[0], parts[1]
    if not target.startswith("@"):
        return "❌ Target must be a Matrix ID like @user:server"
    
    target = normalize_mxid(target)
    
    try:
        amount = int(amount_str)
    except ValueError:
        return "❌ Amount must be a whole number."
    if amount <= 0:
        return "❌ Amount must be positive."

    sender_bal = db.get_balance(sender)
    if sender_bal < amount:
        return f"❌ Insufficient funds. You have {sender_bal:,} coins."

    db.update_balance(sender, -amount, f"give → {target}")
    db.upsert_user(target)
    db.update_balance(target, amount, f"receive ← {sender}")
    new_sender = db.get_balance(sender)
    new_target = db.get_balance(target)
    return (
        f"💸 Transfer complete!\n"
        f"{sender} → {target}: {amount:,} coins\n"
        f"Your balance: {new_sender:,} coins\n"
        f"{target} balance: {new_target:,} coins"
    )


def cmd_richlist() -> str:
    rows = db.get_rich_list(10)
    if not rows:
        return "📊 No bank data yet."
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name  = row["display_name"] or row["mxid"]
        bal   = f'{row["balance"]:,}'
        lines.append(f"{medal} {name} — {bal} coins")
    return "💰 Rich List\n━━━━━━━━━━━━━━\n" + "\n".join(lines)


# ─── CRASH GAME ───────────────────────────────────────────────────────────────
#
# How it works:
#   1. Player bets coins and picks a target multiplier (e.g. 2.0x)
#   2. Bot generates a random crash point (exponential distribution, min 1.0)
#   3. If crash_point >= target → player wins bet * target
#   4. If crash_point < target → player loses bet
#
# House edge is built into the crash distribution (scale factor < 1.0).
#
# To prevent spam, one crash game per room at a time.

def _generate_crash_point() -> float:
    """
    Returns a crash multiplier using exponential distribution.
    ~50% chance of crashing below 2x, ~25% below 1.5x, rare high values possible.
    """
    r = random.random()
    if r < 0.01:
        return round(random.uniform(10.0, 50.0), 2)  # 1% jackpot
    # house edge: scale so EV is slightly below 1
    crash = max(1.0, round(1.0 / (1.0 - random.uniform(0.0, 0.97)), 2))
    return min(crash, 100.0)


async def cmd_crash(mxid: str, room_id: str, args: str) -> tuple[str, bool]:
    """
    Returns (message, won: bool).
    Usage: !crash <bet> <multiplier>
    Example: !crash 500 2.5
    """
    if _active_crashes.get(room_id):
        return "⚠️ A crash game is already running in this room. Wait for it to finish.", False

    parts = args.strip().split()
    if len(parts) < 2:
        return (
            "Usage: !crash <bet> <multiplier>\n"
            "Example: !crash 500 2.5\n"
            "If the rocket doesn't crash before your multiplier, you win!\n"
            f"Max bet: {CRASH_MAX_BET:,} coins", False
        )

    try:
        bet = int(parts[0])
        target = float(parts[1])
    except ValueError:
        return "❌ Bet must be a whole number and multiplier a decimal (e.g. 2.5).", False

    if bet <= 0:
        return "❌ Bet must be positive.", False
    if bet > CRASH_MAX_BET:
        return f"❌ Max bet is {CRASH_MAX_BET:,} coins.", False
    if target < 1.01:
        return "❌ Multiplier must be at least 1.01.", False
    if target > 100.0:
        return "❌ Multiplier can't exceed 100.0.", False

    db.upsert_user(mxid)
    balance = db.get_balance(mxid)
    if balance < bet:
        return f"❌ Insufficient funds. You have {balance:,} coins.", False

    _active_crashes[room_id] = True
    try:
        # Deduct bet immediately
        db.update_balance(mxid, -bet, f"crash bet")

        # Suspense delay
        await asyncio.sleep(random.uniform(1.5, 3.5))

        crash_point = _generate_crash_point()
        won = crash_point >= target

        if won:
            winnings = int(bet * target)
            profit   = winnings - bet
            db.update_balance(mxid, winnings, "crash win")
            db.record_game(mxid, profit, 0)
            new_bal = db.get_balance(mxid)
            msg = (
                f"🚀 Crash Game — WINNER!\n"
                f"━━━━━━━━━━━━━━\n"
                f"🎯 Your target:  {target}x\n"
                f"💥 Crashed at:   {crash_point}x\n"
                f"✅ You won {profit:,} coins! ({winnings:,} returned)\n"
                f"💰 New balance: {new_bal:,} coins"
            )
        else:
            db.record_game(mxid, 0, bet)
            new_bal = db.get_balance(mxid)
            msg = (
                f"🚀 Crash Game — BUST!\n"
                f"━━━━━━━━━━━━━━\n"
                f"🎯 Your target:  {target}x\n"
                f"💥 Crashed at:   {crash_point}x\n"
                f"❌ You lost {bet:,} coins.\n"
                f"💰 New balance: {new_bal:,} coins"
            )
        return msg, won
    finally:
        _active_crashes[room_id] = False


def cmd_gamestats(mxid: str) -> str:
    row = db.get_or_create_bank(mxid, STARTING_BALANCE)
    played = row["games_played"]
    if played == 0:
        return f"📊 {mxid} hasn't played any games yet."
    won   = row["total_won"]
    lost  = row["total_lost"]
    net   = won - lost
    net_s = f'+{net:,}' if net >= 0 else f'{net:,}'
    return (
        f"📊 Game Stats — {mxid}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 Games Played: {played}\n"
        f"✅ Total Won:    {won:,} coins\n"
        f"❌ Total Lost:   {lost:,} coins\n"
        f"📈 Net:          {net_s} coins"
    )


def cmd_loan(mxid: str, args: str) -> str:
    amount_str = args.strip()
    try:
        amount = int(amount_str)
    except ValueError:
        return "Usage: !loan <amount> (max 10,000)"
    if amount <= 0 or amount > 10000:
        return "❌ Amount must be between 1 and 10,000."
    db.update_balance(mxid, amount, "loan")
    new_bal = db.get_balance(mxid)
    return (
        f"💸 Loan granted! +{amount:,} coins added.\n"
        f"💰 New balance: {new_bal:,} coins"
    )
