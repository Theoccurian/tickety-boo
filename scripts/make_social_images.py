"""
Turns the social cards made by build_site.py into JPEG images:
  public/social/<story>.jpg        1080×1350, posted to Instagram
  public/social/<story>-share.jpg  1200×630, the preview shown when a link is shared

Finished images are kept in .social-cache/ so each one is only drawn once.
Run after build_site.py:  python scripts/make_social_images.py
"""
from __future__ import annotations

import json
import shutil

from playwright.sync_api import sync_playwright

from common import ROOT

CARDS = ROOT / "build" / "cards"
CACHE = ROOT / ".social-cache"
OUT = ROOT / "public" / "social"


def main() -> None:
    manifest_file = CARDS / "manifest.json"
    if not manifest_file.exists():
        raise SystemExit("Run scripts/build_site.py first.")
    manifest = json.loads(manifest_file.read_text())
    CACHE.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    todo = [m for m in manifest if not (CACHE / m["cache_key"]).exists()]
    if todo:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            for m in todo:
                page.set_viewport_size({"width": m["w"], "height": m["h"]})
                page.goto((CARDS / m["html"].split("/")[-1]).as_uri())
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(120)
                page.screenshot(path=str(CACHE / m["cache_key"]), type="jpeg", quality=88)
            browser.close()

    for m in manifest:
        shutil.copyfile(CACHE / m["cache_key"], OUT / m["out"])

    # Throw away cached images that no longer belong to any story.
    keep = {m["cache_key"] for m in manifest}
    for f in CACHE.glob("*.jpg"):
        if f.name not in keep:
            f.unlink()

    print(f"Social images: {len(todo)} drawn, {len(manifest) - len(todo)} reused.")

    # Short vertical videos for Instagram Reels, made from the same slides.
    try:
        import make_reels
        make_reels.main()
    except Exception as e:  # noqa: BLE001 - a video problem must never stop the website publishing
        print(f"Reels skipped this time: {e}")


if __name__ == "__main__":
    main()
