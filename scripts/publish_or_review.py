"""
Runs after the news robot inside GitHub Actions.

- Approval mode (auto_publish: false): every new article becomes its own pull request.
  Merge it to publish, close it to throw it away.
- Auto mode (auto_publish: true): new articles are published straight away.
Also tidies away review requests that have sat unanswered for too long.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile

from common import ARTICLES_DIR, NEW_ARTICLES_FILE, load_config, load_json

LABEL = "new-article"


def run(*cmd: str, check: bool = True) -> str:
    print("$ " + " ".join(cmd), flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.stdout.strip():
        print(res.stdout.strip())
    if res.returncode != 0:
        print(res.stderr.strip())
        if check:
            raise SystemExit(f"Command failed: {' '.join(cmd)}")
    return res.stdout


def push_main() -> None:
    for _ in range(3):
        run("git", "pull", "--rebase", "origin", "main", check=False)
        if subprocess.run(["git", "push", "origin", "HEAD:main"]).returncode == 0:
            return
    raise SystemExit("Couldn't push to main.")


def has_changes(*paths: str) -> bool:
    return bool(run("git", "status", "--porcelain", "--", *paths).strip())


def pr_body(a: dict, cat_names: dict) -> str:
    return f"""## {a['title']}

*{a['dek']}*

**Section:** {cat_names.get(a['category'], a['category'])}  |  **Where:** {a['location']}

**In brief:**
{chr(10).join('- ' + p for p in a.get('key_points', []))}

---

{a['body']}

---

**Instagram caption:**

> {a.get('instagram_caption', '').replace(chr(10), chr(10) + '> ')}

---

**Original story:** [{a['source_title']}]({a['source_url']}) ({a['source_name']})

### What to do
- **Publish it:** tap **Merge pull request**, then **Confirm merge**. It's on the website within a few minutes, then joins the Instagram queue.
- **Bin it:** tap **Close pull request**.
- **Tweak the wording or caption first:** open **Files changed**, tap the **⋯** menu on the file, choose **Edit file**, save, then merge.

Worth a quick look at the original story to check the facts match.
"""


def close_stale(days: int) -> None:
    out = run("gh", "pr", "list", "--label", LABEL, "--state", "open",
              "--json", "number,createdAt", "--limit", "100", check=False)
    try:
        prs = json.loads(out or "[]")
    except json.JSONDecodeError:
        return
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    for pr in prs:
        created = dt.datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
        if created < cutoff:
            run("gh", "pr", "close", str(pr["number"]), "--delete-branch",
                "--comment", f"Closed automatically after {days} days without review.", check=False)


def main() -> None:
    cfg = load_config()
    pub = cfg["publishing"]
    cat_names = {c["slug"]: c["name"] for c in cfg["categories"]}
    new = load_json(NEW_ARTICLES_FILE, [])

    run("git", "config", "user.name", "good-news-bot")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")

    if pub.get("auto_publish"):
        if has_changes("content/articles", "data/seen.json"):
            run("git", "add", "content/articles", "data/seen.json")
            run("git", "commit", "-m", f"Publish {len(new)} new article(s)")
            push_main()
        return

    # 1. Save the "already seen" list to the main branch so stories aren't picked twice.
    if has_changes("data/seen.json"):
        run("git", "add", "data/seen.json")
        run("git", "commit", "-m", "Update list of stories already considered")
        push_main()

    # 2. One pull request per article, so each can be approved or binned on its own.
    if new:
        run("gh", "label", "create", LABEL, "--color", "FFC23D",
            "--description", "Article waiting for approval", "--force", check=False)
    for a in new:
        branch = f"article/{a['file'][:-3]}"
        run("git", "switch", "-c", branch)
        run("git", "add", str(ARTICLES_DIR / a["file"]))
        run("git", "commit", "-m", f"New article: {a['title']}")
        run("git", "push", "-u", "origin", branch)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(pr_body(a, cat_names))
        run("gh", "pr", "create", "--base", "main", "--head", branch,
            "--title", a["title"], "--body-file", f.name, "--label", LABEL, check=False)
        run("git", "switch", "main")

    close_stale(int(pub.get("discard_unreviewed_after_days", 5)))


if __name__ == "__main__":
    main()
