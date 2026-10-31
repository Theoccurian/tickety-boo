"""
Posts published stories to Instagram, one at a time, spaced out through the day.

Runs every hour from GitHub Actions. Each run posts at most one story, and only if:
  - Instagram is switched on in config.yaml
  - it's within posting hours (UK time)
  - enough time has passed since the last post
  - there's a recently published story that hasn't been posted yet

Instagram access tokens expire after 60 days, so this script renews the token every
week by itself and stores it, encrypted, in data/instagram_token.json.

Needs two repository secrets: INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_TOKEN_KEY (see SETUP.md).
Run with --check to test the connection without posting anything.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken

from common import ARTICLES_DIR, DATA_DIR, load_config, load_json, published_at, read_article, save_json

API = "https://graph.instagram.com"
POSTED_FILE = DATA_DIR / "instagram_posted.json"
TOKEN_FILE = DATA_DIR / "instagram_token.json"
NOW = dt.datetime.now(dt.timezone.utc)
UK = ZoneInfo("Europe/London")


class InstagramError(Exception):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def api(method: str, path: str, **params) -> dict:
    resp = requests.request(method, f"{API}/{path}", params=params if method == "GET" else None,
                            data=params if method != "GET" else None, timeout=60)
    try:
        data = resp.json()
    except ValueError:
        raise InstagramError(f"Instagram returned an unreadable reply (HTTP {resp.status_code}).")
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise InstagramError(f"Instagram said: {err.get('message', resp.text)[:400]}")
    return data


# ─── Token handling ──────────────────────────────────────────────────────────

def fernet() -> Fernet:
    key = os.environ.get("INSTAGRAM_TOKEN_KEY", "")
    if len(key) < 20:
        sys.exit("INSTAGRAM_TOKEN_KEY secret is missing or too short (use 20+ random characters). See SETUP.md.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def get_token() -> tuple[str, bool]:
    """Returns (token, changed). Uses the stored, auto-renewed token unless you've pasted in a new one."""
    secret = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not secret:
        sys.exit("INSTAGRAM_ACCESS_TOKEN secret is missing. See SETUP.md.")
    secret_hash = hashlib.sha256(secret.encode()).hexdigest()
    f = fernet()
    stored = load_json(TOKEN_FILE, {})

    if stored.get("seeded_from") == secret_hash and stored.get("token"):
        try:
            token = f.decrypt(stored["token"].encode()).decode()
        except InvalidToken:
            sys.exit("Couldn't unlock the saved Instagram token. Has INSTAGRAM_TOKEN_KEY been changed? "
                     "Paste a fresh token into INSTAGRAM_ACCESS_TOKEN to start again.")
        refreshed = dt.datetime.fromisoformat(stored["refreshed_at"])
    else:
        log("Using the token from the INSTAGRAM_ACCESS_TOKEN secret.")
        token, refreshed = secret, None

    if refreshed is None or NOW - refreshed > dt.timedelta(days=7):
        try:
            data = api("GET", "refresh_access_token", grant_type="ig_refresh_token", access_token=token)
            token = data["access_token"]
            log("Renewed the Instagram access token (valid for another 60 days).")
        except InstagramError as e:
            # A brand-new token can't be renewed for 24 hours; that's fine, try again next week.
            log(f"Token not renewed this time ({e}).")
            if refreshed is not None:
                return token, False
        save_json(TOKEN_FILE, {
            "seeded_from": secret_hash,
            "refreshed_at": NOW.replace(microsecond=0).isoformat(),
            "token": f.encrypt(token.encode()).decode(),
        })
        return token, True
    return token, False


# ─── Posting ─────────────────────────────────────────────────────────────────

def _is_share_alike(img: dict) -> bool:
    return "sa" in str(img.get("licence", "")).lower().replace("-", " ").split()


def photo_on_instagram(a: dict, cfg: dict) -> dict | None:
    """The story's photo, if we're allowed to use it in Instagram posts (same rule as the site builder)."""
    img = a.get("image") if isinstance(a.get("image"), dict) else None
    if not img or not img.get("file"):
        return None
    if img.get("instagram_ok") or (_is_share_alike(img) and cfg["instagram"].get("share_alike_photos", False)):
        return img
    return None


def caption_for(a: dict, cfg: dict) -> str:
    cap = (a.get("instagram_caption") or "").strip()
    if not cap:
        tags = " ".join(cfg["instagram"].get("brand_hashtags", []))
        cap = f"{a['title']}\n\n{a.get('dek', '')}\n\nFull story: link in bio\n\n{tags}"
    img = photo_on_instagram(a, cfg)
    if img and img.get("library") == "Wikimedia Commons":
        credit = f"📷 Photo: {img.get('credit', 'Unknown')} / Wikimedia Commons, {img.get('licence', '')}"
        if _is_share_alike(img):
            credit += f". This post is shared under {img.get('licence')}"
        cap = f"{cap}\n\n{credit}."
    return cap[:2150]


def wait_until_ready(token: str, container: str, tries: int = 30) -> None:
    for _ in range(tries):
        status = api("GET", container, fields="status_code", access_token=token).get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Instagram couldn't process the images ({status}).")
        time.sleep(6)
    raise InstagramError("Instagram took too long to process the images; will try again next hour.")


def publish(token: str, user_id: str, image_urls: list[str], caption: str) -> str:
    """One image = a normal post. Two or more = a swipeable carousel."""
    if len(image_urls) == 1:
        container = api("POST", f"{user_id}/media", image_url=image_urls[0], caption=caption, access_token=token)["id"]
    else:
        children = []
        for url in image_urls:
            child = api("POST", f"{user_id}/media", image_url=url, is_carousel_item="true", access_token=token)["id"]
            wait_until_ready(token, child)
            children.append(child)
        container = api("POST", f"{user_id}/media", media_type="CAROUSEL", children=",".join(children),
                        caption=caption, access_token=token)["id"]
    wait_until_ready(token, container)
    return api("POST", f"{user_id}/media_publish", creation_id=container, access_token=token)["id"]


def publish_reel(token: str, user_id: str, video_url: str, caption: str, ig: dict) -> str:
    """A short vertical video. Reels are what Instagram shows to people who don't follow you yet."""
    params = dict(media_type="REELS", video_url=video_url, caption=caption,
                  share_to_feed="true" if ig.get("reels_in_grid", False) else "false", access_token=token)
    if ig.get("trial_reels", False):
        # Trial reels go to non-followers first (needs 200+ followers). Shared to followers if they do well.
        params["trial_params"] = '{"graduation_strategy":"SS_PERFORMANCE"}'
    try:
        container = api("POST", f"{user_id}/media", **params)["id"]
    except InstagramError as e:
        if "trial_params" not in params:
            raise
        log(f"Trial reel not available ({e}); posting it as a normal Reel.")
        params.pop("trial_params")
        container = api("POST", f"{user_id}/media", **params)["id"]
    wait_until_ready(token, container, tries=50)
    return api("POST", f"{user_id}/media_publish", creation_id=container, access_token=token)["id"]


def post_pending_reel(token: str, user_id: str, cfg: dict, posted: dict, only: str | None = None) -> bool:
    """Posts the Reel for a story whose carousel is already up. Returns True if one was posted."""
    ig = cfg["instagram"]
    if not ig.get("reels", True):
        return False
    base = cfg["site"]["base_url"].rstrip("/")
    by_slug = {}
    for path in ARTICLES_DIR.glob("*.md"):
        a = read_article(path)
        if a and a.get("title"):
            by_slug[a.get("slug") or path.stem[11:]] = a
    for slug, rec in sorted(posted["posts"].items(), key=lambda kv: kv[1]["posted_at"]):
        if only and slug != only:
            continue
        if rec.get("reel_id") or rec.get("reel_skipped") or rec.get("reel_attempts", 0) >= 3 or slug not in by_slug:
            continue
        if ig.get("reels_need_photo", True) and not photo_on_instagram(by_slug[slug], cfg):
            rec["reel_skipped"] = "no photo"  # no Reel without a real picture
            save_json(POSTED_FILE, posted)
            save_and_push(f"No Reel for '{by_slug[slug]['title'][:60]}' (no usable photo)")
            continue
        if NOW - dt.datetime.fromisoformat(rec["posted_at"]) > dt.timedelta(days=float(ig.get("max_story_age_days", 3)) + 1):
            continue
        video = f"{base}/social/{slug}-reel.mp4"
        if not online(video):
            log(f"Reel for '{by_slug[slug]['title']}' isn't online yet.")
            continue
        log(f"Posting Reel: {by_slug[slug]['title']}")
        rec["reel_attempts"] = rec.get("reel_attempts", 0) + 1
        try:
            rec["reel_id"] = publish_reel(token, user_id, video, caption_for(by_slug[slug], cfg), ig)
            log("Reel posted.")
        except InstagramError as e:
            log(f"Reel didn't post this time ({e}); will try again later.")
        save_json(POSTED_FILE, posted)
        save_and_push(f"Instagram Reel: {by_slug[slug]['title']}")
        return bool(rec.get("reel_id"))
    return False


def post_story(token: str, user_id: str, cfg: dict, posted: dict, slug: str, title: str) -> None:
    """Puts the story's headline card on our Instagram Story (one try; Stories only last a day anyway)."""
    rec = posted["posts"].get(slug)
    if not cfg["instagram"].get("stories", True) or not rec or rec.get("story_id") or rec.get("story_tried"):
        return
    image = f"{cfg['site']['base_url'].rstrip('/')}/social/{slug}-story.jpg"
    if not online(image):
        return
    rec["story_tried"] = True
    try:
        container = api("POST", f"{user_id}/media", media_type="STORIES", image_url=image, access_token=token)["id"]
        wait_until_ready(token, container)
        rec["story_id"] = api("POST", f"{user_id}/media_publish", creation_id=container, access_token=token)["id"]
        log("Added to our Instagram Story.")
    except InstagramError as e:
        log(f"Couldn't add it to the Story ({e}).")
    save_json(POSTED_FILE, posted)
    save_and_push(f"Instagram Story: {title}")


def _caption_key(text: str) -> str:
    return " ".join((text or "").split())[:120].lower()


def already_on_instagram(token: str, user_id: str, caption: str) -> str | None:
    """Looks at our latest Instagram posts and returns the id of one with this caption, if any.
    This stops a story being posted twice even if our own notes didn't get saved last time."""
    try:
        recent = api("GET", f"{user_id}/media", fields="id,caption,timestamp", limit=25, access_token=token)
    except InstagramError as e:
        log(f"Couldn't check recent posts ({e}).")
        return None
    key = _caption_key(caption)
    for m in recent.get("data", []):
        if key and _caption_key(m.get("caption", "")) == key:
            return m["id"]
    return None


def online(url: str) -> bool:
    try:
        return requests.head(url, timeout=20, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False


def save_and_push(message: str) -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    subprocess.run(["git", "config", "user.name", "good-news-bot"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    files = [str(f) for f in (POSTED_FILE, TOKEN_FILE) if f.exists()]
    if not files:
        return
    subprocess.run(["git", "add", *files], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", message], check=True)
    for _ in range(4):
        pull = subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], capture_output=True, text=True)
        if pull.returncode != 0:
            log(f"git pull problem: {(pull.stderr or pull.stdout).strip()[:300]}")
            subprocess.run(["git", "rebase", "--abort"], capture_output=True)
        push = subprocess.run(["git", "push", "origin", "HEAD:main"], capture_output=True, text=True)
        if push.returncode == 0:
            return
        log(f"git push problem: {(push.stderr or push.stdout).strip()[:300]}")
        time.sleep(8)
    sys.exit("Couldn't save Instagram progress to GitHub.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="test the Instagram connection only")
    parser.add_argument("--now", action="store_true", help="ignore posting hours and spacing")
    args = parser.parse_args()

    cfg = load_config()
    ig = cfg.setdefault("instagram", {})
    if not ig.get("enabled") and not args.check:
        log("Instagram posting is switched off in config.yaml (instagram: enabled: false).")
        return

    posted = load_json(POSTED_FILE, {"last_post_at": None, "posts": {}})
    uk_now = NOW.astimezone(UK)
    start, end = ig.get("posting_hours", [8, 21])
    if not (args.check or args.now):
        if not (start <= uk_now.hour < end):
            log(f"Outside posting hours ({start}:00–{end}:00 UK time). Nothing to do.")
            return
        last = posted.get("last_post_at")
        gap = dt.timedelta(hours=float(ig.get("min_hours_between_posts", 3)))
        if last and NOW - dt.datetime.fromisoformat(last) < gap:
            pending = [k for k, v in posted["posts"].items() if not v.get("reel_id") and not v.get("reel_skipped") and v.get("reel_attempts", 0) < 3]
            if pending and ig.get("reels", True):
                token, token_changed = get_token()
                if token_changed:
                    save_and_push("Renew Instagram access token")
                me = api("GET", "me", fields="user_id,username", access_token=token)
                if post_pending_reel(token, me.get("user_id") or me.get("id"), cfg, posted):
                    return
            log("Posted recently; waiting before the next one.")
            return

    # Find the oldest recent story that hasn't been posted yet.
    max_age = dt.timedelta(days=float(ig.get("max_story_age_days", 3)))
    queue = []
    for path in sorted(ARTICLES_DIR.glob("*.md")):
        a = read_article(path)
        if not a or not a.get("title"):
            continue
        slug = a.get("slug") or path.stem[11:]
        published = published_at(path, a)
        if slug in posted["posts"] or NOW - published > max_age:
            continue
        queue.append((published, slug, a))
    queue.sort(key=lambda x: x[0])

    pending_reels = [k for k, v in posted["posts"].items() if not v.get("reel_id") and not v.get("reel_skipped") and v.get("reel_attempts", 0) < 3]
    if not queue and not args.check and not (pending_reels and ig.get("reels", True)):
        log("No new stories waiting for Instagram.")
        return

    token, token_changed = get_token()
    if token_changed:
        save_and_push("Renew Instagram access token")
    me = api("GET", "me", fields="user_id,username", access_token=token)
    user_id = me.get("user_id") or me.get("id")
    log(f"Connected to Instagram as @{me.get('username')}.")
    if args.check:
        return

    if not queue:
        if not post_pending_reel(token, user_id, cfg, posted):
            log("No new stories waiting for Instagram.")
        return

    base = cfg["site"]["base_url"].rstrip("/")
    for _, slug, a in queue:
        first = f"{base}/social/{slug}.jpg"
        if not online(first):
            log(f"Image for '{a['title']}' isn't online yet; will try again next hour.")
            continue
        images = [first]
        if ig.get("carousel", True):
            for n in (2, 3):
                extra = f"{base}/social/{slug}-{n}.jpg"
                if not online(extra):
                    break
                images.append(extra)
        caption = caption_for(a, cfg)
        media_id = already_on_instagram(token, user_id, caption)
        if media_id:
            log(f"'{a['title']}' is already on Instagram; recording it instead of posting again.")
        else:
            log(f"Posting: {a['title']} ({len(images)} image{'s' if len(images) > 1 else ''})")
            try:
                media_id = publish(token, user_id, images, caption)
            except Exception as e:  # noqa: BLE001 - includes Instagram being slow to reply (timeouts)
                time.sleep(20)
                # Instagram sometimes reports an error even though the post went up. Check before giving up.
                media_id = already_on_instagram(token, user_id, caption)
                if not media_id:
                    raise
                log(f"Instagram reported a problem ({e}), but the post is up; carrying on.")
        posted["posts"][slug] = {"media_id": media_id, "posted_at": NOW.replace(microsecond=0).isoformat()}
        posted["last_post_at"] = NOW.replace(microsecond=0).isoformat()
        # Keep the record tidy: forget posts older than 90 days.
        cutoff = (NOW - dt.timedelta(days=90)).isoformat()
        posted["posts"] = {k: v for k, v in posted["posts"].items() if v["posted_at"] >= cutoff}
        save_json(POSTED_FILE, posted)
        save_and_push(f"Posted to Instagram: {a['title']}")
        log("Posted.")
        # Also on our Story, for followers who tap through Stories rather than the feed.
        post_story(token, user_id, cfg, posted, slug, a["title"])
        # The same story as a Reel, so people who don't follow us yet can find it.
        post_pending_reel(token, user_id, cfg, posted, only=slug)
        return


if __name__ == "__main__":
    try:
        main()
    except InstagramError as e:
        sys.exit(str(e))
