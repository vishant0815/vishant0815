#!/usr/bin/env python3
"""Generate the terminal-style SVG cards used in the profile README.

In GitHub Actions it reads contribution data from the GraphQL API.
Locally you can pass --days-csv (lines of "YYYY-MM-DD,count") to preview offline.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import io
import json
import os
import urllib.request
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "profile.json").read_text())
OUT = ROOT / "assets"

# ---------------------------------------------------------------- theme
BG = "#000000"
PANEL = "#0a0a0a"
BORDER = "#262626"
TEXT = "#ededed"
MUTED = "#8b8b8b"
GREEN = "#39d353"
BLUE = "#79c0ff"
INK = "#d4d4d4"          # portrait characters
LEVELS = ["#141414", "#0e4429", "#006d32", "#26a641", "#39d353"]
LEVEL_INDEX = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
               "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
FONT = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono','DejaVu Sans Mono',monospace"
W = 860
CW = 8.4                 # width of one prompt character at 14px

# Everything animates once when the card first loads. The final frame is the
# element's normal state, so renderers without animation still show it all.
BASE_CSS = """
text{font-family:%s}
.cur{animation:blink 1.1s step-end 1.4s infinite}
@keyframes blink{50%%{opacity:0}}
.fade{animation:fade .7s ease-out both}
@keyframes fade{from{opacity:0}to{opacity:1}}
.rise{animation:rise .7s cubic-bezier(.2,.8,.2,1) both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.pop{transform-box:fill-box;transform-origin:50%% 50%%;animation:pop .5s cubic-bezier(.2,.8,.2,1.3) both}
@keyframes pop{from{opacity:0;transform:scale(.2)}to{opacity:1;transform:none}}
.line{animation:line .25s ease-out both}
@keyframes line{from{opacity:0;transform:translateX(-6px)}to{opacity:1;transform:none}}
.grow{transform-box:fill-box;transform-origin:50%% 100%%;animation:grow .9s cubic-bezier(.2,.8,.2,1) both}
@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}
""" % FONT


# ---------------------------------------------------------------- data
def gql(token: str, query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise SystemExit(f"GraphQL error: {body['errors']}")
    return body["data"]


CAL_FIELDS = "contributionCalendar{ totalContributions weeks{ contributionDays{ date contributionCount contributionLevel } } }"


def fetch_github(login: str, token: str):
    """Return (weeks for the last-year graph, {date: count} for all time)."""
    q = "query($login:String!){ user(login:$login){ createdAt contributionsCollection{ %s } } }" % CAL_FIELDS
    user = gql(token, q, {"login": login})["user"]
    weeks = [
        [(dt.date.fromisoformat(d["date"]), d["contributionCount"], LEVEL_INDEX[d["contributionLevel"]])
         for d in w["contributionDays"]]
        for w in user["contributionsCollection"]["contributionCalendar"]["weeks"]
    ]
    all_days: dict[dt.date, int] = {}
    created = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    yq = ("query($login:String!,$from:DateTime!,$to:DateTime!){ user(login:$login){ "
          "contributionsCollection(from:$from,to:$to){ %s } } }" % CAL_FIELDS)
    for year in range(created.year, now.year + 1):
        start = max(created, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        end = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc))
        data = gql(token, yq, {"login": login, "from": start.isoformat(), "to": end.isoformat()})
        for w in data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                all_days[dt.date.fromisoformat(d["date"])] = d["contributionCount"]
    for w in weeks:  # the last-year graph is the source of truth for recent days
        for day, count, _ in w:
            all_days[day] = count
    return weeks, all_days


def load_csv(path: str, today: dt.date):
    """Offline preview: build the same structures from a CSV of daily counts."""
    counts: dict[dt.date, int] = defaultdict(int)
    for line in Path(path).read_text().split():
        d, c = line.split(",")
        counts[dt.date.fromisoformat(d)] += int(c)
    start = today - dt.timedelta(days=364)
    start -= dt.timedelta(days=(start.weekday() + 1) % 7)  # back to Sunday
    nonzero = sorted(c for c in counts.values() if c)
    q = [nonzero[int(len(nonzero) * f)] for f in (0.25, 0.5, 0.75)] if nonzero else [1, 1, 1]

    def level(c):
        if c == 0:
            return 0
        return 1 + sum(c > t for t in q)

    weeks, day = [], start
    while day <= today:
        week = []
        for _ in range(7):
            if day <= today:
                week.append((day, counts.get(day, 0), level(counts.get(day, 0))))
            day += dt.timedelta(days=1)
        weeks.append(week)
    return weeks, dict(counts)


# ---------------------------------------------------------------- stats
def fmt_day(d: dt.date) -> str:
    return f"{calendar.month_abbr[d.month]} {d.day}"


def plural(n: int) -> str:
    return "day" if n == 1 else "days"


def fmt_range(a: dt.date, b: dt.date) -> str:
    return fmt_day(a) if a == b else f"{fmt_day(a)} – {fmt_day(b)}"


def compute_stats(weeks, all_days):
    days = [(d, c) for w in weeks for d, c, _ in w]
    today = days[-1][0]
    total = sum(c for _, c in days)
    active = sum(1 for _, c in days if c)
    best_day, best = max(days, key=lambda x: (x[1], x[0]))

    # current streak: today may still be empty without breaking it
    cursor = today if all_days.get(today, 0) else today - dt.timedelta(days=1)
    cur_end, cur_len = cursor, 0
    while all_days.get(cursor, 0) > 0:
        cur_len += 1
        cursor -= dt.timedelta(days=1)
    cur_start = cursor + dt.timedelta(days=1)

    # longest streak, all time
    longest, l_start, l_end, run, run_start, prev = 0, None, None, 0, None, None
    for d in sorted(all_days):
        if all_days[d] > 0:
            if run and d - prev == dt.timedelta(days=1):
                run += 1
            else:
                run, run_start = 1, d
            if run >= longest:
                longest, l_start, l_end = run, run_start, d
        else:
            run = 0
        prev = d

    # contributions per month for the current month and the 12 before it
    months = []
    y, m = today.year, today.month
    for _ in range(13):
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months.reverse()
    per_month = defaultdict(int)
    for d, c in all_days.items():
        per_month[(d.year, d.month)] += c

    return {
        "total": total,
        "active": active,
        "span": len(days),
        "best": best,
        "best_day": best_day,
        "avg": total / active if active else 0.0,
        "current": cur_len,
        "current_range": fmt_range(cur_start, cur_end) if cur_len else "push today to start one",
        "longest": longest,
        "longest_range": fmt_range(l_start, l_end) if longest else "no streak yet",
        "months": [(calendar.month_abbr[m][0], per_month[(y, m)]) for y, m in months],
    }


# ---------------------------------------------------------------- svg helpers
def svg_open(h: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" role="img" aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
        f"<style>{BASE_CSS}</style>",
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{h - 1}" rx="12" fill="{BG}" stroke="{BORDER}"/>',
    ]


def prompt(command: str, y: int = 34) -> str:
    """Centered `user@github ~ $ command` line; the command types itself out."""
    user = f"{CONFIG['prompt_user']}@github"
    n = len(user) + 5 + len(command)            # "user ~ $ command"
    x0 = (W - (n + 1.5) * CW) / 2
    cmd_x = x0 + (len(user) + 5) * CW

    def seg(text, col, color):
        return (f'<text x="{x0 + col * CW:.1f}" y="{y}" font-size="14" font-weight="700" fill="{color}" '
                f'textLength="{len(text) * CW:.1f}" lengthAdjust="spacingAndGlyphs">{escape(text)}</text>')

    hold = 3                                    # beats to wait before typing starts
    steps = [0] * hold + [i * CW for i in range(len(command) + 1)]
    widths = ";".join(f"{s:.1f}" for s in steps)
    xs = ";".join(f"{cmd_x + s:.1f}" for s in steps)
    dur = len(steps) * 0.055
    full = len(command) * CW
    return "".join([
        f'<clipPath id="type"><rect x="{cmd_x:.1f}" y="{y - 16}" width="{full:.1f}" height="22">'
        f'<animate attributeName="width" values="{widths}" dur="{dur:.2f}s" calcMode="discrete" fill="freeze"/></rect></clipPath>',
        seg(user, 0, GREEN), seg("~", len(user) + 1, BLUE), seg("$", len(user) + 3, MUTED),
        f'<g clip-path="url(#type)">{seg(command, len(user) + 5, TEXT)}</g>',
        f'<rect class="cur" x="{cmd_x + full + 2:.1f}" y="{y - 12}" width="8" height="15" fill="{GREEN}">'
        f'<animate attributeName="x" values="{xs}" dur="{dur:.2f}s" calcMode="discrete" fill="freeze"/></rect>',
    ])


# ---------------------------------------------------------------- cards
def contributions_svg(weeks, stats) -> str:
    cell, step = 12, 15
    x0, y0 = 51, 78
    h = 226
    out = svg_open(h, f"{stats['total']:,} contributions in the last year")
    out.append(prompt("./contributions.sh"))

    out.append('<g class="fade" style="animation-delay:.5s">')
    last_label_week = -9
    for i, w in enumerate(weeks):
        first = w[0][0]
        if (i == 0 or first.month != weeks[i - 1][0][0].month) and i - last_label_week >= 3 and i < len(weeks) - 1:
            out.append(f'<text x="{x0 + i * step}" y="{y0 - 10}" font-size="10" fill="{MUTED}">{calendar.month_abbr[first.month]}</text>')
            last_label_week = i
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out.append(f'<text x="17" y="{y0 + row * step + 10}" font-size="10" fill="{MUTED}">{name}</text>')
    out.append("</g>")

    # cells pop in as a diagonal wave from the left
    for i, w in enumerate(weeks):
        for d, _, lvl in w:
            row = (d.weekday() + 1) % 7  # Sunday = 0
            delay = 600 + i * 22 + row * 35
            out.append(f'<rect class="pop" style="animation-delay:{delay}ms" x="{x0 + i * step}" y="{y0 + row * step}" '
                       f'width="{cell}" height="{cell}" rx="2.5" fill="{LEVELS[lvl]}" '
                       f'stroke="#ffffff" stroke-opacity="{0.05 if lvl == 0 else 0}"/>')

    fy = y0 + 7 * step + 22
    out.append(f'<g class="rise" style="animation-delay:1.6s">')
    out.append(f'<text x="{x0}" y="{fy}" font-size="12" fill="{TEXT}" xml:space="preserve"><tspan font-weight="700">{stats["total"]:,}</tspan>'
               f'<tspan fill="{MUTED}"> contributions in the last year</tspan></text>')
    lx = x0 + 53 * step - 3 - (5 * 14 + 70)
    out.append(f'<text x="{lx}" y="{fy}" font-size="10" fill="{MUTED}">Less</text>')
    for i, c in enumerate(LEVELS):
        out.append(f'<rect x="{lx + 30 + i * 14}" y="{fy - 10}" width="11" height="11" rx="2" fill="{c}"/>')
    out.append(f'<text x="{lx + 30 + 5 * 14 + 4}" y="{fy}" font-size="10" fill="{MUTED}">More</text>')
    out.append("</g></svg>")
    return "\n".join(out)


def load_portrait() -> list[str]:
    """assets/portrait.txt (made by scripts/make_portrait.py) wins; otherwise
    convert the GitHub avatar, and as a last resort draw the initials."""
    txt = ROOT / "assets" / "portrait.txt"
    if txt.exists():
        return txt.read_text().rstrip("\n").split("\n")
    return ascii_from_image(cols=96, rows=40, aspect=380 / 314)


def ascii_from_image(cols: int, rows: int, aspect: float) -> list[str]:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

    img = None
    if os.environ.get("GH_USER"):
        try:
            url = f"https://github.com/{os.environ['GH_USER']}.png?size=460"
            with urllib.request.urlopen(url, timeout=20) as r:
                img = Image.open(io.BytesIO(r.read()))
        except Exception as e:  # network hiccup: fall back to initials
            print(f"avatar download failed: {e}")
    if img is None:
        img = Image.new("L", (600, 500), 0)
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=400)
        text = CONFIG["initials"]
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((600 - box[2] - box[0]) / 2, (500 - box[3] - box[1]) / 2), text, fill=255, font=font)
        img = img.filter(ImageFilter.GaussianBlur(3))

    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        img = Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 255)), img)
    img = img.convert("L")
    w, h = img.size
    if w / h > aspect:
        nw = int(h * aspect)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        img = img.crop((0, 0, w, int(w / aspect)))

    img = ImageOps.autocontrast(img, cutoff=1)
    edges = img.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
    ImageDraw.Draw(edges).rectangle((0, 0, edges.width - 1, edges.height - 1), outline=0, width=6)
    small = img.resize((cols, rows), Image.LANCZOS)
    small_edges = edges.resize((cols, rows), Image.LANCZOS)
    border = sorted([small.getpixel((x, y)) for x in range(cols) for y in (0, rows - 1)]
                    + [small.getpixel((x, y)) for y in range(rows) for x in (0, cols - 1)])
    bg = border[len(border) // 2]
    reach = max(bg, 255 - bg) or 1

    ramp = " .,:;-~=+*cox%#&@"
    lines = []
    for y in range(rows):
        line = []
        for x in range(cols):
            tone = abs(small.getpixel((x, y)) - bg) / reach
            edge = small_edges.getpixel((x, y)) / 255
            dx, dy = (x / cols - 0.5) * 2, (y / rows - 0.45) * 2
            fade = max(0.0, 1 - max(0.0, (dx * dx + dy * dy) ** 0.5 - 0.75) * 2.2)
            line.append(ramp[int(min(1.0, (tone * 0.55 + edge * 0.9) * fade) * (len(ramp) - 1))])
        lines.append("".join(line))
    return lines


def whoami_svg(stats) -> str:
    h = 440
    out = svg_open(h, "whoami: portrait and contribution stats")
    out.append(prompt("whoami"))

    # left: terminal window that "cats" the ASCII portrait line by line
    px, py, pw, ph = 17, 58, 404, 364
    out.append(f'<g class="fade" style="animation-delay:.35s">')
    out.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="8" fill="{PANEL}" stroke="{BORDER}"/>')
    out.append(f'<path d="M{px} {py + 26}H{px + pw}" stroke="{BORDER}"/>')
    for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        out.append(f'<circle cx="{px + 16 + i * 15}" cy="{py + 13}" r="4.5" fill="{c}"/>')
    out.append(f'<text x="{px + pw / 2}" y="{py + 17}" font-size="10" fill="{MUTED}" text-anchor="middle">~/portrait.txt — cat</text>')
    out.append("</g>")

    lines = load_portrait()
    cols, rows = max(len(l) for l in lines), len(lines)
    ax, ay, aw, ah = px + 12, py + 38, pw - 24, ph - 50
    line_h = ah / rows
    start, per_line = 700, 32
    out.append(f'<g fill="{INK}" font-size="{line_h * 1.02:.2f}">')
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        lead = len(line) - len(line.lstrip())
        body = line.strip()
        out.append(f'<text class="line" style="animation-delay:{start + i * per_line}ms" '
                   f'x="{ax + aw * lead / cols:.2f}" y="{ay + (i + 0.8) * line_h:.2f}" xml:space="preserve" '
                   f'textLength="{aw * len(body) / cols:.2f}" lengthAdjust="spacingAndGlyphs">{escape(body)}</text>')
    out.append("</g>")
    # scan line that sweeps down while the portrait prints, then disappears
    sweep = (rows * per_line) / 1000
    out.append(f'<rect x="{px + 1}" y="{ay}" width="{pw - 2}" height="2" fill="{GREEN}" opacity="0">'
               f'<animate attributeName="y" values="{ay};{ay};{ay + ah}" keyTimes="0;{start / 1000 / (start / 1000 + sweep):.3f};1" '
               f'dur="{start / 1000 + sweep:.2f}s" fill="freeze"/>'
               f'<animate attributeName="opacity" values="0;0;.7;.7;0" keyTimes="0;{start / 1000 / (start / 1000 + sweep):.3f};'
               f'{(start / 1000 + 0.05) / (start / 1000 + sweep):.3f};.97;1" dur="{start / 1000 + sweep:.2f}s" fill="freeze"/></rect>')

    # right: stat tiles
    gx, gy, tw, th, gap = px + pw + 14, py, 200, 68, 8
    tiles = [
        ("current_streak", f"{stats['current']}", plural(stats["current"]), stats["current_range"]),
        ("longest_streak", f"{stats['longest']}", plural(stats["longest"]), stats["longest_range"]),
        ("contributions", f"{stats['total']:,}", "", "in the last year"),
        ("active_days", f"{stats['active']}", f"/ {stats['span']}", f"{stats['active'] / stats['span'] * 100:.0f}% of the year"),
        ("best_day", f"{stats['best']}", "", fmt_day(stats["best_day"]) if stats["best"] else "—"),
        ("avg / active_day", f"{stats['avg']:.1f}", "", "contributions"),
    ]
    for i, (label, value, unit, sub) in enumerate(tiles):
        x = gx + (i % 2) * (tw + gap)
        y = gy + (i // 2) * (th + gap)
        out.append(f'<g class="rise" style="animation-delay:{550 + i * 110}ms">')
        out.append(f'<rect x="{x}" y="{y}" width="{tw}" height="{th}" rx="8" fill="{PANEL}" stroke="{BORDER}"/>')
        out.append(f'<text x="{x + 12}" y="{y + 19}" font-size="10" fill="{MUTED}">$ {escape(label)}</text>')
        unit_span = f'<tspan font-size="11" font-weight="400" fill="{MUTED}"> {escape(unit)}</tspan>' if unit else ""
        out.append(f'<text x="{x + 12}" y="{y + 44}" font-size="22" font-weight="700" fill="{GREEN}" xml:space="preserve">{escape(value)}{unit_span}</text>')
        out.append(f'<text x="{x + 12}" y="{y + 59}" font-size="9.5" fill="{MUTED}">{escape(sub)}</text>')
        out.append("</g>")

    # right bottom: contributions per month
    cx, cy = gx, gy + 3 * (th + gap)
    cw, ch = 2 * tw + gap, py + ph - cy
    out.append(f'<g class="rise" style="animation-delay:1.2s">')
    out.append(f'<rect x="{cx}" y="{cy}" width="{cw}" height="{ch}" rx="8" fill="{PANEL}" stroke="{BORDER}"/>')
    out.append(f'<text x="{cx + 12}" y="{cy + 19}" font-size="10" fill="{MUTED}">$ contributions / month</text>')
    out.append("</g>")
    months = stats["months"]
    peak = max((c for _, c in months), default=0) or 1
    peak_i = max(range(len(months)), key=lambda i: (months[i][1], i))
    top, bottom = cy + 40, cy + ch - 22
    slot = (cw - 24) / len(months)
    bw = slot * 0.62
    for i, (letter, count) in enumerate(months):
        bx = cx + 12 + i * slot + (slot - bw) / 2
        bh = max(2.0, (bottom - top) * count / peak)
        hot = i == peak_i and count > 0
        out.append(f'<rect class="grow" style="animation-delay:{1400 + i * 70}ms" x="{bx:.1f}" y="{bottom - bh:.1f}" '
                   f'width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="{GREEN if hot else "#26a641"}" '
                   f'fill-opacity="{1 if hot else (0.85 if count else 0.35)}"/>')
        if hot:
            out.append(f'<text class="fade" style="animation-delay:{1400 + len(months) * 70 + 300}ms" x="{bx + bw / 2:.1f}" '
                       f'y="{bottom - bh - 5:.1f}" font-size="10" font-weight="700" fill="{GREEN}" text-anchor="middle">{count:,}</text>')
        out.append(f'<text class="fade" style="animation-delay:1.3s" x="{bx + bw / 2:.1f}" y="{bottom + 14}" font-size="9" '
                   f'fill="{MUTED}" text-anchor="middle">{letter}</text>')
    out.append(f'<path class="fade" style="animation-delay:1.3s" d="M{cx + 12} {bottom + 0.5}H{cx + cw - 12}" stroke="{BORDER}"/>')
    out.append("</svg>")
    return "\n".join(out)


def links_svg() -> str:
    h = 92
    out = svg_open(h, "./links.sh")
    out.append(prompt("./links.sh"))
    out.append(f'<text class="rise" style="animation-delay:.8s" x="{W / 2}" y="62" font-size="13" fill="{TEXT}" text-anchor="middle">{escape(CONFIG["tagline"])}</text>')
    out.append(f'<text class="rise" style="animation-delay:1s" x="{W / 2}" y="80" font-size="11" fill="{MUTED}" text-anchor="middle">{escape(CONFIG["stack"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def badge_svg(label: str, value: str, color: str, delay: int) -> str:
    cw = 7.2
    lw, vw = len(label) * cw + 20, len(value) * cw + 20
    w, h = lw + vw, 28
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h}" viewBox="0 0 {w:.0f} {h}" role="img" aria-label="{escape(label)}: {escape(value)}">'
        f"<style>text{{font-family:{FONT}}}g{{animation:fade .6s ease-out {delay}ms both}}"
        "@keyframes fade{from{opacity:0}to{opacity:1}}</style><g>"
        f'<rect x="0.5" y="0.5" width="{w - 1:.0f}" height="{h - 1}" rx="6" fill="{BG}" stroke="{BORDER}"/>'
        f'<path d="M6.5 .5H{lw:.0f}V{h - 0.5}H6.5a6 6 0 0 1-6-6V6.5a6 6 0 0 1 6-6z" fill="{color}"/>'
        f'<text x="{lw / 2:.1f}" y="18" font-size="12" font-weight="700" fill="#ffffff" text-anchor="middle" '
        f'textLength="{len(label) * cw:.1f}" lengthAdjust="spacingAndGlyphs">{escape(label)}</text>'
        f'<text x="{lw + vw / 2:.1f}" y="18" font-size="12" fill="{TEXT}" text-anchor="middle" '
        f'textLength="{len(value) * cw:.1f}" lengthAdjust="spacingAndGlyphs">{escape(value)}</text>'
        "</g></svg>"
    )


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-csv", help="offline preview: CSV of date,count")
    ap.add_argument("--today", help="offline preview: override today's date (YYYY-MM-DD)")
    args = ap.parse_args()

    if args.days_csv:
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
        weeks, all_days = load_csv(args.days_csv, today)
    else:
        token = os.environ.get("GH_TOKEN")
        login = os.environ.get("GH_USER") or CONFIG["github_user"]
        if not token:
            raise SystemExit("GH_TOKEN is not set")
        weeks, all_days = fetch_github(login, token)

    stats = compute_stats(weeks, all_days)
    OUT.mkdir(exist_ok=True)
    (OUT / "contributions.svg").write_text(contributions_svg(weeks, stats))
    (OUT / "whoami.svg").write_text(whoami_svg(stats))
    (OUT / "links.svg").write_text(links_svg())
    for i, b in enumerate(CONFIG["badges"]):
        (OUT / f"badge-{b['id']}.svg").write_text(badge_svg(b["label"], b["value"], b["color"], 1200 + i * 150))
    print(json.dumps({k: v for k, v in stats.items() if k != "months"}, default=str, indent=2))


if __name__ == "__main__":
    main()
