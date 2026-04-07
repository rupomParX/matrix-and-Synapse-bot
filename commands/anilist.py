"""
commands/anilist.py — AniList commands for RoseBot
!anime !manga !character !airing !top !studio
"""

import datetime
import re
import aiohttp

ANILIST_API = "https://graphql.anilist.co"


async def anilist_query(query: str, variables: dict) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            ANILIST_API,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            return await r.json()


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'")
            .replace("~!", "").replace("!~", "").strip()
    )


def trunc(text: str, n: int = 300) -> str:
    if not text:
        return "N/A"
    text = clean_html(text)
    return text[:n] + "…" if len(text) > n else text


def fmt_score(s) -> str:
    return f"{s}/100" if s else "N/A"


def fuzzy(d: dict) -> str:
    if not d:
        return "N/A"
    parts = [str(d["year"])] if d.get("year") else []
    if d.get("month"):
        parts.append(str(d["month"]).zfill(2))
    if d.get("day"):
        parts.append(str(d["day"]).zfill(2))
    return "-".join(parts) or "N/A"


# ─── ANIME ────────────────────────────────────────────────────────────────────

ANIME_Q = """
query ($search: String, $id: Int) {
  Media(search: $search, id: $id, type: ANIME) {
    id title { romaji english native }
    description episodes duration status genres
    averageScore popularity format season seasonYear
    startDate { year month day } endDate { year month day }
    studios(isMain: true) { nodes { name } }
    coverImage { extraLarge large }
    siteUrl isAdult
    nextAiringEpisode { episode timeUntilAiring }
  }
}"""


async def cmd_anime(args: str) -> tuple[str, str | None]:
    """Returns (text, image_url|None)"""
    if not args:
        return "Usage: !anime <title or AniList ID>", None
    variables = {"id": int(args)} if args.isdigit() else {"search": args}
    data = await anilist_query(ANIME_Q, variables)
    if "errors" in data or not data.get("data", {}).get("Media"):
        return f'❌ No anime found for "{args}".', None

    m = data["data"]["Media"]
    t = m["title"]
    name   = t.get("english") or t.get("romaji") or t.get("native") or "Unknown"
    romaji = t.get("romaji", "")
    native = t.get("native", "")
    studios = ", ".join(s["name"] for s in m["studios"]["nodes"]) or "N/A"
    genres  = ", ".join(m.get("genres", [])) or "N/A"
    status  = (m.get("status") or "N/A").replace("_", " ").title()
    fmt     = (m.get("format") or "N/A").replace("_", " ").title()
    season  = f'{(m.get("season") or "").title()} {m.get("seasonYear", "")}'.strip() or "N/A"
    eps     = str(m.get("episodes") or "?")
    dur     = f'{m["duration"]} min' if m.get("duration") else "N/A"
    score   = fmt_score(m.get("averageScore"))
    pop     = f'{m.get("popularity", 0):,}'
    start   = fuzzy(m.get("startDate"))
    end     = fuzzy(m.get("endDate"))
    desc    = trunc(m.get("description", ""), 280)
    url     = m.get("siteUrl", "")
    adult   = " ⚠️ 18+" if m.get("isAdult") else ""

    airing = ""
    nae = m.get("nextAiringEpisode")
    if nae:
        d = nae["timeUntilAiring"] // 86400
        h = (nae["timeUntilAiring"] % 86400) // 3600
        airing = f"\n⏳ Next: Ep {nae['episode']} in {d}d {h}h"

    text = (
        f"🎬 {name}{adult}\n"
        f"📛 {romaji}" + (f" / {native}" if native else "") + "\n"
        f"━━━━━━━━━━━━━━\n"
        f"📺 Format: {fmt}\n"
        f"📊 Status: {status}\n"
        f"🗓 Season: {season}\n"
        f"📅 Aired: {start} → {end}\n"
        f"🎞 Episodes: {eps} ({dur}/ep)\n"
        f"⭐ Score: {score}  👥 Popularity: {pop}\n"
        f"🏢 Studio: {studios}\n"
        f"🏷 Genres: {genres}"
        f"{airing}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{desc}\n"
        f"🔗 {url}"
    )
    cover = m["coverImage"].get("extraLarge") or m["coverImage"].get("large")
    return text, cover


# ─── MANGA ────────────────────────────────────────────────────────────────────

MANGA_Q = """
query ($search: String, $id: Int) {
  Media(search: $search, id: $id, type: MANGA) {
    id title { romaji english native }
    description chapters volumes status genres
    averageScore popularity format
    startDate { year month day } endDate { year month day }
    staff(perPage: 3) { nodes { name { full } } }
    coverImage { extraLarge large }
    siteUrl isAdult
  }
}"""


async def cmd_manga(args: str) -> tuple[str, str | None]:
    if not args:
        return "Usage: !manga <title or AniList ID>", None
    variables = {"id": int(args)} if args.isdigit() else {"search": args}
    data = await anilist_query(MANGA_Q, variables)
    if "errors" in data or not data.get("data", {}).get("Media"):
        return f'❌ No manga found for "{args}".', None

    m = data["data"]["Media"]
    t = m["title"]
    name   = t.get("english") or t.get("romaji") or t.get("native") or "Unknown"
    romaji = t.get("romaji", "")
    native = t.get("native", "")
    genres   = ", ".join(m.get("genres", [])) or "N/A"
    status   = (m.get("status") or "N/A").replace("_", " ").title()
    fmt      = (m.get("format") or "N/A").replace("_", " ").title()
    chapters = str(m.get("chapters") or "?")
    volumes  = str(m.get("volumes") or "?")
    score    = fmt_score(m.get("averageScore"))
    pop      = f'{m.get("popularity", 0):,}'
    start    = fuzzy(m.get("startDate"))
    end      = fuzzy(m.get("endDate"))
    staff    = ", ".join(s["name"]["full"] for s in m["staff"]["nodes"]) or "N/A"
    desc     = trunc(m.get("description", ""), 280)
    url      = m.get("siteUrl", "")
    adult    = " ⚠️ 18+" if m.get("isAdult") else ""

    text = (
        f"📚 {name}{adult}\n"
        f"📛 {romaji}" + (f" / {native}" if native else "") + "\n"
        f"━━━━━━━━━━━━━━\n"
        f"📖 Format: {fmt}\n"
        f"📊 Status: {status}\n"
        f"📅 Published: {start} → {end}\n"
        f"📄 Chapters: {chapters}  📦 Volumes: {volumes}\n"
        f"⭐ Score: {score}  👥 Popularity: {pop}\n"
        f"✍️ Staff: {staff}\n"
        f"🏷 Genres: {genres}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{desc}\n"
        f"🔗 {url}"
    )
    cover = m["coverImage"].get("extraLarge") or m["coverImage"].get("large")
    return text, cover


# ─── CHARACTER ────────────────────────────────────────────────────────────────

CHAR_Q = """
query ($search: String, $id: Int) {
  Character(search: $search, id: $id) {
    id name { full native alternative }
    description gender age
    dateOfBirth { month day }
    favourites image { large } siteUrl
    media(perPage: 5, sort: POPULARITY_DESC) {
      nodes { title { romaji english } type }
    }
  }
}"""


async def cmd_character(args: str) -> tuple[str, str | None]:
    if not args:
        return "Usage: !character <name or AniList ID>", None
    variables = {"id": int(args)} if args.isdigit() else {"search": args}
    data = await anilist_query(CHAR_Q, variables)
    if "errors" in data or not data.get("data", {}).get("Character"):
        return f'❌ No character found for "{args}".', None

    c = data["data"]["Character"]
    full   = c["name"].get("full") or "Unknown"
    native = c["name"].get("native") or ""
    alts   = ", ".join(c["name"].get("alternative") or [])
    gender = c.get("gender") or "N/A"
    age    = c.get("age") or "N/A"
    dob    = c.get("dateOfBirth") or {}
    bday   = f'{dob.get("month","?")}/{dob.get("day","?")}' if dob.get("month") else "N/A"
    favs   = f'{c.get("favourites", 0):,}'
    desc   = trunc(c.get("description", ""), 280)
    url    = c.get("siteUrl", "")

    appearances = []
    for node in (c.get("media") or {}).get("nodes", []):
        tt = node["title"]
        title = tt.get("english") or tt.get("romaji") or "?"
        appearances.append(f"  • {title} [{node.get('type','').title()}]")
    appears = "\n".join(appearances) or "  N/A"

    text = (
        f"👤 {full}" + (f" ({native})" if native else "") + "\n"
        + (f"🔀 AKA: {alts}\n" if alts else "")
        + f"━━━━━━━━━━━━━━\n"
        f"⚥ Gender: {gender}\n"
        f"🎂 Birthday: {bday}  🎯 Age: {age}\n"
        f"❤️ Favourites: {favs}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{desc}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📺 Appears in:\n{appears}\n"
        f"🔗 {url}"
    )
    img = (c.get("image") or {}).get("large")
    return text, img


# ─── AIRING ───────────────────────────────────────────────────────────────────

AIRING_Q = """
query ($search: String, $id: Int) {
  Media(search: $search, id: $id, type: ANIME, status: RELEASING) {
    id title { romaji english }
    status episodes coverImage { extraLarge large } siteUrl
    nextAiringEpisode { episode airingAt timeUntilAiring }
    airingSchedule(notYetAired: true, perPage: 3) {
      nodes { episode airingAt }
    }
  }
}"""

AIRING_ANY_Q = """
query ($search: String, $id: Int) {
  Media(search: $search, id: $id, type: ANIME) {
    id title { romaji english }
    status episodes coverImage { extraLarge large } siteUrl
    nextAiringEpisode { episode airingAt timeUntilAiring }
    airingSchedule(notYetAired: true, perPage: 3) {
      nodes { episode airingAt }
    }
  }
}"""


async def cmd_airing(args: str) -> tuple[str, str | None]:
    if not args:
        return "Usage: !airing <anime title or ID>", None
    variables = {"id": int(args)} if args.isdigit() else {"search": args}
    data = await anilist_query(AIRING_Q, variables)
    media = (data.get("data") or {}).get("Media")
    if not media:
        data = await anilist_query(AIRING_ANY_Q, variables)
        media = (data.get("data") or {}).get("Media")
    if not media:
        return f'❌ No airing info found for "{args}".', None

    tt = media["title"]
    name   = tt.get("english") or tt.get("romaji") or "Unknown"
    status = (media.get("status") or "N/A").replace("_", " ").title()
    total  = media.get("episodes") or "?"
    url    = media.get("siteUrl", "")

    nae = media.get("nextAiringEpisode")
    if nae:
        s = nae["timeUntilAiring"]
        d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
        next_info = f"⏭ Next: Episode {nae['episode']} in {d}d {h}h {m}m"
    else:
        next_info = "⏭ No upcoming episode scheduled."

    nodes = (media.get("airingSchedule") or {}).get("nodes", [])
    sched = "\n".join(
        f"  • Ep {n['episode']} — {datetime.datetime.utcfromtimestamp(n['airingAt']).strftime('%Y-%m-%d %H:%M UTC')}"
        for n in nodes
    ) or "  N/A"

    text = (
        f"📡 {name}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📊 Status: {status}\n"
        f"🎞 Total Episodes: {total}\n"
        f"{next_info}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📅 Upcoming:\n{sched}\n"
        f"🔗 {url}"
    )
    cover = (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large")
    return text, cover


# ─── TOP ──────────────────────────────────────────────────────────────────────

TOP_Q = """
query ($genre: String, $page: Int) {
  Page(page: $page, perPage: 10) {
    media(type: ANIME, sort: SCORE_DESC, genre: $genre, isAdult: false) {
      title { romaji english } averageScore popularity siteUrl
    }
  }
}"""


async def cmd_top(args: str) -> tuple[str, None]:
    genre = args.strip() or None
    data  = await anilist_query(TOP_Q, {"page": 1, "genre": genre})
    items = ((data.get("data") or {}).get("Page") or {}).get("media", [])
    if not items:
        return "❌ No results found.", None
    header = f"🏆 Top Anime" + (f" — {genre}" if genre else "") + "\n━━━━━━━━━━━━━━\n"
    lines = []
    for i, m in enumerate(items, 1):
        t    = m["title"]
        name = t.get("english") or t.get("romaji") or "?"
        sc   = m.get("averageScore") or "N/A"
        lines.append(f"{i}. {name} ⭐{sc}")
    return header + "\n".join(lines), None


# ─── STUDIO ───────────────────────────────────────────────────────────────────

STUDIO_Q = """
query ($search: String) {
  Studio(search: $search) {
    id name siteUrl isAnimationStudio favourites
    media(perPage: 10, sort: POPULARITY_DESC, isMain: true) {
      nodes { title { romaji english } averageScore seasonYear type siteUrl }
    }
  }
}"""


async def cmd_studio(args: str) -> tuple[str, None]:
    if not args:
        return "Usage: !studio <studio name>", None
    data   = await anilist_query(STUDIO_Q, {"search": args})
    studio = (data.get("data") or {}).get("Studio")
    if not studio:
        return f'❌ No studio found for "{args}".', None

    sname  = studio.get("name", "Unknown")
    url    = studio.get("siteUrl", "")
    is_an  = "Yes" if studio.get("isAnimationStudio") else "No"
    favs   = f'{studio.get("favourites", 0):,}'
    works  = []
    for node in (studio.get("media") or {}).get("nodes", []):
        tt    = node["title"]
        title = tt.get("english") or tt.get("romaji") or "?"
        sc    = node.get("averageScore") or "N/A"
        yr    = node.get("seasonYear") or "?"
        works.append(f"  • {title} ({yr}) ⭐{sc}")

    text = (
        f"🏢 {sname}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎬 Animation Studio: {is_an}\n"
        f"❤️ Favourites: {favs}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📺 Notable Works:\n" + ("\n".join(works) or "  N/A") + f"\n"
        f"🔗 {url}"
    )
    return text, None
