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
FADE_IN, FADE_OUT, RISE = 0.35, 0.2, 34
DESIGN_VERSION = "reel-v3"  # change to redraw every Reel

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
.hook::after {{ content: ""; display: block; width: 150px; height: 12px; margin-top: 26px; border-radius: 6px; background: #F2AE1C; }}
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

def render_layers(page, r: dict, site_name: str, tmp: Path) -> tuple[Image.Image, list[Image.Image]]:
    """Background (photo or illustration) plus one transparent text layer per moment in the video."""
    def shot(css: str, body: str, transparent: bool) -> Image.Image:
        f = tmp / "layer.html"
        f.write_text(PAGE.format(fonts=FONTS, css=css, body=body), encoding="utf-8")
        page.goto(f.as_uri())
        page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)", timeout=15000)
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(120)
        png = tmp / "layer.png"
        page.screenshot(path=str(png), omit_background=transparent)
        img = Image.open(png).convert("RGBA")
        img.load()
        return img

    art = f'<img src="{r["photo"]}" alt="">' if r["photo"] else r["cover"]
    credit = f'<p class="credit">{html.escape(r["credit"])}</p>' if r.get("credit") else ""
    bg = shot(BG_CSS.format(colour=r["colour"]), f'<div class="art">{art}</div><div class="fade"></div>{credit}', False).convert("RGB")

    kicker = f'<p class="kicker">{SUN}<span><b>Good news</b> &nbsp;·&nbsp; {html.escape(r["section"])}</span></p>'
    where = f'<p class="where">{html.escape(r["location"])}</p>' if r.get("location") else ""
    hook = html.escape(r["hook"])
    beats = [html.escape(b) for b in (r["beats"] + r["beats"])[:2]]
    bodies = [
        f'<div class="block">{kicker}<p class="hook" style="font-size:{hook_size(r["hook"])}px">{hook}</p>{where}</div>',
        f'<div class="block">{kicker}<p class="fact" style="font-size:{fact_size(r["beats"][0])}px">{beats[0]}</p></div>',
        f'<div class="block">{kicker}<p class="fact" style="font-size:{fact_size(beats[1])}px">{beats[1]}</p></div>',
        f'<div class="block">{kicker}<p class="end-cta">Full story: link in bio</p><p class="end-sub">{html.escape(site_name)}</p></div>',
    ]
    layers = [shot(TEXT_CSS.replace("{{", "{").replace("}}", "}"), b, True) for b in bodies]
    return bg, layers


def frame_at(bg: Image.Image, layers: list[Image.Image], t: float) -> Image.Image:
    z = 1 + 0.06 * (t / SECONDS)  # slow push-in on the photo for the whole video
    cw, ch = int(W / z), int(H / z)
    x, y = (W - cw) // 2, int((H - ch) * 0.35)
    frame = bg.crop((x, y, x + cw, y + ch)).resize((W, H), Image.BICUBIC).convert("RGBA")
    for idx, start, end in TIMELINE:
        if not (start - 0.01 <= t < end + 0.01):
            continue
        a, dy = 1.0, 0
        if idx > 0 and t < start + FADE_IN:  # the first line is on screen from frame one
            p = (t - start) / FADE_IN
            a, dy = p, int(RISE * (1 - p) ** 2)
        if idx < len(TIMELINE) - 1 and t > end - FADE_OUT:
            a = min(a, (end - t) / FADE_OUT)
        a = max(0.0, min(1.0, a))
        if a <= 0:
            continue
        layer = layers[idx]
        if a < 1:
            layer = layer.copy()
            layer.putalpha(layer.getchannel("A").point(lambda v, a=a: int(v * a)))
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


def make_reel(bg: Image.Image, layers: list[Image.Image], out: Path, music: bool) -> None:
    tmp_audio = out.with_suffix(".wav")
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if music:
        make_music(tmp_audio, SECONDS)
        cmd += ["-i", str(tmp_audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-map", "0:v", "-map", "1:a", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for n in range(int(SECONDS * FPS)):
            proc.stdin.write(frame_at(bg, layers, n / FPS).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        tmp_audio.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg couldn't make the video")


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
    CACHE.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / "reel-tmp"
    tmp.mkdir(exist_ok=True)
    made = reused = 0
    keep: set[str] = set()
    pw = browser = page = None
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
            story = CACHE / f"{r['slug']}-story-{key}.jpg"
            keep.update({video.name, story.name})
            if not (video.exists() and story.exists()):
                if page is None:
                    from playwright.sync_api import sync_playwright
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch()
                    page = browser.new_page(viewport={"width": W, "height": H})
                bg, layers = render_layers(page, r, site_name, tmp)
                frame_at(bg, layers, 1.0).save(story, "JPEG", quality=90)
                make_reel(bg, layers, video, music)
                made += 1
            else:
                reused += 1
            shutil.copyfile(video, OUT / f"{r['slug']}-reel.mp4")
            shutil.copyfile(story, OUT / f"{r['slug']}-story.jpg")
    finally:
        if browser is not None:
            browser.close()
            pw.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    for f in list(CACHE.glob("*-reel-*.mp4")) + list(CACHE.glob("*-story-*.jpg")):
        if f.name not in keep:
            f.unlink()
    print(f"Reels: {made} made, {reused} reused.")


if __name__ == "__main__":
    main()
