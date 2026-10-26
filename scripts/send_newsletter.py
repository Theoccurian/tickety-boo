"""
Puts together the weekly newsletter from the week's published stories and creates it in Kit.

By default it's saved as a DRAFT in Kit for you to read and send (newsletter: auto_send: false).
Set auto_send: true to have it sent automatically.

Needs the KIT_API_KEY repository secret (see SETUP.md).
Run:  python scripts/send_newsletter.py           (creates it in Kit)
      python scripts/send_newsletter.py --preview (writes newsletter-preview.html, sends nothing)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import sys

import requests

from common import ARTICLES_DIR, ROOT, load_config, published_at, read_article

KIT_API = "https://api.kit.com/v4/broadcasts"
NOW = dt.datetime.now(dt.timezone.utc)


def week_stories(days: int = 7, limit: int = 8) -> list[dict]:
    stories = []
    for path in ARTICLES_DIR.glob("*.md"):
        a = read_article(path)
        if not a or not a.get("title"):
            continue
        d = published_at(path, a)
        if NOW - d <= dt.timedelta(days=days):
            a["_date"], a["_slug"] = d, a.get("slug") or path.stem[11:]
            stories.append(a)
    stories.sort(key=lambda a: a["_date"], reverse=True)
    # Keep it varied: at most two stories from any one section.
    picked, per_cat = [], {}
    for a in stories:
        c = a.get("category")
        if per_cat.get(c, 0) < 2:
            picked.append(a)
            per_cat[c] = per_cat.get(c, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def build_email(cfg: dict, stories: list[dict]) -> tuple[str, str, str]:
    site = cfg["site"]
    base = site["base_url"].rstrip("/")
    cats = {c["slug"]: c for c in cfg["categories"]}
    e = html.escape
    nl = cfg.get("newsletter") or {}

    subject = nl.get("subject") or f"{len(stories)} good things from this week"
    preview = stories[0]["title"] if stories else site.get("tagline", "")

    rows = []
    for a in stories:
        cat = cats.get(a.get("category"), {"name": "", "colour": "#132036"})
        url = f"{base}/stories/{a['_slug']}/"
        rows.append(f"""
<tr><td style="padding:0 0 26px">
  <p style="margin:0 0 4px;font:600 13px/1.4 Arial,sans-serif;color:{cat['colour']}">{e(cat['name'])} &nbsp;·&nbsp; {e(a.get('location', ''))}</p>
  <p style="margin:0 0 6px;font:700 20px/1.25 Arial,sans-serif;color:#132036"><a href="{url}" style="color:#132036;text-decoration:none">{e(a['title'])}</a></p>
  <p style="margin:0 0 6px;font:16px/1.5 Georgia,serif;color:#55627A">{e(a.get('dek', ''))}</p>
  <a href="{url}" style="font:600 14px Arial,sans-serif;color:#132036">Read the story</a>
</td></tr>""")

    partners = [p for p in (cfg.get("partners") or []) if p and p.get("name")]
    offers = ""
    if partners:
        items = "".join(
            f"""<p style="margin:0 0 14px;font:16px/1.5 Arial,sans-serif;color:#132036"><strong>{e(p['name'])}</strong>: {e(p.get('offer', ''))}"""
            + (f""" with code <strong>{e(p['code'])}</strong>""" if p.get("code") else "")
            + (f""" <a href="{e(p['url'])}" style="color:#132036">Visit</a>""" if p.get("url") else "")
            + "</p>"
            for p in partners
        )
        offers = f"""
<tr><td style="padding:8px 20px 6px;background:#FFE9A8;border-radius:12px">
  <p style="margin:12px 0 10px;font:700 17px Arial,sans-serif;color:#132036">Subscriber offers</p>
  <p style="margin:0 0 12px;font:12px Arial,sans-serif;color:#55627A">Ad: paid partnerships with brands we've chosen to work with.</p>
  {items}
</td></tr><tr><td style="height:26px"></td></tr>"""

    support = cfg.get("support") or {}
    donate = ""
    if support.get("donate_url"):
        donate = f"""<tr><td style="padding:0 0 20px;font:15px/1.5 Georgia,serif;color:#55627A">
  {e(site['name'])} is free. If this brightened your week, you can <a href="{e(support['donate_url'])}" style="color:#132036;font-weight:600">support it here</a>.
</td></tr>"""

    intro = nl.get("intro") or "Here are some of the good things that happened around the world this week."
    body = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto">
<tr><td style="padding:0 0 22px;font:17px/1.55 Georgia,serif;color:#132036">{e(intro)}</td></tr>
{''.join(rows)}
{offers}
{donate}
<tr><td style="padding:6px 0 0;font:14px/1.5 Arial,sans-serif;color:#55627A">More every day at <a href="{base}/" style="color:#132036">{e(base.split('://')[-1])}</a>{f' and on <a href="{e(site["instagram_url"])}" style="color:#132036">Instagram</a>' if site.get('instagram_url') else ''}.</td></tr>
</table>"""
    return subject, preview, body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    nl = cfg.get("newsletter") or {}
    stories = week_stories(limit=int(nl.get("stories_per_issue", 8)))
    if len(stories) < 3:
        print(f"Only {len(stories)} story/stories this week; skipping the newsletter.")
        return
    subject, preview, body = build_email(cfg, stories)

    if args.preview:
        (ROOT / "newsletter-preview.html").write_text(f"<h3>{html.escape(subject)}</h3>{body}", encoding="utf-8")
        print("Wrote newsletter-preview.html")
        return
    if not nl.get("weekly_digest"):
        print("Weekly newsletter is switched off in config.yaml (newsletter: weekly_digest: false).")
        return
    key = os.environ.get("KIT_API_KEY", "").strip()
    if not key:
        sys.exit("KIT_API_KEY secret is missing. See SETUP.md.")

    send_at = None
    if nl.get("auto_send"):
        send_at = (NOW + dt.timedelta(minutes=15)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {"subject": subject, "preview_text": preview, "content": body, "description": f"Weekly digest {NOW.date()}",
               "public": bool(nl.get("public_archive", False)), "send_at": send_at}
    r = requests.post(KIT_API, json=payload, headers={"X-Kit-Api-Key": key, "Accept": "application/json"}, timeout=60)
    if r.status_code >= 300:
        sys.exit(f"Kit said ({r.status_code}): {r.text[:400]}")
    print("Newsletter " + ("scheduled to send in 15 minutes." if send_at else "saved as a draft in Kit. Open Kit to read it and press send."))


if __name__ == "__main__":
    main()
