"""
After each publish, tells search engines that support IndexNow (Bing, which also powers
Microsoft Copilot, plus Yandex, Seznam, Naver and others) which pages are new, so they're
picked up within hours instead of days. Google reads the sitemaps instead.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlparse

import requests

from common import ARTICLES_DIR, load_config, published_at, read_article


def main() -> None:
    cfg = load_config()
    seo = cfg.get("seo", {})
    key = seo.get("indexnow_key")
    if not (seo.get("indexnow") and key):
        print("IndexNow is switched off.")
        return
    site = cfg["site"]
    base = site["base_url"].rstrip("/")
    if "YOUR-DOMAIN" in base:
        print("Set base_url in config.yaml first.")
        return

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=26)
    urls, sections = [], set()
    for path in ARTICLES_DIR.glob("*.md"):
        a = read_article(path)
        if not a:
            continue
        d = published_at(path, a)
        if d >= cutoff:
            urls.append(f"{base}/stories/{a.get('slug') or path.stem[11:]}/")
            sections.add(a.get("category"))
    if not urls:
        print("No new stories to announce.")
        return
    urls += [f"{base}/"] + [f"{base}/category/{s}/" for s in sections if s]

    body = {"host": urlparse(base).netloc, "key": key, "keyLocation": f"{base}/{key}.txt", "urlList": urls}
    try:
        r = requests.post("https://api.indexnow.org/indexnow", json=body, timeout=30)
        print(f"IndexNow: announced {len(urls)} page(s), reply {r.status_code}.")
    except requests.RequestException as e:
        print(f"IndexNow couldn't be reached this time ({e}). Search engines will still find pages via the sitemap.")


if __name__ == "__main__":
    main()
