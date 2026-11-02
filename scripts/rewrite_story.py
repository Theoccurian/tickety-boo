"""
Rewrites one story with today's writing rules and looks for a new picture.

Used by the "Rewrite a story" button in GitHub Actions. You type a few words from the
headline; it finds the story (waiting for approval, or already published), re-reads the
original article, rewrites it, finds a photo, and updates the pull request (or opens a new
one for a published story) so you can check it before it goes live.

Run:  python scripts/rewrite_story.py "psilocybin"
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from common import ARTICLES_DIR, PHOTOS_DIR, ROOT, load_config, read_article, slugify, write_article
from find_news import AI, fetch_source_text, log, write_one
from images import find_image, save_photo
from publish_or_review import LABEL, pr_body, run


def git(*args: str, check: bool = True) -> str:
    return run("git", *args, check=check)


def find_story(words: str) -> tuple[str | None, str]:
    """Returns (branch or None for main, article file name)."""
    want = [w for w in slugify(words).split("-") if w]
    if not want:
        sys.exit("Type a few words from the story's headline.")
    git("fetch", "--prune", "origin")
    branches = [b.strip() for b in git("branch", "-r").splitlines()
                if re.match(r"origin/(article|rewrite)/", b.strip())]
    for b in branches:
        stem = b.split("/", 2)[-1]
        merged = subprocess.run(["git", "merge-base", "--is-ancestor", b, "origin/main"]).returncode == 0
        if not merged and all(w in stem for w in want):
            return b.split("/", 1)[1], f"{stem}.md"
    for path in sorted(ARTICLES_DIR.glob("*.md"), reverse=True):
        if all(w in path.stem for w in want):
            return None, path.name
    sys.exit(f"Couldn't find a story matching '{words}'. Try different words from its headline.")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('Usage: rewrite_story.py "words from the headline"')
    words = sys.argv[1]
    cfg = load_config()
    git("config", "user.name", "good-news-bot")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")

    branch, filename = find_story(words)
    if branch:
        log(f"Found it waiting for approval: {filename}")
        git("switch", "-C", branch, f"origin/{branch}")
    else:
        branch = f"rewrite/{filename[:-3]}"
        log(f"Found it on the website: {filename}. The rewrite will come to you for approval.")
        git("switch", "-C", branch)
    path = ARTICLES_DIR / filename
    if not path.exists():
        # The file was deleted at some point (e.g. while editing the pull request): bring back the last version.
        rel = str(path.relative_to(ROOT))
        last = git("log", "--format=%H", "-1", "--diff-filter=d", "--", rel).strip() or \
            git("log", "--all", "--format=%H", "-1", "--diff-filter=A", "--", rel).strip()
        if not last:
            sys.exit(f"Couldn't find any version of {filename}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(git("show", f"{last}:{rel}"), encoding="utf-8")
        log("Brought back the story file, which had been deleted.")
    meta = read_article(path)
    if not meta:
        sys.exit(f"Couldn't read {filename}.")
    old_body = meta.pop("body")
    meta.pop("file", None)

    log(f"Re-reading the original: {meta.get('source_url')}")
    source_text = fetch_source_text(meta["source_url"])
    if not source_text or len(source_text) < 400:
        sys.exit("Couldn't read enough of the original article to rewrite it (the site may block robots).")

    ai = AI(cfg)
    cand = {"title": meta.get("source_title") or meta["title"], "category": meta.get("category", ""),
            "url": meta["source_url"], "source": meta.get("source_name", "")}
    log("Rewriting…")
    art = write_one(ai, cfg, cand, source_text)
    if not art:
        sys.exit("The rewrite didn't pass the checks, so the story was left as it was.")

    meta.update({
        "title": art["headline"].strip(),
        "dek": art["dek"].strip(),
        "category": art["category"],
        "location": art["location"].strip(),
        "tags": [t.strip() for t in art.get("tags", []) if t.strip()][:5],
        "key_points": [p.strip() for p in art.get("key_points", []) if p.strip()][:3],
        "instagram_caption": art.get("instagram_caption", "").strip(),
        "reel_hook": art.get("reel_hook", "").strip(),
    })
    body = art["body_markdown"]

    # A fresh picture under today's rules
    photo = PHOTOS_DIR / f"{path.stem}.jpg"
    meta.pop("image", None)
    log("Looking for a photo…")
    image = find_image(ai, meta["title"], meta["dek"], body, log=log)
    size = image and save_photo(image["url"], photo)
    if image and size:
        image.update(file=photo.name, width=size[0], height=size[1])
        meta["image"] = image
        log(f"Photo: {image['caption']} ({image['library']})")
    elif photo.exists():
        photo.unlink()
        log("No suitable photo, so it will use the sunrise illustration.")

    write_article(path, meta, body)
    git("add", "-A", str(path), str(PHOTOS_DIR))
    if not git("status", "--porcelain").strip():
        log("Nothing changed.")
        return
    git("commit", "-m", f"Rewrite: {meta['title']}")
    git("push", "--force-with-lease", "-u", "origin", branch)

    a = {**meta, "body": body, "file": filename}
    cat_names = {c["slug"]: c["name"] for c in cfg["categories"]}
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("**Rewritten with the latest rules.**\n\n" + pr_body(a, cat_names, branch))
    existing = run("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number", check=False).strip()
    if existing:
        run("gh", "pr", "edit", existing, "--title", meta["title"], "--body-file", f.name, check=False)
        log(f"Updated pull request #{existing}. Open it to check the new version.")
    else:
        run("gh", "label", "create", LABEL, "--color", "FFC23D", "--force", check=False)
        run("gh", "pr", "create", "--base", "main", "--head", branch, "--title", f"Rewrite: {meta['title']}",
            "--body-file", f.name, "--label", LABEL, check=False)
        log("Opened a pull request with the rewrite. Merge it to replace the published version.")


if __name__ == "__main__":
    main()
