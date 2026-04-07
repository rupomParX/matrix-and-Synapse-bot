"""
commands/media.py — Media download commands for RoseBot
!ytdl <url>  !mp3 <url>  !igdl <url>  !fbdl <url>  !xdl <url>  !pixiv <url|id>
All files are uploaded directly to the Matrix room.
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path

import aiohttp
import aiofiles

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB Matrix upload limit (safe default)


# ─── yt-dlp helper ────────────────────────────────────────────────────────────

async def _ytdlp_download(url: str, extra_opts: list[str] = None) -> tuple[Path | None, str]:
    """
    Run yt-dlp in a subprocess. Returns (file_path, error_msg).
    file_path is None on failure.
    """
    outdir = DOWNLOADS_DIR
    outtmpl = str(outdir / "%(title).60s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize", "50m",
        "-o", outtmpl,
        "--print", "after_move:filepath",
    ]
    if extra_opts:
        cmd.extend(extra_opts)
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip().splitlines()
        last = next((l for l in reversed(err) if l.strip()), "Unknown error")
        return None, last

    lines = stdout.decode(errors="replace").strip().splitlines()
    # last non-empty line is the filepath
    for line in reversed(lines):
        line = line.strip()
        if line and Path(line).exists():
            return Path(line), ""
    return None, "yt-dlp did not return a valid file path."


# ─── YouTube video ────────────────────────────────────────────────────────────

async def cmd_ytdl(args: str) -> tuple[Path | None, str, str]:
    """Returns (file_path, mimetype, error)"""
    url = args.strip()
    if not url:
        return None, "", "Usage: !ytdl <YouTube/video URL>"
    path, err = await _ytdlp_download(url, [
        "-f", "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best",
        "--merge-output-format", "mp4",
    ])
    if not path:
        return None, "", f"❌ Download failed: {err}"
    return path, "video/mp4", ""


# ─── YouTube → MP3 ────────────────────────────────────────────────────────────

async def cmd_mp3(args: str) -> tuple[Path | None, str, str]:
    url = args.strip()
    if not url:
        return None, "", "Usage: !mp3 <YouTube/audio URL>"
    path, err = await _ytdlp_download(url, [
        "-x", "--audio-format", "mp3", "--audio-quality", "5",
    ])
    if not path:
        return None, "", f"❌ Download failed: {err}"
    return path, "audio/mpeg", ""


# ─── Instagram ────────────────────────────────────────────────────────────────

async def cmd_igdl(args: str) -> tuple[Path | None, str, str]:
    url = args.strip()
    if not url or "instagram.com" not in url:
        return None, "", "Usage: !igdl <Instagram post/reel URL>"
    path, err = await _ytdlp_download(url, [
        "-f", "best",
        "--add-header", "User-Agent:Mozilla/5.0",
    ])
    if not path:
        return None, "", f"❌ Download failed: {err}"
    mime = "video/mp4" if path.suffix.lower() in (".mp4", ".mov", ".webm") else "image/jpeg"
    return path, mime, ""


# ─── Facebook ─────────────────────────────────────────────────────────────────

async def cmd_fbdl(args: str) -> tuple[Path | None, str, str]:
    url = args.strip()
    if not url or ("facebook.com" not in url and "fb.watch" not in url):
        return None, "", "Usage: !fbdl <Facebook video URL>"
    path, err = await _ytdlp_download(url, ["-f", "best"])
    if not path:
        return None, "", f"❌ Download failed: {err}"
    return path, "video/mp4", ""


# ─── X / Twitter ──────────────────────────────────────────────────────────────

async def cmd_xdl(args: str) -> tuple[Path | None, str, str]:
    url = args.strip()
    if not url or not re.search(r"(x\.com|twitter\.com)", url):
        return None, "", "Usage: !xdl <X/Twitter post URL>"
    path, err = await _ytdlp_download(url, ["-f", "best"])
    if not path:
        return None, "", f"❌ Download failed: {err}"
    mime = "video/mp4" if path.suffix.lower() in (".mp4", ".mov", ".webm") else "image/jpeg"
    return path, mime, ""


# ─── Pixiv (via pixiv.cat proxy) ──────────────────────────────────────────────

PIXIV_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?pixiv\.net/(?:en/)?artworks/(\d+)|^(\d+)$"
)


async def cmd_pixiv(args: str) -> tuple[list[tuple[bytes, str]] | None, str]:
    """
    Returns ([(image_bytes, mimetype), ...], error_msg)
    Uses pixiv.cat as proxy — no login needed.
    """
    args = args.strip()
    m = PIXIV_URL_RE.search(args)
    if not m:
        return None, "Usage: !pixiv <pixiv artwork URL or artwork ID>"

    artwork_id = m.group(1) or m.group(2)
    results = []

    async with aiohttp.ClientSession() as session:
        # pixiv.cat supports /artwork_id.jpg and /artwork_id-p0.jpg etc.
        # Try fetching pages 0-3 (stop at first 404)
        for page in range(4):
            suffix = f"{artwork_id}-p{page}" if page > 0 else artwork_id
            for ext in ("jpg", "png"):
                proxy_url = f"https://pixiv.cat/{suffix}.{ext}"
                try:
                    async with session.get(
                        proxy_url,
                        timeout=aiohttp.ClientTimeout(total=20),
                        headers={"Referer": "https://www.pixiv.net/"},
                    ) as resp:
                        if resp.status == 200:
                            ct = resp.content_type or f"image/{ext}"
                            data = await resp.read()
                            results.append((data, ct))
                            break  # found this page, move to next
                except Exception:
                    continue
            else:
                if page > 0:
                    break  # no more pages

        if not results:
            return None, f"❌ Could not fetch artwork {artwork_id} from pixiv.cat. It may be private or deleted."

    return results, ""
