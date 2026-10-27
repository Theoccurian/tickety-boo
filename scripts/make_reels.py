"""
Turns each story's three Instagram slides into a short vertical video (a Reel):
  public/social/<story>-reel.mp4   1080×1920, about 14 seconds, with a gentle original music bed
  public/social/<story>-story.jpg  1080×1920 still of the headline, for Instagram Stories

Instagram shows Reels to people who don't follow you far more than ordinary posts,
so this is how a new account gets discovered.

Finished videos are kept in .social-cache/ so each one is only made once.
Run after make_social_images.py (it calls this automatically).
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from common import ROOT, load_config

CARDS = ROOT / "build" / "cards"
CACHE = ROOT / ".social-cache"
OUT = ROOT / "public" / "social"
FONTS = (ROOT / "static" / "fonts").as_uri()

W, H, FPS = 1080, 1920, 30
SLIDE_SECONDS = (3.5, 4.5, 6.5)  # headline, In brief, Why it matters (more to read on the last ones)
FADE_SECONDS = 0.4
DESIGN_VERSION = "reel-v2"  # change to redraw every Reel

FRAME_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: "Bricolage Grotesque"; font-weight: 200 800; src: url("{fonts}/bricolage-grotesque.woff2") format("woff2"); }}
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: 1080px; height: 1920px; overflow: hidden; }}
body {{
  font-family: "Bricolage Grotesque", sans-serif; color: #fff;
  background: radial-gradient(120% 70% at 50% 18%, color-mix(in srgb, {colour} 70%, white) 0%, {colour} 55%, color-mix(in srgb, {colour} 75%, black) 100%);
  display: flex; flex-direction: column; align-items: center;
}}
.top {{ margin-top: 190px; display: flex; align-items: center; gap: 18px; font-weight: 800; font-size: 50px; letter-spacing: -0.025em; }}
.top svg {{ width: 76px; height: 76px; }}
.sub {{ margin-top: 14px; font-size: 30px; font-weight: 520; opacity: .9; letter-spacing: .01em; }}
.slide {{ margin-top: 52px; width: 920px; height: 1150px; border-radius: 34px; overflow: hidden;
  box-shadow: 0 30px 60px -20px rgba(0,0,0,.45), 0 0 0 1px rgba(255,255,255,.12); }}
.slide img {{ display: block; width: 100%; height: 100%; }}
.cta {{ margin-top: 48px; background: #F2AE1C; color: #132036; font-weight: 750; font-size: 34px; padding: 18px 34px; border-radius: 999px; }}
</style></head><body>
<div class="top"><svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="30" r="11" fill="#FFD266"/>
<g stroke="#FFD266" stroke-width="3.2" stroke-linecap="round"><path d="M24 6v6M8.4 14.4l4.2 4.2M39.6 14.4l-4.2 4.2M3 30h5M40 30h5"/></g>
<path d="M2 42h44" stroke="#fff" stroke-width="3.2" stroke-linecap="round"/></svg>{name}</div>
<p class="sub">{sub}</p>
<div class="slide"><img src="{slide}"></div>
<p class="cta">{cta}</p>
</body></html>"""


# ─── Music: a soft, original chord progression (no copyright worries) ────────

def _note(freq: float, t: np.ndarray) -> np.ndarray:
    return (np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t)
            + 0.5 * np.sin(2 * np.pi * freq * 1.003 * t))


def make_music(path: Path, seconds: float, rate: int = 44100) -> None:
    """Gentle pad + chime arpeggio in C major: C, G, Am, F."""
    midi = lambda n: 440 * 2 ** ((n - 69) / 12)  # noqa: E731
    chords = [(60, 64, 67), (59, 62, 67), (57, 60, 64), (57, 60, 65)]
    n = int(seconds * rate)
    out = np.zeros(n)
    chord_len = seconds / 4
    for i, chord in enumerate(chords):
        start, length = int(i * chord_len * rate), int((chord_len + 0.8) * rate)
        t = np.arange(min(length, n - start)) / rate
        env = np.minimum(1, t / 0.7) * np.minimum(1, np.maximum(0, (chord_len + 0.8 - t) / 0.9))
        pad = sum(_note(midi(m), t) for m in chord) * env * 0.07
        out[start:start + len(t)] += pad
        # chimes: chord notes an octave up, one every quarter of the chord
        for k in range(4):
            cs = start + int(k * chord_len / 4 * rate)
            ct = np.arange(min(int(1.6 * rate), n - cs)) / rate
            if len(ct) <= 0:
                continue
            f = midi(chord[k % 3] + 12 + (12 if k == 3 else 0))
            out[cs:cs + len(ct)] += np.sin(2 * np.pi * f * ct) * np.exp(-ct * 3.2) * 0.09
    fade = int(0.8 * rate)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    out = out / max(1e-9, np.abs(out).max()) * 0.32
    left = out
    right = np.roll(out, int(0.012 * rate))  # a touch of width
    pcm = (np.stack([left, right], axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ─── Video ───────────────────────────────────────────────────────────────────

def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def frames(stills: list[Image.Image]):
    fade = int(FADE_SECONDS * FPS)

    def zoomed(i: int, k: int) -> np.ndarray:
        per = int(SLIDE_SECONDS[i] * FPS)
        # slow push-in from 100% to 103% across each slide's time on screen
        z = 1 + 0.03 * (k / (per + fade))
        img = stills[i]
        cw, ch = int(W / z), int(H / z)
        x, y = (W - cw) // 2, (H - ch) // 2
        return np.asarray(img.crop((x, y, x + cw, y + ch)).resize((W, H), Image.BICUBIC), dtype=np.float32)

    for i in range(len(stills)):
        per = int(SLIDE_SECONDS[i] * FPS)
        for k in range(per):
            frame = zoomed(i, k)
            if i + 1 < len(stills) and k >= per - fade:
                a = (k - (per - fade) + 1) / (fade + 1)
                frame = frame * (1 - a) + zoomed(i + 1, k - (per - fade)) * a
            yield frame.astype(np.uint8).tobytes()


def make_reel(stills: list[Image.Image], out: Path, music: bool) -> None:
    seconds = sum(SLIDE_SECONDS[:len(stills)])
    tmp_audio = out.with_suffix(".wav")
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if music:
        make_music(tmp_audio, seconds)
        cmd += ["-i", str(tmp_audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-map", "0:v", "-map", "1:a", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for f in frames(stills):
            proc.stdin.write(f)
    finally:
        proc.stdin.close()
        proc.wait()
        tmp_audio.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg couldn't make the video")


def main() -> None:
    cfg = load_config()
    ig = cfg.get("instagram") or {}
    if not ig.get("reels", True):
        return
    manifest_file = CARDS / "manifest.json"
    if not manifest_file.exists():
        return
    manifest = json.loads(manifest_file.read_text())
    stories: dict[str, dict] = {}
    for m in manifest:
        if m.get("variant") in ("ig", "brief", "why") and m.get("slug"):
            s = stories.setdefault(m["slug"], {"colour": m.get("colour", "#2B62C4"), "section": m.get("section", ""), "slides": {}})
            s["slides"][m["variant"]] = m
    music = bool(ig.get("reels_music", True))
    site_name = cfg["site"]["name"]
    made = reused = 0
    keep = set()

    browser = None
    try:
        for slug, s in stories.items():
            slides = [s["slides"][v] for v in ("ig", "brief", "why") if v in s["slides"]]
            if len(slides) < 2:
                continue
            key = hashlib.md5("|".join([DESIGN_VERSION, str(music), s["colour"]] + [m["cache_key"] for m in slides]).encode()).hexdigest()[:10]
            cached = CACHE / f"{slug}-reel-{key}.mp4"
            story = CACHE / f"{slug}-story-{key}.jpg"
            keep.update({cached.name, story.name})
            if not (cached.exists() and story.exists()):
                if browser is None:
                    from playwright.sync_api import sync_playwright
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": W, "height": H})
                stills = []
                for n, m in enumerate(slides):
                    html_file = CACHE / f"{slug}-reelframe-{n}.html"
                    html_file.write_text(FRAME_HTML.format(
                        fonts=FONTS, colour=s["colour"], name=site_name,
                        sub=f"Good news &nbsp;·&nbsp; {s['section']}" if s["section"] else "Good news from around the world",
                        slide=(CACHE / m["cache_key"]).as_uri(), cta="Full story: link in bio"), encoding="utf-8")
                    page.goto(html_file.as_uri())
                    page.wait_for_function("document.images[0].complete && document.images[0].naturalWidth > 0", timeout=10000)
                    page.evaluate("document.fonts.ready")
                    page.wait_for_timeout(150)
                    tmp = CACHE / f"{slug}-reelframe-{n}.png"
                    page.screenshot(path=str(tmp))
                    stills.append(Image.open(tmp).convert("RGB"))
                    tmp.unlink()
                    html_file.unlink()
                page.close()
                stills[0].save(story, "JPEG", quality=90)  # the same headline frame, for Instagram Stories
                make_reel(stills, cached, music)
                made += 1
            else:
                reused += 1
            shutil.copyfile(cached, OUT / f"{slug}-reel.mp4")
            shutil.copyfile(story, OUT / f"{slug}-story.jpg")
    finally:
        if browser is not None:
            browser.close()
            pw.stop()

    for f in list(CACHE.glob("*-reel-*.mp4")) + list(CACHE.glob("*-story-*.jpg")):
        if f.name not in keep:
            f.unlink()
    print(f"Reels: {made} made, {reused} reused.")


if __name__ == "__main__":
    main()
