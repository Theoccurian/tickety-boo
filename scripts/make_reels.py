"""
Makes each story's Reel (short vertical video) and Instagram Story image:
  public/social/<story>-reel.mp4   1080×1920, about 11 seconds, with a gentle original music bed
  public/social/<story>-story.jpg  1080×1920 still of the opening frame, for Instagram Stories

Built to stop people scrolling: a full-width photo (or the story's sunrise illustration) that slowly
moves, a big hook line in the first second, then two short facts, then "Full story: link in bio".
Instagram shows Reels to people who don't follow you, so this is how a new account gets found.

Finished videos are kept in .social-cache/ so each one is only made once.
make_social_images.py runs this automatically.
"""
from __future__ import annotations

import hashlib
import html
import json
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
# (layer, starts at, ends at) in seconds. Layer 0 is the hook, then the two facts, then the ending.
TIMELINE = [(0, 0.0, 3.0), (1, 3.0, 6.2), (2, 6.2, 9.4), (3, 9.4, 11.4)]
SECONDS = TIMELINE[-1][2]
FADE_IN, FADE_OUT, RISE = 0.3, 0.2, 34
PUNCH = 0.6          # seconds of quick push-in at the very start
WORD_GAP = 0.14      # the hook appears one word at a time
GROUP_GAP = 0.12     # the facts appear two words at a time
DESIGN_VERSION = "reel-v5"  # change to redraw every Reel

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: "Bricolage Grotesque"; font-weight: 200 800; src: url("{fonts}/bricolage-grotesque.woff2") format("woff2"); }}
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: 1080px; height: 1920px; overflow: hidden; background: transparent; }}
body {{ font-family: "Bricolage Grotesque", sans-serif; color: #fff; -webkit-font-smoothing: antialiased; }}
{css}
</style></head><body>{body}</body></html>"""

BG_CSS = """
body {{ background: color-mix(in srgb, {colour} 38%, #0B1220); }}
.art {{ position: absolute; inset: 0 0 auto 0; height: 1240px; overflow: hidden; }}
.art img, .art svg {{ display: block; width: 100%; height: 100%; object-fit: cover; }}
.fade {{ position: absolute; inset: 0 0 auto 0; height: 1240px;
  background: linear-gradient(to bottom, rgba(0,0,0,.28) 0%, rgba(0,0,0,0) 18%, rgba(0,0,0,0) 48%, color-mix(in srgb, {colour} 38%, #0B1220) 100%); }}
.credit {{ position: absolute; left: 60px; top: 150px; font-size: 22px; font-weight: 500; opacity: .85; text-shadow: 0 1px 4px rgba(0,0,0,.6); max-width: 760px; }}
"""

TEXT_CSS = """
.block {{ position: absolute; left: 72px; width: 850px; bottom: 420px; display: flex; flex-direction: column; gap: 26px; }}
.kicker {{ align-self: flex-start; display: inline-flex; align-items: center; gap: 12px; padding: 12px 22px 12px 16px; border-radius: 999px;
  background: rgba(11,18,32,.55); font-size: 28px; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; }}
.kicker svg {{ width: 38px; height: 38px; }}
.kicker b {{ color: #FFD266; font-weight: 800; }}
.hook {{ font-weight: 800; line-height: 1.02; letter-spacing: -0.035em; text-wrap: balance; text-shadow: 0 3px 18px rgba(0,0,0,.35); }}
.hook::after {{ content: ""; display: block; width: 150px; height: 12px; margin-top: 26px; border-radius: 6px; background: #F2AE1C; visibility: hidden; }}
.hook.done::after {{ visibility: visible; }}
.h {{ visibility: hidden; }}
.fact {{ font-weight: 700; line-height: 1.16; letter-spacing: -0.02em; text-wrap: pretty; text-shadow: 0 2px 12px rgba(0,0,0,.35); }}
.where {{ font-size: 30px; font-weight: 600; opacity: .85; }}
.end-cta {{ align-self: flex-start; background: #F2AE1C; color: #132036; font-weight: 800; font-size: 60px; padding: 22px 40px; border-radius: 999px; letter-spacing: -0.02em; }}
.end-sub {{ font-size: 40px; font-weight: 650; }}
"""

SUN = ('<svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="30" r="11" fill="#FFD266"/>'
       '<g stroke="#FFD266" stroke-width="3.6" stroke-linecap="round"><path d="M24 6v6M8.4 14.4l4.2 4.2M39.6 14.4l-4.2 4.2M3 30h5M40 30h5"/></g>'
       '<path d="M2 42h44" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/></svg>')


def hook_size(text: str) -> int:
    n = len(text)
    return 112 if n <= 32 else 100 if n <= 45 else 88 if n <= 62 else 76


def fact_size(text: str) -> int:
    n = len(text)
    return 70 if n <= 70 else 62 if n <= 100 else 54


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


# ─── Drawing the layers ──────────────────────────────────────────────────────

def render_layers(page, r: dict, site_name: str, tmp: Path, page_hi=None, site_domain: str = "") -> tuple[Image.Image, list[list[Image.Image]]]:
    """Background (photo or illustration) plus one transparent text layer per moment in the video.
    The background is drawn at double resolution so the slow zoom stays sharp and smooth."""
    def shot(css: str, body: str, transparent: bool, pg=None) -> Image.Image:
        pg = pg or page
        f = tmp / "layer.html"
        f.write_text(PAGE.format(fonts=FONTS, css=css, body=body), encoding="utf-8")
        pg.goto(f.as_uri())
        pg.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)", timeout=15000)
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(120)
        png = tmp / "layer.png"
        pg.screenshot(path=str(png), omit_background=transparent)
        img = Image.open(png).convert("RGBA")
        img.load()
        return img

    art = f'<img src="{r["photo"]}" alt="">' if r["photo"] else r["cover"]
    credit = f'<p class="credit">{html.escape(r["credit"])}</p>' if r.get("credit") else ""
    bg = shot(BG_CSS.format(colour=r["colour"]), f'<div class="art">{art}</div><div class="fade"></div>{credit}', False, page_hi).convert("RGB")

    kicker = f'<p class="kicker">{SUN}<span><b>Good news</b> &nbsp;·&nbsp; {html.escape(r["section"])}</span></p>'
    where = html.escape(r["location"]) if r.get("location") else ""

    def words(text: str, shown: int) -> str:
        # Every word is laid out from the start (so nothing jumps); later ones are just hidden.
        parts = html.escape(text).split()
        return " ".join(f'<span{"" if i < shown else " class=h"}>{w}</span>' for i, w in enumerate(parts))

    def states(text: str, cls: str, size: int, per: int, extra_done: str = "") -> list[str]:
        n = len(text.split())
        steps = list(range(0, n, per)) + [n]
        return [f'<div class="block">{kicker}<p class="{cls}{" done" if k >= n else ""}" style="font-size:{size}px">'
                f'{words(text, k)}</p>{extra_done.replace("<p ", "<p data-x ", 1) if k >= n else extra_done.replace("<p class=\"", "<p class=\"h ", 1)}</div>'
                for k in steps]

    beats = (r["beats"] + r["beats"])[:2]
    css = TEXT_CSS.replace("{{", "{").replace("}}", "}")
    plan = [
        states(r["hook"], "hook", hook_size(r["hook"]), 1, f'<p class="where">{where}</p>' if where else ""),
        states(beats[0], "fact", fact_size(beats[0]), 2),
        states(beats[1], "fact", fact_size(beats[1]), 2),
        [f'<div class="block">{kicker}<p class="end-cta">Full story: link in bio</p><p class="end-sub">{html.escape(site_domain or site_name)}</p></div>'],
    ]
    layers = [[shot(css, body, True) for body in beat] for beat in plan]
    return bg, layers


def _faded(layer: Image.Image, a: float) -> Image.Image:
    if a >= 1:
        return layer
    out = layer.copy()
    out.putalpha(layer.getchannel("A").point(lambda v, a=a: int(v * a)))
    return out


def _zoom(t: float) -> float:
    """A quick push-in in the first moment (so something moves straight away), then a slow drift."""
    if t < PUNCH:
        p = t / PUNCH
        return 1 + 0.07 * (1 - (1 - p) ** 3)
    p = (t - PUNCH) / (SECONDS - PUNCH)
    return 1.07 + 0.05 * (p * p * (3 - 2 * p))


def frame_at(bg: Image.Image, layers: list[list[Image.Image]], t: float) -> Image.Image:
    # Worked out in fractions of a pixel on the double-resolution background, so it glides.
    z = _zoom(t)
    bw, bh = bg.size
    cw, ch = bw / z, bh / z
    x0, y0 = (bw - cw) / 2, (bh - ch) * 0.35
    frame = bg.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch)).convert("RGBA")
    for idx, start, end in TIMELINE:
        if not (start - 0.01 <= t < end + 0.01):
            continue
        a, dy = 1.0, 0
        if idx > 0 and t < start + FADE_IN:
            p = (t - start) / FADE_IN
            a, dy = p, int(RISE * (1 - p) ** 2)
        if idx < len(TIMELINE) - 1 and t > end - FADE_OUT:
            a = min(a, (end - t) / FADE_OUT)
        a = max(0.0, min(1.0, a))
        if a <= 0:
            continue
        # Words pop in a few at a time, like TikTok captions.
        steps = layers[idx]
        reveal = start + (0.12 if idx == 0 else 0.1)
        gap = WORD_GAP if idx == 0 else GROUP_GAP
        s_ = (t - reveal) / gap
        k = int(s_) if s_ > 0 else 0
        if k >= len(steps) - 1:
            layer = steps[-1]
        else:
            layer = steps[k].copy()
            frac = s_ - k if s_ > 0 else 0.0
            layer.alpha_composite(_faded(steps[k + 1], min(1.0, frac * 1.8)))
        layer = _faded(layer, a)
        if dy:
            shifted = Image.new("RGBA", (W, H))
            shifted.paste(layer, (0, dy))
            layer = shifted
        frame.alpha_composite(layer)
    return frame.convert("RGB")


# ─── Video ───────────────────────────────────────────────────────────────────

def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def make_reel(bg: Image.Image, layers: list[Image.Image], silent_out: Path, ig_out: Path | None, music: bool) -> None:
    """Writes the silent version (for TikTok, where you add a trending sound) and, if wanted,
    the Instagram version with the music bed (same pictures, no second encode)."""
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-tune", "film", "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-an", "-movflags", "+faststart", str(silent_out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for n in range(int(SECONDS * FPS)):
            proc.stdin.write(frame_at(bg, layers, n / FPS).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg couldn't make the video")
    if ig_out is None:
        return
    audio = ig_out.with_suffix(".wav")
    if music:
        make_music(audio, SECONDS)
        a_in = ["-i", str(audio)]
    else:
        a_in = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    res = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(silent_out), *a_in,
                          "-map", "0:v", "-map", "1:a", "-shortest", "-c:v", "copy",
                          "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(ig_out)])
    audio.unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg couldn't add the music")


def main() -> None:
    cfg = load_config()
    if not (cfg.get("instagram") or {}).get("reels", True):
        return
    data_file = CARDS / "reels.json"
    if not data_file.exists():
        return
    stories = json.loads(data_file.read_text())
    music = bool(cfg["instagram"].get("reels_music", True))
    site_name = cfg["site"]["name"]
    site_domain = cfg["site"].get("base_url", "").split("//")[-1].strip("/")
    CACHE.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / "reel-tmp"
    tmp.mkdir(exist_ok=True)
    made = reused = 0
    keep: set[str] = set()
    pw = browser = page = page_hi = None
    try:
        for r in stories:
            photo_bytes = b""
            if r["photo"].startswith("file://"):
                try:
                    photo_bytes = Path(r["photo"][7:]).read_bytes()
                except OSError:
                    r["photo"] = ""
            key = hashlib.md5((DESIGN_VERSION + str(music) + json.dumps(r, sort_keys=True)).encode() + photo_bytes).hexdigest()[:10]
            video = CACHE / f"{r['slug']}-reel-{key}.mp4"
            silent = CACHE / f"{r['slug']}-tiktok-{key}.mp4"
            story = CACHE / f"{r['slug']}-story-{key}.jpg"
            keep.update({video.name, silent.name, story.name})
            want_video = r.get("video", True)
            if not (story.exists() and (not want_video or (silent.exists() and video.exists()))):
                if page is None:
                    from playwright.sync_api import sync_playwright
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch()
                    page = browser.new_page(viewport={"width": W, "height": H})
                    page_hi = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
                bg, layers = render_layers(page, r, site_name, tmp, page_hi, site_domain)
                frame_at(bg, layers, 2.6).save(story, "JPEG", quality=90)  # the hook, fully shown
                if want_video:
                    make_reel(bg, layers, silent, video, music)
                made += 1
            else:
                reused += 1
            if want_video and r.get("ig", True):
                shutil.copyfile(video, OUT / f"{r['slug']}-reel.mp4")
            if want_video and r.get("tiktok"):
                shutil.copyfile(silent, OUT / f"{r['slug']}-tiktok.mp4")
            shutil.copyfile(story, OUT / f"{r['slug']}-story.jpg")
    finally:
        if browser is not None:
            browser.close()
            pw.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    for f in list(CACHE.glob("*-reel-*.mp4")) + list(CACHE.glob("*-tiktok-*.mp4")) + list(CACHE.glob("*-story-*.jpg")):
        if f.name not in keep:
            f.unlink()
    print(f"Reels: {made} made, {reused} reused.")


if __name__ == "__main__":
    main()
