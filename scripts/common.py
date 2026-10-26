"""Shared helpers used by the news robot and the site builder."""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
PHOTOS_DIR = ROOT / "content" / "photos"
DATA_DIR = ROOT / "data"
SEEN_FILE = DATA_DIR / "seen.json"
NEW_ARTICLES_FILE = DATA_DIR / "new_articles.json"
PHOTO_UPDATES_FILE = DATA_DIR / "photo_updates.json"
PHOTO_CHECKED_FILE = DATA_DIR / "photo_checked.json"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("publishing", {})
    cfg.setdefault("ai", {})
    return cfg


def slugify(text: str, max_len: int = 70) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text or "story"


def read_article(path: Path) -> dict | None:
    """Read a markdown file with YAML front matter. Returns None if malformed."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return None
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None
    meta["body"] = parts[2].strip()
    meta["file"] = path.name
    return meta


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    # Multi-line text (like Instagram captions) is written as an easy-to-edit block.
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def write_article(path: Path, meta: dict, body: str) -> None:
    front = yaml.dump(meta, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000)
    path.write_text(f"---\n{front}---\n\n{body.strip()}\n", encoding="utf-8")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _parse_dt(value) -> dt.datetime | None:
    try:
        if isinstance(value, dt.datetime):
            d = value
        else:
            d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


_PUBLISH_TIMES: dict | None = None


def publish_times() -> dict[str, dt.datetime]:
    """When each story actually went live: the moment its file arrived on the main branch
    (i.e. when you approved it), read from the project's history."""
    global _PUBLISH_TIMES
    if _PUBLISH_TIMES is not None:
        return _PUBLISH_TIMES
    times: dict[str, dt.datetime] = {}
    try:
        out = subprocess.run(
            ["git", "log", "--first-parent", "--diff-filter=A", "--name-only", "--format=@@%cI", "--", "content/articles"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        ).stdout
        current = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("@@"):
                current = _parse_dt(line[2:])
            elif line.endswith(".md") and current and Path(line).name not in times:
                times[Path(line).name] = current
    except (OSError, subprocess.SubprocessError):
        pass
    _PUBLISH_TIMES = times
    return times


def published_at(path: Path, meta: dict) -> dt.datetime:
    """The time a story went live. Falls back to the time it was written."""
    written = _parse_dt(meta.get("date") or path.name[:10]) or dt.datetime.now(dt.timezone.utc)
    live = publish_times().get(path.name)
    return max(written, live) if live else written
