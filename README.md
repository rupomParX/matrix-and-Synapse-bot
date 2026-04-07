# 🌹 RoseBot — Standalone Matrix Bot

A fully standalone Matrix bot with E2EE, AniList, media downloads, economy games, and more.

---

## 📁 Project Structure

```
rosebot/
├── bot.py                  ← Main bot (run this)
├── db.py                   ← SQLite database layer
├── .env                    ← Your credentials (copy from .env.example)
├── .env.example            ← Template
├── requirements.txt
├── commands/
│   ├── anilist.py          ← !anime !manga !character !airing !top !studio
│   ├── media.py            ← !ytdl !mp3 !igdl !fbdl !xdl !pixiv
│   ├── games.py            ← !crash !bank !daily !give !richlist
│   ├── utils.py            ← !ping !weather !translate !urban !yts !whoami !id !stats !rank
│   └── admin.py            ← !kick !ban !unban !banlist
├── web/
│   └── dashboard.html      ← Open this in a browser for the config UI
├── store/                  ← Auto-created — Matrix session data & E2EE keys
├── downloads/              ← Auto-created — Temp media files (auto-cleaned)
└── data/
    └── rosebot.db          ← Auto-created — SQLite database
```

---

## ⚡ Quick Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
# Also needs ffmpeg for audio/video:
sudo apt install ffmpeg        # Debian/Ubuntu
brew install ffmpeg            # macOS
```

### 2. Configure credentials
```bash
cp .env.example .env
nano .env   # Fill in your Matrix token, device ID, etc.
```

Or open `web/dashboard.html` in a browser → Configuration tab → Save (.env copied to clipboard).

### 3. Run the bot
```bash
python bot.py
```

That's it. The bot will:
- Do an initial silent sync (discards old messages)
- Register E2EE keys if needed
- Start listening for commands

---

## 🔑 Getting Your Matrix Credentials

You need:
| Field | Where to find it |
|---|---|
| `MATRIX_HOMESERVER` | Your homeserver URL |
| `MATRIX_USER` | Your bot's full MXID e.g. `@rose:server.com` |
| `MATRIX_DEVICE_ID` | Element → Settings → Security → Session ID |
| `MATRIX_TOKEN` | Element → Settings → Help & About → Access Token |
| `MATRIX_MEGOLM_PASSPHRASE` | Any strong passphrase you choose |

---

## 🎮 Commands

| Command | Description |
|---|---|
| `!anime <title>` | Anime info + cover image |
| `!manga <title>` | Manga info |
| `!character <name>` | Character info |
| `!airing <title>` | Airing schedule |
| `!top [genre]` | Top rated anime |
| `!studio <name>` | Studio info |
| `!ytdl <url>` | Download YouTube video → uploads to room |
| `!mp3 <url>` | YouTube → MP3 → uploads to room |
| `!igdl <url>` | Instagram download |
| `!fbdl <url>` | Facebook video download |
| `!xdl <url>` | X/Twitter video download |
| `!pixiv <url\|id>` | Pixiv artwork via pixiv.cat |
| `!bank` | Your balance |
| `!daily` | Claim daily 500 coins |
| `!crash <bet> <mult>` | Crash gambling game |
| `!give <@user> <amt>` | Transfer coins |
| `!richlist` | Top 10 balances |
| `!ping` | Latency check |
| `!weather <city>` | Current weather |
| `!translate <lang> <text>` | Translate text |
| `!urban <term>` | Urban Dictionary |
| `!yts <query>` | YouTube search |
| `!whoami` | Your profile + stats |
| `!id <@user>` | Look up a user |
| `!stats` | Global bot stats |
| `!rank` | Room leaderboard |
| `!kick <@user>` | Admin: kick user |
| `!ban <@user>` | Admin: ban user |
| `!unban <@user>` | Admin: unban user |
| `!banlist` | Admin: list bans |
| `!help` | Full command list |

---

## 🛡 Admin Setup

Set admin MXIDs in `.env`:
```
BOT_ADMINS=@yourmxid:server.com,@other:server.com
```

OR: any room member with power level ≥ 50 can use admin commands.

---

## 🎮 Crash Game

```
!crash 500 2.5
```
Bets 500 coins. If the rocket doesn't crash before 2.5x, you win `500 × 2.5 = 1250 coins`.
If it crashes before 2.5x, you lose 500 coins.

The crash point is randomly generated — ~50% chance below 2x, with rare jackpots up to 50x.

---

## 🔧 Adding Commands

Each command file in `commands/` is independent:
1. Write an `async def cmd_yourcommand(args: str) -> str` function
2. Wire it in `bot.py` under `handle_command()` with `elif cmd == "yourcommand":`

---

## 📜 Logs

```
2024-01-15 12:34:56 [INFO] rosebot: Starting RoseBot...
2024-01-15 12:34:57 [INFO] rosebot: E2EE available.
2024-01-15 12:34:58 [INFO] rosebot: RoseBot online.
2024-01-15 12:35:01 [INFO] rosebot: CMD [!room:server] @user: !anime Re:Zero
```
