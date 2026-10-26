"""
The news robot.

1. Reads the RSS feeds listed in config.yaml
2. Asks a fast AI model which headlines are genuinely good news
3. For the best few, fetches the original article and has the AI write
   an original short piece in the site's own words
4. Fact-checks each piece against the original and checks nothing was copied
5. Saves the finished articles into content/articles/

Run:  python scripts/find_news.py            (needs ANTHROPIC_API_KEY)
      python scripts/find_news.py --list     (just show candidate headlines, no AI)
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import difflib
import html
import os
import random
import re
import sys
import time
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests
import trafilatura

from common import (
    ARTICLES_DIR, NEW_ARTICLES_FILE, PHOTO_CHECKED_FILE, PHOTO_UPDATES_FILE, PHOTOS_DIR, SEEN_FILE,
    read_article,
    load_config, load_json, save_json, slugify, write_article,
)
from images import find_image, save_photo

USER_AGENT = "Mozilla/5.0 (compatible; GoodNewsBot/1.0; +https://github.com)"
NOW = dt.datetime.now(dt.timezone.utc)


def log(msg: str) -> None:
    print(msg, flush=True)


# ─── Feeds ──────────────────────────────────────────────────────────────────

def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalise_url(url: str) -> str:
    """Strip tracking parameters and fragments so the same story isn't seen twice."""
    p = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid", "cmp", "at_"))]
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", urlencode(query), ""))


def entry_time(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime.fromtimestamp(calendar.timegm(t), dt.timezone.utc)
    return None


def similar_to_any(title: str, others: list[str], threshold: float = 0.72) -> bool:
    t = title.lower()
    return any(difflib.SequenceMatcher(None, t, o.lower()).ratio() >= threshold for o in others)


def fetch_candidates(cfg: dict, seen: dict) -> list[dict]:
    max_age = dt.timedelta(hours=cfg["publishing"].get("max_age_hours", 48))
    candidates: list[dict] = []
    for feed in cfg.get("feeds", []):
        try:
            resp = requests.get(feed["url"], headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as e:  # noqa: BLE001 — one bad feed shouldn't stop the run
            log(f"  ! Couldn't read feed '{feed['name']}': {e}")
            continue
        count = 0
        for entry in parsed.entries[:40]:
            link = entry.get("link")
            title = clean_text(entry.get("title", ""))
            if not link or not title:
                continue
            url = normalise_url(link)
            if url in seen["urls"]:
                continue
            published = entry_time(entry)
            if published and NOW - published > max_age:
                continue
            if similar_to_any(title, seen.get("recent_headlines", [])):
                seen["urls"][url] = NOW.date().isoformat()
                continue
            summary = clean_text(entry.get("summary", ""))[:500]
            candidates.append({
                "id": len(candidates),
                "title": title,
                "summary": summary,
                "url": url,
                "source": feed["name"],
                "published": published.isoformat() if published else None,
            })
            count += 1
        log(f"  {feed['name']}: {count} new headline(s)")
    return candidates


# ─── AI ─────────────────────────────────────────────────────────────────────

class AI:
    def __init__(self, cfg: dict):
        import anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY is not set. Add it as a repository secret (see SETUP.md).")
        self.client = anthropic.Anthropic()
        self.errors = anthropic.APIError
        self.scoring_model = cfg["ai"].get("scoring_model", "claude-haiku-5-5")
        self.writing_model = cfg["ai"].get("writing_model", "claude-sonnet-5-5")
        # Some models don't accept a forced tool call; for those we ask nicely instead.
        self.no_forced_tools: set[str] = set()

    def call(self, model: str, system: str, prompt: str | list, tool_name: str, schema: dict, max_tokens: int = 4000) -> dict:
        """Ask the model to fill in a structured form (a 'tool'), so we always get clean data back."""
        last_err = None
        attempt = 0
        while attempt < 4:
            forced = model not in self.no_forced_tools
            sys_text = system if forced else (
                system + f"\n\nAlways give your answer by calling the {tool_name} tool, filling in every required field. Do not reply with plain text."
            )
            try:
                msg = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=sys_text,
                    tools=[{"name": tool_name, "description": "Submit your answer.", "input_schema": schema}],
                    tool_choice={"type": "tool", "name": tool_name} if forced else {"type": "auto"},
                    messages=[{"role": "user", "content": prompt}],
                )
                for block in msg.content:
                    if block.type == "tool_use" and block.name == tool_name:
                        missing = [k for k in schema.get("required", []) if k not in block.input]
                        if missing:
                            raise RuntimeError(f"answer was missing {', '.join(missing)}")
                        return block.input
                raise RuntimeError("model returned no structured answer")
            except (self.errors, RuntimeError) as e:
                text = str(e)
                if forced and "tool_choice" in text and "not supported" in text:
                    # This model can't be forced to use a tool: switch method and retry straight away.
                    self.no_forced_tools.add(model)
                    log(f"    {model} needs a different request style; switching")
                    continue
                last_err = e
                wait = 2 ** attempt * 5
                log(f"    AI call failed ({e}); retrying in {wait}s")
                time.sleep(wait)
                attempt += 1
        raise RuntimeError(f"AI call failed after retries: {last_err}")


SCORING_SYSTEM = """You are the news editor of a "good news" website. You decide which headlines are worth covering.

A story qualifies only if ALL of these are true:
- It reports something real that has actually happened or been achieved (not a plan, prediction, opinion piece, advert, product review, listicle, quiz, podcast or how-to guide).
- The outcome is clearly positive for people, animals or the planet.
- It is not mainly about politics, party politics, war, crime, death or disaster (a positive story set against tragedy, such as a rescue, is fine only if the positive part is the main point).
- It is not celebrity gossip or a sponsored/promotional story.
- It is about ONE story, not a roundup or digest of several stories (e.g. "This week's good news", "5 things that went right").

Score qualifying stories 1-10:
- 9-10: significant real-world impact, widely relevant, uplifting, clearly newsworthy
- 7-8: solid, genuine good news readers would be glad to learn
- 4-6: mildly positive, very local, or thin
- 1-3: barely positive
Mark duplicate=true if it covers the same event as any of the "already covered" headlines.
Pick the single best-fitting category."""


def score_candidates(ai: AI, cfg: dict, candidates: list[dict], recent: list[str]) -> list[dict]:
    cats = [c["slug"] for c in cfg["categories"]]
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "qualifies": {"type": "boolean"},
                        "score": {"type": "integer", "minimum": 1, "maximum": 10},
                        "category": {"type": "string", "enum": cats, "description": category_guide(cfg)},
                        "duplicate": {"type": "boolean"},
                    },
                    "required": ["id", "qualifies", "score", "category", "duplicate"],
                },
            }
        },
        "required": ["results"],
    }
    scored: list[dict] = []
    for i in range(0, len(candidates), 40):
        batch = candidates[i:i + 40]
        listing = "\n".join(f"[{c['id']}] ({c['source']}) {c['title']} — {c['summary'][:280]}" for c in batch)
        already = "\n".join(f"- {h}" for h in recent[-60:]) or "(none yet)"
        prompt = f"Already covered recently:\n{already}\n\nNew headlines to judge (one result per id):\n{listing}"
        try:
            out = ai.call(ai.scoring_model, SCORING_SYSTEM, prompt, "submit_scores", schema)
        except RuntimeError as e:
            log(f"  ! Scoring failed for a batch: {e}")
            continue
        by_id = {c["id"]: c for c in batch}
        for r in out.get("results", []):
            c = by_id.get(r.get("id"))
            if c:
                c.update(qualifies=r["qualifies"], score=r["score"], category=r["category"], duplicate=r["duplicate"])
                scored.append(c)
    return scored


def fetch_source_text(url: str) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log(f"    couldn't open original article: {e}")
        return None
    text = trafilatura.extract(resp.text, include_comments=False, include_tables=False, favor_precision=True)
    return text.strip()[:9000] if text else None


WRITING_SYSTEM = """You write short, original news articles for "{site}", a website of genuinely good news. British English spelling.

Strict rules:
1. Use ONLY facts found in the SOURCE TEXT. Never add names, numbers, dates, places, quotes or claims that are not in it. If unsure, leave it out.
2. Write entirely in your own words with your own sentence structure. Never copy or lightly reword sentences from the source.
3. At most ONE direct quote, under 15 words, copied exactly from the source and attributed to the person who said it.
4. Tone: warm, clear and factual, with a light English accent: the voice of a good-humoured British newspaper. Where it fits naturally, use classic English phrasing and adjectives and fond, characterful adjectives for the people and creatures in the story: "dashing young calves", "a good sport", "a plucky little charity", "a sprightly 90-year-old", "rather good news", "no mean feat", "a cracking result", "splendid", "chuffed", "a welcome boost", "on the up". Use one or two such touches per article, never more, and never in the headline, in quotes, in key points or in place of a precise fact. Understated rather than twee: no "jolly hockey sticks", no "guv'nor", no puns on the site name. No hype, no exclamation marks, no clichés ("heartwarming", "restores faith in humanity", "game-changer").
5. Body: 250-450 words in short paragraphs (2-4 sentences each), opening with the key news, not background. No headings except one final section headed exactly "## Why it matters": one paragraph explaining the significance using the source and widely established general knowledge only (no new statistics).
6. Headline: specific and accurate, sentence case, under 90 characters, no clickbait, no question headlines.
7. Standfirst (dek): one sentence of 15-30 words summarising the story.
8. Location: the country or region where it happened (e.g. "Kenya", "Scotland, UK"), or "Global".
9. Do not mention the source outlet in the body; the site credits it separately.
10. Key points: exactly 3 short, self-contained factual statements (each under 20 words) summarising who, what, where and the outcome, so a reader or search engine can understand the story at a glance. Same facts rules apply.
11. Instagram caption: 2-4 short, friendly sentences in your own words telling the story (same facts rules apply), with the same light English touch (one phrase at most), at most one fitting emoji, then a blank line, then "Full story: link in bio", then a blank line, then 3-5 relevant hashtags plus {hashtags}.

Set usable=false (and leave the other fields brief) if the source text is too thin to write 250 accurate words, is not actually good news, is promotional/opinion, or describes something that hasn't happened yet."""


def category_guide(cfg: dict) -> str:
    """Tells the AI what each section is for, so stories land in the right one."""
    return "The section that best fits. " + "; ".join(
        f"{c['slug']}: {c.get('guide') or c.get('description', '')}" for c in cfg["categories"]
    )


def article_schema(cats: list[str], guide: str = "") -> dict:
    return {
        "type": "object",
        "properties": {
            "usable": {"type": "boolean"},
            "reason_if_unusable": {"type": "string"},
            "headline": {"type": "string"},
            "dek": {"type": "string"},
            "category": {"type": "string", "enum": cats, **({"description": guide} if guide else {})},
            "location": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "body_markdown": {"type": "string"},
            "instagram_caption": {"type": "string"},
            "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        },
        "required": ["usable", "headline", "dek", "category", "location", "tags", "body_markdown", "instagram_caption", "key_points"],
    }


VERIFY_SYSTEM = """You are a strict fact-checker. Compare the ARTICLE with the SOURCE TEXT it was written from.
List every specific claim in the article (names, numbers, dates, places, organisations, quotes, outcomes) that is NOT supported by the source, or that misrepresents it.
Quotes must appear word-for-word in the source. General background that is common knowledge is acceptable.
Set accurate=true only if there are no problems."""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "accurate": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["accurate", "problems"],
}


def copied_passages(article: str, source: str, n: int = 10) -> list[str]:
    """Find any run of n+ consecutive words copied straight from the source."""
    def words(s):
        return re.findall(r"[a-z0-9']+", s.lower())
    src = words(source)
    grams = {" ".join(src[i:i + n]) for i in range(len(src) - n + 1)}
    art = words(article)
    hits, i = [], 0
    while i <= len(art) - n:
        g = " ".join(art[i:i + n])
        if g in grams:
            hits.append(g)
            i += n
        else:
            i += 1
    return hits


def write_one(ai: AI, cfg: dict, cand: dict, source_text: str) -> dict | None:
    cats = [c["slug"] for c in cfg["categories"]]
    tags = " ".join(cfg.get("instagram", {}).get("brand_hashtags", ["#goodnews"]))
    system = WRITING_SYSTEM.format(site=cfg["site"]["name"], hashtags=tags)
    prompt = (
        f"Suggested category: {cand['category']}\n"
        f"Original headline: {cand['title']}\n\n"
        f"SOURCE TEXT:\n<<<\n{source_text}\n>>>"
    )
    draft = ai.call(ai.writing_model, system, prompt, "submit_article", article_schema(cats, category_guide(cfg)), max_tokens=3000)
    if not draft.get("usable"):
        log(f"    skipped by writer: {draft.get('reason_if_unusable', 'not usable')}")
        return None

    for attempt in range(2):
        points = "\n".join(f"- {p}" for p in draft.get("key_points", []))
        full = f"{draft['headline']}\n{draft['dek']}\nKEY POINTS:\n{points}\n\n{draft['body_markdown']}\n\nINSTAGRAM CAPTION:\n{draft.get('instagram_caption', '')}"
        problems = []
        copied = copied_passages(full, source_text)
        if copied:
            problems += [f"Copied wording from the source, rewrite in your own words: \"{c}\"" for c in copied]
        check = ai.call(
            ai.writing_model, VERIFY_SYSTEM,
            f"SOURCE TEXT:\n<<<\n{source_text}\n>>>\n\nARTICLE:\n<<<\n{full}\n>>>",
            "submit_check", VERIFY_SCHEMA, max_tokens=1500,
        )
        if not check.get("accurate"):
            problems += check.get("problems", []) or ["Fact-checker flagged an unspecified problem."]
        if not problems:
            return draft
        if attempt == 1:
            log("    dropped: still failed checks after a rewrite -> " + "; ".join(problems)[:300])
            return None
        log(f"    fixing {len(problems)} issue(s) found in checks")
        fix_prompt = (
            prompt
            + "\n\nYOUR PREVIOUS DRAFT:\n<<<\n" + full + "\n>>>\n\n"
            + "Problems that must be fixed:\n" + "\n".join(f"- {p}" for p in problems)
            + "\n\nRewrite the whole article, fixing every problem and following all rules."
        )
        draft = ai.call(ai.writing_model, system, fix_prompt, "submit_article", article_schema(cats, category_guide(cfg)), max_tokens=3000)
        if not draft.get("usable"):
            return None
    return None


# ─── Main ───────────────────────────────────────────────────────────────────

def prune_seen(seen: dict) -> None:
    cutoff = (NOW - dt.timedelta(days=60)).date().isoformat()
    seen["urls"] = {u: d for u, d in seen["urls"].items() if d >= cutoff}
    seen["recent_headlines"] = seen.get("recent_headlines", [])[-150:]


def backfill_photos(cfg: dict, ai: "AI", just_written: list[str] = ()) -> None:
    """Looks for photos for published stories that don't have one yet, a couple per run.
    Each photo found is sent for approval; each story is only tried once."""
    img_cfg = cfg.get("images", {})
    per_run = int(img_cfg.get("backfill_per_run", 2))
    if not img_cfg.get("enabled", True) or per_run <= 0:
        return
    checked = set(load_json(PHOTO_CHECKED_FILE, [])) | set(just_written)  # new stories already had their try
    todo = []
    for path in sorted(ARTICLES_DIR.glob("*.md"), reverse=True):
        if path.name in checked:
            continue
        a = read_article(path)
        if a and a.get("title") and not a.get("image"):
            todo.append((path, a))
    if not todo:
        save_json(PHOTO_CHECKED_FILE, sorted(checked))
        return
    updates = []
    log(f"Looking for photos for {min(per_run, len(todo))} older story/stories…")
    for path, a in todo[:per_run]:
        log(f"  {a['title']}")
        checked.add(path.name)
        body = a.pop("body")
        a.pop("file", None)
        image = find_image(ai, a["title"], a.get("dek", ""), body, log=log)
        size = image and save_photo(image["url"], PHOTOS_DIR / f"{path.stem}.jpg")
        if image and size:
            image.update(file=f"{path.stem}.jpg", width=size[0], height=size[1])
            a["image"] = image
            write_article(path, a, body)
            updates.append({"file": path.name, "title": a["title"], "image": image})
            log(f"    photo: {image['caption']} ({image['library']})")
    save_json(PHOTO_CHECKED_FILE, sorted(checked))
    save_json(PHOTO_UPDATES_FILE, updates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="only list candidate headlines (no AI, nothing saved)")
    args = parser.parse_args()

    cfg = load_config()
    pub = cfg["publishing"]
    seen = load_json(SEEN_FILE, {"urls": {}, "recent_headlines": []})
    seen.setdefault("urls", {})
    seen.setdefault("recent_headlines", [])
    save_json(NEW_ARTICLES_FILE, [])
    save_json(PHOTO_UPDATES_FILE, [])

    log("Reading news feeds…")
    candidates = fetch_candidates(cfg, seen)
    log(f"{len(candidates)} new headline(s) to consider.")
    if args.list:
        for c in candidates:
            log(f"- [{c['source']}] {c['title']}")
        return
    if not candidates:
        prune_seen(seen)
        save_json(SEEN_FILE, seen)
        backfill_photos(cfg, AI(cfg))
        return

    ai = AI(cfg)
    log("Picking the best good-news stories…")
    scored = score_candidates(ai, cfg, candidates, seen["recent_headlines"])
    # Everything we've judged is marked as seen so it isn't re-judged next time.
    for c in candidates:
        if "score" in c:
            seen["urls"][c["url"]] = NOW.date().isoformat()

    min_score = pub.get("min_score", 7)
    shortlist = sorted(
        (c for c in scored if c["qualifies"] and not c["duplicate"] and c["score"] >= min_score),
        key=lambda c: (c["score"], random.random()), reverse=True,
    )
    log(f"{len(shortlist)} story/stories scored {min_score}+.")

    target = pub.get("articles_per_run", 3)
    used_sources: set[str] = set()
    written: list[dict] = []
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    # Keep each run varied: prefer stories from different sections, then fill up with the best of the rest.
    first_pass, seen_cats = [], set()
    for c in shortlist:
        if c["category"] not in seen_cats:
            first_pass.append(c)
            seen_cats.add(c["category"])
    shortlist = first_pass + [c for c in shortlist if c not in first_pass]

    for cand in shortlist:
        if len(written) >= target:
            break
        if cand["source"] in used_sources:  # keep each run varied
            continue
        log(f"• Writing: {cand['title']}  (score {cand['score']}, {cand['source']})")
        source_text = fetch_source_text(cand["url"])
        if not source_text or len(source_text) < 600:
            log("    skipped: couldn't get enough of the original article to write accurately")
            continue
        try:
            art = write_one(ai, cfg, cand, source_text)
        except RuntimeError as e:
            log(f"    skipped: {e}")
            continue
        if not art:
            continue
        if similar_to_any(art["headline"], seen["recent_headlines"]):
            log("    skipped: too similar to a recent story")
            continue

        slug = slugify(art["headline"])
        path = ARTICLES_DIR / f"{NOW.date().isoformat()}-{slug}.md"
        n = 2
        while path.exists():
            path = ARTICLES_DIR / f"{NOW.date().isoformat()}-{slug}-{n}.md"
            n += 1
        meta = {
            "title": art["headline"].strip(),
            "dek": art["dek"].strip(),
            "date": NOW.replace(microsecond=0).isoformat(),
            "category": art["category"],
            "location": art["location"].strip(),
            "tags": [t.strip() for t in art.get("tags", []) if t.strip()][:5],
            "source_name": cand["source"],
            "source_title": cand["title"],
            "source_url": cand["url"],
            "slug": path.stem[11:],
            "cover_seed": random.randint(1, 10**6),
            "key_points": [p.strip() for p in art.get("key_points", []) if p.strip()][:3],
            "instagram_caption": art.get("instagram_caption", "").strip(),
        }
        if cfg.get("images", {}).get("enabled", True):
            log("    looking for a photo")
            image = find_image(ai, meta["title"], meta["dek"], art["body_markdown"], log=log)
            size = image and save_photo(image["url"], PHOTOS_DIR / f"{path.stem}.jpg")
            if image and size:
                image.update(file=f"{path.stem}.jpg", width=size[0], height=size[1])
                meta["image"] = image
                log(f"    photo: {image['caption']} ({image['library']})")
            elif image:
                log("    no photo: couldn't download the chosen one")
        write_article(path, meta, art["body_markdown"])
        used_sources.add(cand["source"])
        seen["recent_headlines"] += [meta["title"], cand["title"]]
        written.append({**meta, "file": path.name, "body": art["body_markdown"]})
        log(f"    saved {path.name}")

    prune_seen(seen)
    save_json(SEEN_FILE, seen)
    save_json(NEW_ARTICLES_FILE, written)
    backfill_photos(cfg, ai, [w["file"] for w in written])
    log(f"Done: {len(written)} new article(s).")


if __name__ == "__main__":
    main()
