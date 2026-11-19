"""
Finds the real Instagram accounts of the organisations in a story, so posts can mention them.

No guessing: a handle is only used if the organisation's own website links to it.
  1. Collect the links inside the original article's main text (not the publisher's menus).
  2. The AI picks the organisations central to the story (at most 3).
  3. Open each organisation's website and read the Instagram link it publishes.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
import trafilatura

UA = "Mozilla/5.0 (compatible; TicketyBooNewsBot/1.0; +https://ticketyboonews.co.uk)"
NOT_HANDLES = {"p", "reel", "reels", "explore", "stories", "accounts", "about", "developer", "tv", "share",
               "direct", "legal", "privacy", "web", "sharer", "instagram", "help"}
SKIP_DOMAINS = ("facebook.com", "twitter.com", "x.com", "linkedin.com", "youtube.com", "tiktok.com", "doi.org",
                "google.com", "apple.com", "amazon.", "wikipedia.org", "bit.ly", "t.co", "pinterest.", "threads.net",
                "whatsapp.com", "mailto:", "archive.org", "gov.uk/government/news")

PICK_SYSTEM = """You help a good-news website mention the organisations behind a story on Instagram.
From the numbered links (taken from the original article), choose the official websites of organisations that are
central to this story: the charity, project, research group, university, council, company or community group that
did the good thing. At most 3. Skip news outlets, journals, generic references, governments' general pages,
shops, and anything only mentioned in passing. Choose none if nothing fits."""

PICK_SCHEMA = {
    "type": "object",
    "properties": {"choose": {"type": "array", "items": {"type": "integer"}, "maxItems": 3}},
    "required": ["choose"],
}


NAME_SYSTEM = """You help a good-news website mention the organisations behind a story on Instagram.
Name up to 3 organisations that are central to this story (the university, research institute, charity, hospital,
council, museum, zoo or company that did the good thing), and the address of each one's official main website
(e.g. Stanford University -> https://www.stanford.edu). Only include an organisation if you are confident of its
official website address. Skip news outlets, journals, governments in general, and anything mentioned in passing."""

NAME_SCHEMA = {
    "type": "object",
    "properties": {"orgs": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "website": {"type": "string"}}, "required": ["name", "website"]}}},
    "required": ["orgs"],
}

GENERIC_WORDS = {"the", "of", "and", "for", "university", "college", "institute", "trust", "foundation", "society",
                 "national", "royal", "school", "centre", "center", "hospital", "museum", "group", "uk", "inc", "ltd"}


def site_is(org_name: str, page: str) -> bool:
    """True if the page's own title says it belongs to this organisation (so a wrong guess is caught)."""
    title = " ".join(re.findall(r"<title[^>]*>(.*?)</title>", page, flags=re.I | re.S)[:1])
    title += " " + " ".join(re.findall(r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)', page, flags=re.I))
    title = html.unescape(title).lower()
    words = [w for w in re.findall(r"[a-z]+", org_name.lower()) if w not in GENERIC_WORDS and len(w) > 2]
    return bool(words) and all(w in title for w in words)


VERIFY_SYSTEM = """You check Instagram handles before a news site mentions them. Be strict: a wrong mention is much worse
than no mention. For each organisation you get its name, its own website, and the Instagram handles that website links to.
Pick a handle only if it is clearly that organisation's OWN official account (its name or a recognisable abbreviation
of it, e.g. Wildfowl & Wetlands Trust -> wwtworldwide). Return an empty string if none clearly matches, if it looks like a
partner, funder, person, shop or a different branch, or if you are at all unsure."""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"handles": {"type": "array", "items": {"type": "string"},
                               "description": "One entry per organisation, in order: the chosen handle, or empty string"}},
    "required": ["handles"],
}


def _get(url: str, timeout: int = 15) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        r.raise_for_status()
        return r.text if "html" in r.headers.get("content-type", "html") else None
    except Exception:  # noqa: BLE001
        return None


def instagram_handles_in(page: str) -> list[str]:
    found = re.findall(r"instagram\.com/([A-Za-z0-9_.]{2,30})/?(?=[\"'?#\s<])", page, flags=re.I)
    handles = [h.strip(".").lower() for h in found if h.lower() not in NOT_HANDLES]
    return [h for h, _ in Counter(handles).most_common()]


def article_links(source_url: str) -> list[tuple[str, str]]:
    """(link text, absolute url) for links inside the article's main text."""
    page = _get(source_url, timeout=25)
    if not page:
        return []
    main = trafilatura.extract(page, output_format="xml", include_links=True, include_comments=False) or ""
    links = re.findall(r'<ref target="([^"]+)">(.*?)</ref>', main, flags=re.S)
    src_domain = urlparse(source_url).netloc.replace("www.", "")
    out, seen = [], set()
    for href, text in links:
        url = urljoin(source_url, href)
        dom = urlparse(url).netloc.replace("www.", "")
        if not dom or dom == src_domain or any(s in url for s in SKIP_DOMAINS) or url in seen:
            continue
        seen.add(url)
        out.append((html.unescape(re.sub(r"<[^>]+>", "", text)).strip()[:80], url))
    return out[:25]


def find_instagram_tags(ai, title: str, dek: str, source_url: str, log=print, body_text: str = "") -> list[str]:
    links = article_links(source_url)
    direct = [(t, u) for t, u in links if "instagram.com/" in u]
    sites = [(t, u) for t, u in links if "instagram.com/" not in u]
    numbered = "\n".join(f"{i}. {t or '(no text)'} - {u}" for i, (t, u) in enumerate(sites, start=1))
    chosen: list[str] = []
    if sites and numbered:
        try:
            pick = ai.call(ai.scoring_model, PICK_SYSTEM, f"Story: {title}\n{dek}\n\nLinks:\n{numbered}",
                           "choose_links", PICK_SCHEMA, max_tokens=300)
            chosen = [sites[i - 1][1] for i in pick.get("choose", []) if 1 <= i <= len(sites)]
        except RuntimeError as e:
            log(f"    tags: couldn't choose links ({e})")
    # Gather the handles each organisation's own website links to.
    orgs = []
    names = {u: t for t, u in sites}
    for url in chosen:
        home = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
        found = instagram_handles_in(_get(url) or "") or instagram_handles_in(_get(home) or "")
        if found:
            orgs.append({"name": names.get(url) or urlparse(url).netloc, "site": urlparse(url).netloc, "candidates": found[:5]})
    for t, u in direct:  # an Instagram account the article itself links to
        found = instagram_handles_in(u + '"')
        if found:
            orgs.append({"name": t or "linked in the article", "site": "(linked directly in the article)", "candidates": found[:1]})
    # Organisations named in the story but not linked: look up their official site, and only trust it if the
    # site's own title confirms it's them.
    try:
        named = ai.call(ai.scoring_model, NAME_SYSTEM, f"Story: {title}\n{dek}\n\n{body_text[:2500]}",
                        "name_orgs", NAME_SCHEMA, max_tokens=400).get("orgs", [])
    except RuntimeError as e:
        log(f"    tags: couldn't name organisations ({e})")
        named = []
    seen_sites = {o["site"].replace("www.", "") for o in orgs}
    for org in named[:3]:
        site = str(org.get("website", "")).strip()
        if not site.startswith("http"):
            site = "https://" + site
        dom = urlparse(site).netloc
        if not dom or dom.replace("www.", "") in seen_sites:
            continue
        page = _get(f"https://{dom}/") or ""
        if not page or not site_is(org.get("name", ""), page):
            log(f"    tags: skipped {org.get('name')} ({dom}): couldn't confirm it's their site")
            continue
        found = instagram_handles_in(page)
        if found:
            orgs.append({"name": org["name"], "site": dom, "candidates": found[:5]})
            seen_sites.add(dom.replace("www.", ""))
    if not orgs:
        log("    Instagram mentions: none (no clear match)")
        return []
    # Strict check: only accept a handle that is clearly the organisation's own account.
    listing = "\n".join(f"{i}. {o['name']} - website {o['site']} - handles: {', '.join('@' + h for h in o['candidates'])}"
                        for i, o in enumerate(orgs, start=1))
    try:
        verdict = ai.call(ai.scoring_model, VERIFY_SYSTEM, f"Story: {title}\n\nOrganisations:\n{listing}",
                          "confirm_handles", VERIFY_SCHEMA, max_tokens=300)
    except RuntimeError as e:
        log(f"    tags: couldn't check handles ({e}); mentioning nobody")
        return []
    handles: list[str] = []
    for o, h in zip(orgs, verdict.get("handles", [])):
        h = str(h).strip().lstrip("@").lower()
        if h and h in o["candidates"] and h not in handles:  # must be one we actually found on their site
            handles.append(h)
    log(f"    Instagram mentions: {' '.join('@' + h for h in handles[:3]) or 'none (no clear match)'}")
    return handles[:3]
