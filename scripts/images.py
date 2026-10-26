"""
Finds an honest, correctly licensed photo for a story.

Two kinds of subject:
  specific - a named species, place, landmark, organisation or object
             -> only an exact match from Wikimedia Commons, captioned "file photo"
  generic  - an everyday scene (a school breakfast, solar panels, volunteers planting trees)
             -> a stock photo from Pixabay, captioned "Illustrative photo"

Every candidate is looked at by the AI before it is used. If nothing clearly shows the
right subject, the story keeps its sunrise illustration: no photo is better than a wrong one.
"""
from __future__ import annotations

import base64
import html
import os
import re

import io
from pathlib import Path

import requests

UA = "TicketyBooNewsBot/1.0 (https://ticketyboonews.co.uk; hello@ticketyboonews.co.uk)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_API = "https://api.pexels.com/v1/search"
PIXABAY_API = "https://pixabay.com/api/"

# Licences we accept from Wikimedia Commons: public domain, CC0, CC BY and CC BY-SA.
# Share-alike (SA) photos are used on the site only, not cropped into Instagram slides.
PLAN_SYSTEM = """You choose the photo for a news story on a good-news website. Honesty matters more than prettiness.

Decide what a photo would need to show:
- kind "specific": the story is about a named species, place, landmark, building, organisation, vehicle or object, where a photo of something else would mislead (e.g. Irrawaddy dolphins, the Mekong River, a named national park). The photo must show exactly that.
- kind "generic": the story's subject is an everyday scene where any good photo of that kind of thing is honest as an illustration (e.g. children eating breakfast at school, solar panels on a roof, a nurse with a patient).
- kind "none": no photo would be honest or useful (e.g. abstract policy, a named private person, a very specific event).

Never ask for photos of identifiable private people. Prefer subjects over people where possible."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["specific", "generic", "none"]},
        "must_show": {"type": "string", "description": "Exactly what the photo must clearly show, e.g. 'an Irrawaddy dolphin'"},
        "search_terms": {"type": "string", "description": "2-5 word search query for a photo library"},
        "caption": {"type": "string", "description": "Short factual caption, e.g. 'An Irrawaddy dolphin in the Mekong River'. Must not claim it shows the people, animals or event in the story."},
    },
    "required": ["kind", "must_show", "search_terms", "caption"],
}

CHECK_SYSTEM = """You check candidate photos for a news story. Look at each image carefully.
Accept a photo only if it clearly and recognisably shows what is required. For a specific subject (a species, a place, a landmark), it must be that exact subject, not something similar.
Reject photos that are blurry, mostly text, maps or diagrams, contain logos or brand names, show identifiable children's faces close up, or could mislead readers about the story.
Pick the single best acceptable photo, or none."""

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "best": {"type": "integer", "description": "Number of the best acceptable photo, or 0 if none is acceptable"},
        "reason": {"type": "string"},
    },
    "required": ["best", "reason"],
}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _licence_ok(short_name: str) -> tuple[bool, bool]:
    """Returns (usable, ok_to_crop_for_instagram)."""
    s = (short_name or "").lower().replace("_", " ")
    words = s.replace("-", " ").split()
    if "nc" in words or "nd" in words:
        return False, False
    if s.startswith(("cc0", "pd", "public domain", "no restrictions")) or "public domain" in s:
        return True, True
    if s.startswith(("cc by", "cc-by", "attribution")):
        return True, "sa" not in words
    return False, False


def commons_candidates(query: str, limit: int = 8) -> list[dict]:
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 1600,
    }
    try:
        r = requests.get(COMMONS_API, params=params, headers={"User-Agent": UA}, timeout=25)
        pages = (r.json().get("query") or {}).get("pages") or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        if info.get("width", 0) < 900:
            continue
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        usable, crop_ok = _licence_ok(lic)
        if not usable:
            continue
        out.append({
            "thumb": (info.get("thumburl") or "").replace("/1600px-", "/640px-") or None,
            "full": info.get("thumburl") or info.get("url"),
            "source_page": info.get("descriptionurl"),
            "description": _clean((meta.get("ImageDescription") or {}).get("value", ""))[:300] or p.get("title", ""),
            "artist": _clean((meta.get("Artist") or {}).get("value", "")) or "Unknown",
            "licence": lic, "licence_url": (meta.get("LicenseUrl") or {}).get("value", ""),
            "library": "Wikimedia Commons", "ig_ok": crop_ok,
        })
    return out


def pixabay_candidates(query: str, limit: int = 8) -> list[dict]:
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    try:
        r = requests.get(PIXABAY_API, params={
            "key": key, "q": query[:100], "image_type": "photo", "orientation": "horizontal",
            "safesearch": "true", "per_page": max(3, limit), "min_width": 1200,
        }, headers={"User-Agent": UA}, timeout=25)
        hits = r.json().get("hits") or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for h in hits:
        web = h.get("webformatURL") or ""
        if not web:
            continue
        out.append({
            "thumb": web, "full": h.get("largeImageURL") or web.replace("_640.", "_1280."),
            "source_page": h.get("pageURL"), "description": f"Photo tagged: {h.get('tags', '')}",
            "artist": h.get("user") or "Unknown", "licence": "Pixabay Content Licence",
            "licence_url": "https://pixabay.com/service/license-summary/", "library": "Pixabay", "ig_ok": True,
        })
    return out


def pexels_candidates(query: str, limit: int = 8) -> list[dict]:
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    try:
        r = requests.get(PEXELS_API, params={"query": query, "per_page": limit, "orientation": "landscape"},
                         headers={"Authorization": key, "User-Agent": UA}, timeout=25)
        photos = r.json().get("photos") or []
    except Exception:  # noqa: BLE001
        return []
    return [{
        "thumb": p["src"].get("medium"), "full": p["src"].get("large2x") or p["src"].get("original"),
        "source_page": p.get("url"), "description": p.get("alt") or "",
        "artist": p.get("photographer") or "Unknown", "licence": "Pexels licence",
        "licence_url": "https://www.pexels.com/license/", "library": "Pexels", "ig_ok": True,
    } for p in photos if p.get("src")]


def _thumbnail(url: str) -> tuple[str, str] | None:
    """Downloads a small preview so the AI can look at it. Returns (media type, base64 data)."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    kind = r.headers.get("content-type", "").split(";")[0].strip()
    if kind not in ("image/jpeg", "image/png", "image/webp", "image/gif") or len(r.content) > 4_000_000:
        return None
    return kind, base64.b64encode(r.content).decode()


def find_image(ai, story_title: str, dek: str, body: str, log=print) -> dict | None:
    """Returns image details for the story's front matter, or None to keep the illustration."""
    try:
        plan = ai.call(
            ai.scoring_model, PLAN_SYSTEM,
            f"Headline: {story_title}\nSummary: {dek}\n\nStory:\n{body[:2500]}",
            "plan_photo", PLAN_SCHEMA, max_tokens=600,
        )
    except RuntimeError as e:
        log(f"    no photo: {e}")
        return None
    kind = plan.get("kind")
    if kind == "none":
        log("    no photo: subject isn't suitable for one")
        return None

    if kind == "specific":
        candidates = commons_candidates(plan["search_terms"])
    else:
        q = plan["search_terms"]
        candidates = pixabay_candidates(q) or pexels_candidates(q) or commons_candidates(q)
    candidates = [c for c in candidates if c.get("thumb")][:6]
    if not candidates:
        log(f"    no photo found for '{plan['search_terms']}'")
        return None

    content = [{"type": "text", "text": (
        f"Story headline: {story_title}\nThe photo must clearly show: {plan['must_show']}\n"
        f"Subject type: {kind} ({'must be exactly this subject' if kind == 'specific' else 'any honest photo of this kind of scene'})\n\n"
        "Candidates follow, numbered from 1."
    )}]
    shown = []
    for c in candidates:
        data = _thumbnail(c["thumb"])
        if data:
            shown.append(c)
            content.append({"type": "text", "text": f"Photo {len(shown)}. Library description: {c['description'][:200]}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": data[0], "data": data[1]}})
    if not shown:
        log("    no photo: couldn't download any candidates")
        return None
    candidates = shown
    try:
        check = ai.call(ai.scoring_model, CHECK_SYSTEM, content, "choose_photo", CHECK_SCHEMA, max_tokens=500)
    except RuntimeError as e:
        log(f"    no photo: couldn't check candidates ({e})")
        return None
    best = int(check.get("best") or 0)
    if not (1 <= best <= len(candidates)):
        log(f"    no photo: none clearly showed {plan['must_show']}")
        return None

    c = candidates[best - 1]
    label = "file photo" if kind == "specific" else "Illustrative photo"
    caption = plan["caption"].rstrip(". ")
    return {
        "url": c["full"],
        "caption": f"{caption} ({label})" if kind == "specific" else f"{label}: {caption}",
        "credit": c["artist"][:80],
        "library": c["library"],
        "licence": c["licence"],
        "licence_url": c["licence_url"],
        "source_page": c["source_page"],
        "instagram_ok": bool(c["ig_ok"]),
        "kind": kind,
    }


def save_photo(url: str, dest: Path, max_width: int = 1600) -> tuple[int, int] | None:
    """Downloads the chosen photo, resizes it and saves a clean JPEG (no camera data). Returns (width, height)."""
    from PIL import Image, ImageOps
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=40)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    if img.width < 600 or img.height < 400:
        return None
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=80, optimize=True, progressive=True)
    return img.width, img.height
