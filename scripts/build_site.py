"""
Builds the website from the articles in content/articles/ into the public/ folder.

Run:  python scripts/build_site.py
Then open public/index.html, or preview with:  python -m http.server -d public
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
import re
import shutil
import zlib
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

import seo
from common import ARTICLES_DIR, PHOTOS_DIR, ROOT, load_config, published_at, read_article

OUT = ROOT / "public"
CARDS = ROOT / "build" / "cards"

# ─── Icons (24×24, drawn with strokes) ───────────────────────────────────────
ICONS = {
    "health": '<path d="M12 20.5s-7.5-4.6-7.5-10.4A4.1 4.1 0 0 1 12 7.6a4.1 4.1 0 0 1 7.5 2.5c0 5.8-7.5 10.4-7.5 10.4z"/><path d="M7.5 12.5h2.6l1.2-2.4 1.8 4.6 1.3-2.2h2.1"/>',
    "environment": '<path d="M4.5 19.5c0-8.3 6.2-15 15-15 0 8.8-6.7 15-15 15z"/><path d="M4.5 19.5 13 11"/>',
    "science": '<circle cx="12" cy="12" r="1.6"/><ellipse cx="12" cy="12" rx="9" ry="3.6"/><ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(120 12 12)"/>',
    "people": '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.6 2.7-6 6-6s6 2.4 6 6"/><circle cx="17" cy="9" r="2.6"/><path d="M15.6 14.3c3 .1 5.4 2.3 5.4 5.7"/>',
    "kindness": '<path d="M15 10.2s-3.4-2-3.4-4.5a1.8 1.8 0 0 1 3.4-.9 1.8 1.8 0 0 1 3.4.9c0 2.5-3.4 4.5-3.4 4.5z"/><path d="M3 13.5h2.5c1.6 0 2.6.9 4 .9H13a1.6 1.6 0 0 1 0 3.2h-3.4"/><path d="M8.8 17.6h4.8l5.6-3.3a1.6 1.6 0 0 1 1.9 2.5L15 20.5H3"/>',
    "animals": '<ellipse cx="6.2" cy="10.4" rx="1.8" ry="2.3"/><ellipse cx="10" cy="6.6" rx="1.8" ry="2.4"/><ellipse cx="14.4" cy="6.6" rx="1.8" ry="2.4"/><ellipse cx="18" cy="10.4" rx="1.8" ry="2.3"/><path d="M12.2 11.8c-2.6 0-5.2 3.4-5.2 5.6 0 1.6 1.2 2.6 2.8 2.6 1 0 1.6-.5 2.4-.5s1.4.5 2.4.5c1.6 0 2.8-1 2.8-2.6 0-2.2-2.6-5.6-5.2-5.6z"/>',
    "history": '<path d="M3.5 9 12 4l8.5 5z"/><path d="M4.5 9h15M4 20.5h16M5.5 18h13"/><path d="M7.5 11.5v4.5M12 11.5v4.5M16.5 11.5v4.5"/>',
    "technology": '<path d="M9.2 17.5h5.6M10.2 20.5h3.6"/><path d="M12 3.5a6 6 0 0 0-3.6 10.8c.7.6 1 1.3 1 2.2h5.2c0-.9.3-1.6 1-2.2A6 6 0 0 0 12 3.5z"/>',
}


def icon_svg(name: str, size: int = 18, cls: str = "icon") -> Markup:
    body = ICONS.get(name, ICONS["kindness"])
    return Markup(
        f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>'
    )


def cover_svg(colour: str, icon: str, seed: int, w: int = 1200, h: int = 630, label: str = "") -> Markup:
    """Each story gets its own sunrise, drawn in its section's colour with the section's symbol in the sun."""
    rnd = random.Random(seed)
    r = rnd.uniform(0.30, 0.40) * min(w, h * 1.6) if w > h else rnd.uniform(0.34, 0.42) * w
    cx = rnd.uniform(0.32, 0.68) * w
    cy = h - r * rnd.uniform(0.45, 0.62)
    rays = rnd.choice([14, 16, 18, 20, 24])
    turn = rnd.uniform(0, 360 / rays)
    far = math.hypot(w, h) * 1.2
    parts = []
    for i in range(0, rays, 2):
        a1 = math.radians(turn + i * 360 / rays)
        a2 = math.radians(turn + (i + 1) * 360 / rays)
        parts.append(
            f'M{cx:.0f},{cy:.0f} L{cx + far * math.cos(a1):.0f},{cy + far * math.sin(a1):.0f} '
            f'L{cx + far * math.cos(a2):.0f},{cy + far * math.sin(a2):.0f}Z'
        )
    rings = "".join(
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r * k:.0f}" fill="none" stroke="#fff" stroke-opacity="{o}" stroke-width="{sw}"/>'
        for k, o, sw in ((1.28, 0.30, 3), (1.6, 0.18, 2), (1.98, 0.10, 2))
    )
    isz = r * 0.62
    ix, iy = cx - isz / 2, cy - r * 0.62 - isz / 2 + r * 0.06
    icon_g = (
        f'<g transform="translate({ix:.1f} {iy:.1f}) scale({isz / 24:.3f})" fill="none" stroke="{colour}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{ICONS.get(icon, "")}</g>'
    )
    aria = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true"'
    return Markup(
        f'<svg class="cover-art" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice" {aria} xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="{w}" height="{h}" fill="{colour}"/>'
        f'<path d="{" ".join(parts)}" fill="#fff" fill-opacity="0.07"/>'
        f'{rings}<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="#FFE9A8"/>{icon_g}</svg>'
    )


def photo_html(a: dict, small: bool = False, eager: bool = False) -> Markup:
    """The story's real photo if it has one, otherwise its sunrise illustration."""
    p = a.get("photo")
    if not p:
        w, h = (600, 600) if small else (1200, 630)
        return cover_svg(a["cat"]["colour"], a["cat"]["icon"], a["seed"], w, h, label="" if small else a["cat"]["name"] + " illustration")
    src = p["thumb"] if small else p["src"]
    alt = "" if small else escape(p.get("alt", ""), {'"': "&quot;"})
    load = 'fetchpriority="high"' if eager else 'loading="lazy"'
    return Markup(f'<img class="photo" src="{src}" alt="{alt}" width="{p["width"]}" height="{p["height"]}" {load} decoding="async">')


def card_photo(a: dict) -> str:
    """Photo for the Instagram/link-preview cards, only if its licence lets us crop it."""
    p = a.get("photo")
    return p["file_uri"] if p and p.get("ig_ok") else ""


# ─── Helpers ─────────────────────────────────────────────────────────────────

def nice_date(d: dt.datetime) -> str:
    return f"{d.day} {d:%B %Y}"


def parse_date(v) -> dt.datetime:
    if isinstance(v, dt.datetime):
        d = v
    elif isinstance(v, dt.date):
        d = dt.datetime(v.year, v.month, v.day)
    else:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def load_articles(cats: dict) -> list[dict]:
    md = markdown.Markdown(extensions=["smarty"])
    items = []
    for path in sorted(ARTICLES_DIR.glob("*.md")):
        a = read_article(path)
        if not a or not a.get("title"):
            print(f"  ! Skipping {path.name}: missing title or front matter")
            continue
        a["date"] = published_at(path, a)
        if a.get("category") not in cats:
            a["category"] = next(iter(cats))
        a["slug"] = a.get("slug") or path.stem[11:]
        a["cat"] = cats[a["category"]]
        a["path"] = f"/stories/{a['slug']}/"
        a["date_nice"] = nice_date(a["date"])
        a["dek"] = a.get("dek") or ""
        words = len(re.findall(r"\w+", a["body"]))
        a["words"] = words
        a["minutes"] = max(1, round(words / 230))
        a["key_points"] = [str(p) for p in (a.get("key_points") or [])]
        why = re.split(r"^##\s*Why it matters\s*$", a["body"], flags=re.M | re.I)
        a["why"] = ""
        if len(why) > 1:
            text = why[1].split("\n## ")[0].strip()
            text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
            a["why"] = re.sub(r"[*_`>#]", "", text).strip()
        a["tags"] = [str(t) for t in (a.get("tags") or [])]
        if a.get("updated"):
            try:
                a["updated_dt"] = parse_date(a["updated"])
            except ValueError:
                pass
        md.reset()
        a["html"] = Markup(md.convert(a["body"]))
        a["seed"] = a.get("cover_seed") or zlib.crc32(a["slug"].encode()) % 10**6
        a["photo"] = None
        img = a.get("image") or {}
        if isinstance(img, dict) and img.get("file") and (PHOTOS_DIR / img["file"]).exists():
            caption = str(img.get("caption") or "")
            a["photo"] = {
                **img, "local": PHOTOS_DIR / img["file"], "file_uri": (PHOTOS_DIR / img["file"]).as_uri(),
                "src": f"/images/{a['slug']}.jpg", "thumb": f"/images/{a['slug']}-sm.jpg",
                "width": int(img.get("width") or 1600), "height": int(img.get("height") or 1067),
                "alt": re.sub(r"^Illustrative photo:\s*|\s*\(file photo\)$", "", caption),
                "ig_ok": bool(img.get("instagram_ok")),
            }
        items.append(a)
    items.sort(key=lambda x: x["date"], reverse=True)
    return items


def title_size(title: str, sizes: tuple) -> int:
    n = len(title)
    for limit, size in zip((45, 65, 85), sizes):
        if n <= limit:
            return size
    return sizes[-1]


def write_photos(articles: list[dict]) -> None:
    """Copies each story's photo into the site, plus a small square-ish version for lists."""
    from PIL import Image
    (OUT / "images").mkdir(exist_ok=True)
    for a in articles:
        p = a["photo"]
        if not p:
            continue
        try:
            shutil.copyfile(p["local"], OUT / "images" / f"{a['slug']}.jpg")
            with Image.open(p["local"]) as img:
                img.thumbnail((640, 640))
                img.convert("RGB").save(OUT / "images" / f"{a['slug']}-sm.jpg", "JPEG", quality=78, optimize=True)
        except OSError as e:
            print(f"  ! Photo for {a['slug']} couldn't be used ({e}); using the illustration")
            a["photo"] = None


def paginate(items: list, per_page: int) -> list[list]:
    return [items[i:i + per_page] for i in range(0, len(items), per_page)] or [[]]


# ─── Build ───────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_config()
    site = cfg["site"]
    base_url = site["base_url"].rstrip("/")
    base_path = urlparse(base_url).path.rstrip("/")
    cats = {c["slug"]: c for c in cfg["categories"]}
    per_page = int(cfg["publishing"].get("stories_per_page", 13))

    articles = load_articles(cats)
    now = dt.datetime.now(dt.timezone.utc)
    this_week = sum(1 for a in articles if now - a["date"] < dt.timedelta(days=7))

    # This week's homepage headline: a new English phrase every Monday.
    phrases = [p for p in (site.get("tagline_phrases") or []) if p]
    if phrases and "{phrase}" in (site.get("rotating_tagline") or ""):
        weeks = (now.date() - dt.date(2026, 10, 26)).days // 7  # the first phrase is used from Monday 26 October 2026
        site["headline"] = site["rotating_tagline"].replace("{phrase}", phrases[weeks % len(phrases)])
    else:
        site["headline"] = site["tagline"]

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(ROOT / "static", OUT / "static")
    write_photos(articles)

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))

    def url(p: str = "/") -> str:
        return base_path + p

    S = seo.Site(cfg, base_url)
    partners = [p for p in (cfg.get("partners") or []) if p and p.get("name")]
    env.globals.update(
        site=site, categories=cfg["categories"], url=url, abs_url=lambda p: base_url + p,
        icon=icon_svg, cover=cover_svg, photo=photo_html, year=now.year, approval=not cfg["publishing"].get("auto_publish"),
        feeds=cfg.get("feeds", []), support=cfg.get("support") or {}, newsletter=cfg.get("newsletter") or {},
        analytics=cfg.get("analytics") or {}, partners=partners,
        partners_cfg=cfg.get("partners_page") or {},
    )

    def render(template: str, out_path: str, **ctx) -> None:
        target = OUT / out_path.lstrip("/")
        if out_path.endswith("/"):
            target = target / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(template).render(page_path=out_path, **ctx), encoding="utf-8")

    # Homepage + older pages
    lead, rest = (articles[0], articles[1:]) if articles else (None, [])
    pages = paginate(rest, per_page)
    for i, chunk in enumerate(pages, start=1):
        render(
            "index.html", "/" if i == 1 else f"/page/{i}/",
            lead=lead if i == 1 else None, stories=chunk, page=i, pages=len(pages),
            this_week=this_week, total=len(articles),
            schema=seo.home_schema(S, articles) if i == 1 else None,
            prev_url=(url("/") if i == 2 else url(f"/page/{i - 1}/")) if i > 1 else None,
            next_url=url(f"/page/{i + 1}/") if i < len(pages) else None,
        )

    # Section pages
    for slug, cat in cats.items():
        in_cat = [a for a in articles if a["category"] == slug]
        cpages = paginate(in_cat, per_page)
        for i, chunk in enumerate(cpages, start=1):
            base = f"/category/{slug}/"
            render(
                "category.html", base if i == 1 else f"{base}page/{i}/",
                cat=cat, stories=chunk, page=i, pages=len(cpages), total=len(in_cat),
                schema=seo.category_schema(S, cat, in_cat) if i == 1 else None,
                prev_url=(url(base) if i == 2 else url(f"{base}page/{i - 1}/")) if i > 1 else None,
                next_url=url(f"{base}page/{i + 1}/") if i < len(cpages) else None,
            )

    # Story pages
    for a in articles:
        related = [b for b in articles if b["category"] == a["category"] and b is not a][:3]
        if len(related) < 3:
            related += [b for b in articles if b is not a and b not in related][: 3 - len(related)]
        render("article.html", a["path"], a=a, related=related, schema=seo.article_schema(S, a))

    # Social images: HTML cards that scripts/make_social_images.py turns into JPEGs
    if CARDS.exists():
        shutil.rmtree(CARDS)
    CARDS.mkdir(parents=True)
    card_tpl = env.get_template("social_card.html")
    design_hash = hashlib.md5((ROOT / "templates" / "social_card.html").read_bytes()).hexdigest()[:8]
    manifest = []
    for a in articles:
        key = (f"{card_photo(a)}|{a['title']}|{a['dek']}|{a['category']}|{a['seed']}|{a['location']}|{site['name']}|{a['cat']['colour']}|"
               f"{'|'.join(a['key_points'])}|{a['why']}|{design_hash}")
        h = hashlib.md5(key.encode()).hexdigest()[:10]
        variants = [("ig", 1080, 1350, f"{a['slug']}.jpg"), ("share", 1200, 630, f"{a['slug']}-share.jpg")]
        if a["key_points"]:
            variants.append(("brief", 1080, 1350, f"{a['slug']}-2.jpg"))
            if a["why"]:
                variants.append(("why", 1080, 1350, f"{a['slug']}-3.jpg"))
        points_len = max((len(p) for p in a["key_points"]), default=0)
        for variant, w, ht, out in variants:
            html_path = CARDS / f"{a['slug']}-{variant}.html"
            html_path.write_text(card_tpl.render(
                a=a, variant=variant, w=w, h=ht, card_photo=card_photo(a), fonts_dir=(ROOT / "static" / "fonts").as_uri(),
                ig_size=title_size(a["title"], (86, 76, 66, 62)), share_size=title_size(a["title"], (58, 50, 44, 40)),
                brief_size=52 if points_len <= 70 else 46 if points_len <= 100 else 40,
                why_size=44 if len(a["why"]) <= 280 else 40 if len(a["why"]) <= 420 else 35 if len(a["why"]) <= 560 else 31,
            ), encoding="utf-8")
            manifest.append({"html": str(html_path), "out": out, "w": w, "h": ht, "cache_key": f"{a['slug']}-{variant}-{h}.jpg"})
    (CARDS / "manifest.json").write_text(json.dumps(manifest, indent=1))

    render("about.html", "/about/", schema=seo.about_schema(S))
    render("privacy.html", "/privacy/")
    if partners:
        render("partners.html", "/partners/")
    render("404.html", "/404.html", robots="noindex")

    # Search engines and AI search tools
    seo.write_feed(OUT, S, articles)
    seo.write_sitemaps(OUT, S, articles, cats)
    seo.write_robots(OUT, S)
    seo.write_ai_files(OUT, S, articles, cats)
    seo.write_indexnow_key(OUT, S)

    print(f"Built {len(articles)} stories into {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
