"""
Everything that helps search engines and AI search tools find, understand and cite the site:
structured data (schema.org), sitemaps, robots.txt, llms.txt, plain-text story copies,
the RSS feed and the IndexNow key. Used by build_site.py.
"""
from __future__ import annotations

import datetime as dt
import json
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from markupsafe import Markup

# Crawlers that collect pages to *train* AI models (blocked only if allow_ai_training is false).
AI_TRAINING_BOTS = [
    "GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "CCBot",
    "meta-externalagent", "Bytespider", "Amazonbot",
]
# Crawlers that let AI *search* tools find and cite pages. Always welcome.
AI_SEARCH_BOTS = [
    "OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot",
    "Perplexity-User", "DuckAssistBot", "Meta-WebIndexer", "MistralAI-User", "Bingbot", "Googlebot",
]


class Site:
    """Small helper holding the site's settings and absolute-URL logic."""

    def __init__(self, cfg: dict, base_url: str):
        self.cfg = cfg
        self.s = cfg["site"]
        self.base = base_url
        self.name = self.s["name"]
        self.lang = self.s.get("language", "en-GB")

    def abs(self, path: str) -> str:
        return self.base + path

    @property
    def logo(self) -> str:
        return self.abs("/static/brand/logo-512.png")

    def org(self) -> dict:
        org = {
            "@type": "NewsMediaOrganization",
            "@id": self.abs("/#organization"),
            "name": self.name,
            "url": self.abs("/"),
            "logo": {"@type": "ImageObject", "url": self.logo, "width": 512, "height": 512},
            "description": self.s.get("description", ""),
            "publishingPrinciples": self.abs("/about/"),
            "correctionsPolicy": self.abs("/about/#corrections"),
        }
        if self.s.get("instagram_url"):
            org["sameAs"] = [self.s["instagram_url"]]
        if self.s.get("contact_email"):
            org["email"] = self.s["contact_email"]
        return org


def to_script(data) -> Markup:
    """JSON-LD safe to drop inside a <script> tag."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return Markup(text.replace("</", "<\\/"))


def crumbs(site: Site, trail: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": site.abs(path)}
            for i, (name, path) in enumerate(trail, start=1)
        ],
    }


def iso(d: dt.datetime) -> str:
    return d.replace(microsecond=0).isoformat()


# ─── Structured data per page type ───────────────────────────────────────────

def home_schema(site: Site, articles: list[dict]) -> Markup:
    return to_script({
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite", "@id": site.abs("/#website"), "name": site.name, "alternateName": "TBN",
                "url": site.abs("/"), "description": site.s.get("description", ""), "inLanguage": site.lang,
                "publisher": {"@id": site.abs("/#organization")},
            },
            site.org(),
            {
                "@type": "ItemList", "name": "Latest good news",
                "itemListElement": [
                    {"@type": "ListItem", "position": i, "url": site.abs(a["path"]), "name": a["title"]}
                    for i, a in enumerate(articles[:10], start=1)
                ],
            },
        ],
    })


def article_schema(site: Site, a: dict) -> Markup:
    modified = a.get("updated_dt") or a["date"]
    article = {
        "@type": "NewsArticle",
        "@id": site.abs(a["path"]) + "#article",
        "mainEntityOfPage": site.abs(a["path"]),
        "headline": a["title"][:110],
        "description": a.get("dek", ""),
        "abstract": " ".join(a.get("key_points", [])) or a.get("dek", ""),
        "image": [site.abs(f"/social/{a['slug']}-share.jpg"), site.abs(f"/social/{a['slug']}.jpg")],
        "datePublished": iso(a["date"]),
        "dateModified": iso(modified),
        "author": {"@type": "Organization", "name": site.name, "url": site.abs("/about/")},
        "publisher": {"@id": site.abs("/#organization")},
        "articleSection": a["cat"]["name"],
        "inLanguage": site.lang,
        "wordCount": a.get("words"),
        "isAccessibleForFree": True,
    }
    if a.get("tags"):
        article["keywords"] = ", ".join(a["tags"])
    if a.get("location") and a["location"].lower() != "global":
        article["contentLocation"] = {"@type": "Place", "name": a["location"]}
    if a.get("source_url"):
        article["isBasedOn"] = {
            "@type": "NewsArticle", "url": a["source_url"], "headline": a.get("source_title", ""),
            "publisher": {"@type": "Organization", "name": a.get("source_name", "")},
        }
        article["citation"] = a["source_url"]
    return to_script({
        "@context": "https://schema.org",
        "@graph": [
            article,
            site.org(),
            crumbs(site, [("Home", "/"), (a["cat"]["name"], f"/category/{a['cat']['slug']}/"), (a["title"], a["path"])]),
        ],
    })


def category_schema(site: Site, cat: dict, stories: list[dict]) -> Markup:
    path = f"/category/{cat['slug']}/"
    return to_script({
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "CollectionPage", "name": f"{cat['name']} good news", "url": site.abs(path),
                "description": cat.get("description", ""), "inLanguage": site.lang,
                "isPartOf": {"@id": site.abs("/#website")},
                "mainEntity": {
                    "@type": "ItemList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": i, "url": site.abs(a["path"]), "name": a["title"]}
                        for i, a in enumerate(stories[:20], start=1)
                    ],
                },
            },
            crumbs(site, [("Home", "/"), (cat["name"], path)]),
        ],
    })


def about_schema(site: Site) -> Markup:
    return to_script({
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "AboutPage", "name": f"About {site.name}", "url": site.abs("/about/"),
             "about": {"@id": site.abs("/#organization")}},
            site.org(),
        ],
    })


# ─── Files at the root of the site ───────────────────────────────────────────

def write_sitemaps(out: Path, site: Site, articles: list[dict], cats: dict) -> None:
    latest = iso(articles[0]["date"]) if articles else iso(dt.datetime.now(dt.timezone.utc))
    rows = [f"<url><loc>{site.abs('/')}</loc><lastmod>{latest}</lastmod></url>",
            f"<url><loc>{site.abs('/about/')}</loc></url>", f"<url><loc>{site.abs('/privacy/')}</loc></url>"]
    for slug in cats:
        in_cat = [a for a in articles if a["category"] == slug]
        lm = f"<lastmod>{iso(in_cat[0]['date'])}</lastmod>" if in_cat else ""
        rows.append(f"<url><loc>{site.abs(f'/category/{slug}/')}</loc>{lm}</url>")
    for a in articles:
        modified = a.get("updated_dt") or a["date"]
        rows.append(
            f"<url><loc>{site.abs(a['path'])}</loc><lastmod>{iso(modified)}</lastmod>"
            f"<image:image><image:loc>{site.abs(f'/social/{a["slug"]}-share.jpg')}</image:loc></image:image></url>"
        )
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
        + "".join(rows) + "</urlset>", encoding="utf-8")

    # Google News sitemap: only stories from the last 2 days, as Google asks.
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    news = [a for a in articles if a["date"] >= cutoff]
    lang = site.lang.split("-")[0]
    (out / "news-sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        + "".join(
            f"<url><loc>{site.abs(a['path'])}</loc><news:news><news:publication><news:name>{escape(site.name)}</news:name>"
            f"<news:language>{lang}</news:language></news:publication><news:publication_date>{iso(a['date'])}</news:publication_date>"
            f"<news:title>{escape(a['title'])}</news:title></news:news></url>"
            for a in news
        ) + "</urlset>", encoding="utf-8")


def write_robots(out: Path, site: Site) -> None:
    lines = ["# Search engines and AI search tools are welcome to read and cite this site.", "User-agent: *", "Allow: /", ""]
    for bot in AI_SEARCH_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    if not site.cfg.get("seo", {}).get("allow_ai_training", True):
        lines.append("# AI model training is not permitted.")
        lines += [f"User-agent: {bot}" for bot in AI_TRAINING_BOTS]
        lines += ["Disallow: /", ""]
    lines += [f"Sitemap: {site.abs('/sitemap.xml')}", f"Sitemap: {site.abs('/news-sitemap.xml')}", ""]
    (out / "robots.txt").write_text("\n".join(lines), encoding="utf-8")


def story_markdown(site: Site, a: dict) -> str:
    points = "\n".join(f"- {p}" for p in a.get("key_points", []))
    return (
        f"# {a['title']}\n\n"
        f"> {a.get('dek', '')}\n\n"
        f"- Published: {a['date'].date().isoformat()} by {site.name}\n"
        f"- Section: {a['cat']['name']}\n"
        f"- Location: {a.get('location', '')}\n"
        f"- Web page: {site.abs(a['path'])}\n"
        f"- Original report: [{a.get('source_title', '')}]({a.get('source_url', '')}) ({a.get('source_name', '')})\n\n"
        + (f"## In brief\n\n{points}\n\n" if points else "")
        + f"{a['body'].strip()}\n"
    )


def write_ai_files(out: Path, site: Site, articles: list[dict], cats: dict) -> None:
    """llms.txt (a site guide for AI tools) plus a plain-text copy of every story."""
    for a in articles:
        (out / "stories" / f"{a['slug']}.md").write_text(story_markdown(site, a), encoding="utf-8")

    approval = not site.cfg["publishing"].get("auto_publish")
    lines = [
        f"# {site.name}",
        "",
        f"> {site.s.get('description', '')}",
        "",
        "Every story reports something positive that has actually happened, is written up in our own words from a "
        "named original report (linked on each story), and is checked against that report"
        + (" and approved by an editor" if approval else "") + " before publishing. "
        "Each story page states its publication date, location and original source.",
        "",
        f"- [About {site.name} and how stories are made]({site.abs('/about/')})",
        f"- [RSS feed]({site.abs('/feed.xml')})",
        "",
        "## Sections",
        "",
    ]
    lines += [f"- [{c['name']}]({site.abs(f'/category/{s}/')}): {c.get('description', '')}" for s, c in cats.items()]
    lines += ["", "## Latest stories", ""]
    lines += [
        f"- [{a['title']}]({site.abs(f'/stories/{a["slug"]}.md')}): "
        f"{a.get('dek', '')} ({a.get('location', '')}, {a['date'].date().isoformat()})"
        for a in articles[:60]
    ]
    (out / "llms.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_feed(out: Path, site: Site, articles: list[dict]) -> None:
    def item(a):
        points = "".join(f"<li>{escape(p)}</li>" for p in a.get("key_points", []))
        html = (f"<p><em>{escape(a.get('dek', ''))}</em></p>" + (f"<ul>{points}</ul>" if points else "")
                + str(a["html"])
                + f"<p>Originally reported by {escape(a.get('source_name', ''))}: "
                  f"<a href=\"{escape(a.get('source_url', ''))}\">{escape(a.get('source_title', ''))}</a></p>")
        return (
            f"<item><title>{escape(a['title'])}</title><link>{site.abs(a['path'])}</link>"
            f"<guid isPermaLink=\"true\">{site.abs(a['path'])}</guid>"
            f"<pubDate>{format_datetime(a['date'])}</pubDate><category>{escape(a['cat']['name'])}</category>"
            f"<description>{escape(a.get('dek', ''))}</description>"
            f"<content:encoded><![CDATA[{html.replace(']]>', ']]&gt;')}]]></content:encoded>"
            f"<media:content url=\"{site.abs(f'/social/{a["slug"]}-share.jpg')}\" medium=\"image\" type=\"image/jpeg\" width=\"1200\" height=\"630\"/>"
            "</item>"
        )

    now = format_datetime(dt.datetime.now(dt.timezone.utc))
    (out / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:atom="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/"><channel>'
        f"<title>{escape(site.name)}</title><link>{site.abs('/')}</link>"
        f"<atom:link href=\"{site.abs('/feed.xml')}\" rel=\"self\" type=\"application/rss+xml\"/>"
        f"<description>{escape(site.s.get('description', ''))}</description><language>{site.lang}</language>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        f"<image><url>{site.logo}</url><title>{escape(site.name)}</title><link>{site.abs('/')}</link></image>"
        + "".join(item(a) for a in articles[:30]) + "</channel></rss>", encoding="utf-8")


def write_indexnow_key(out: Path, site: Site) -> None:
    seo = site.cfg.get("seo", {})
    key = seo.get("indexnow_key")
    if seo.get("indexnow") and key:
        (out / f"{key}.txt").write_text(key, encoding="utf-8")
