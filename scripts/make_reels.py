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
TIMELINE = [(0, 0.0, 4.2), (1, 4.2, 8.4), (2, 8.4, 12.6), (3, 12.6, 14.6)]
SECONDS = TIMELINE[-1][2]
FADE_IN, FADE_OUT, RISE = 0.3, 0.2, 34
PUNCH = 0.6          # seconds of quick push-in at the very start
WORD_GAP = 0.26      # the hook appears one word at a time
GROUP_GAP = 0.2      # the facts appear one word at a time, a little quicker as they're longer
WORD_FADE = 0.38     # how long each word takes to fade in
WORD_RISE = 14       # pixels each word glides up as it appears
BAR_SWEEP = 0.5      # the yellow bar under the hook sweeps in over this long
BANNER_MOVE = 0.5    # the intro banner drops in / slides away over this long
BANNER_OUT = 2.2     # when it starts sliding away
HOLD_HOOK = 1.6      # seconds the full hook stays on screen
HOLD_FACT = 1.5      # seconds each full fact stays on screen
END_CARD = 2.0


def set_timeline(r: dict) -> None:
    """Each part lasts as long as its words take to appear, plus time to read."""
    global TIMELINE, SECONDS
    beats = (r["beats"] + r["beats"])[:2]
    hook = 0.12 + len(r["hook"].split()) * WORD_GAP + BAR_SWEEP + HOLD_HOOK
    facts = [max(3.5, 0.1 + len(b.split()) * GROUP_GAP + HOLD_FACT) for b in beats]
    t, TIMELINE = 0.0, []
    for idx, d in enumerate([hook, *facts, END_CARD]):
        TIMELINE.append((idx, round(t, 2), round(t + d, 2)))
        t += d
    SECONDS = round(t, 2)
DESIGN_VERSION = "reel-v13"  # change to redraw every Reel

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
.end-handle {{ font-size: 34px; font-weight: 650; opacity: .92; margin-top: -10px; }}
.end-handle span {{ display: inline-block; min-width: 186px; font-weight: 500; opacity: .75; }}
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

def render_layers(page, r: dict, site_name: str, tmp: Path, page_hi=None, site_domain: str = "",
                  handles: list | None = None) -> tuple[Image.Image, list[dict]]:
    """Background (photo or illustration) plus one transparent text layer per moment in the video.
    The background is drawn at double resolution so the slow zoom stays sharp and smooth."""
    def shot(css: str, body: str, transparent: bool, pg=None, want_sun: bool = False, sun_sel: str = ".kicker svg"):
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
        if want_sun:
            rect = pg.evaluate("(sel) => { const r = document.querySelector(sel).getBoundingClientRect();"
                               " return [r.x, r.y, r.width, r.height]; }", sun_sel)
            return img, rect
        return img

    art = f'<img src="{r["photo"]}" alt="">' if r["photo"] else r["cover"]
    credit = f'<p class="credit">{html.escape(r["credit"])}</p>' if r.get("credit") else ""
    bg = shot(BG_CSS.format(colour=r["colour"]), f'<div class="art">{art}</div><div class="fade"></div>{credit}', False, page_hi).convert("RGB")

    kicker = f'<p class="kicker">{SUN}<span><b>Good news</b> &nbsp;·&nbsp; {html.escape(r["section"])}</span></p>'
    where = html.escape(r["location"]) if r.get("location") else ""

    css = TEXT_CSS.replace("{{", "{").replace("}}", "}")
    beats = (r["beats"] + r["beats"])[:2]
    handles_html = "".join(f'<p class="end-handle"><span>{html.escape(name)}</span> @{html.escape(h)}</p>'
                           for name, h in (handles or []))

    def block(text: str, cls: str, size: int, extra: str, show_kicker: bool, word: int | None,
              show_extra: bool, show_bar: bool = False) -> str:
        # The full layout is always present (so nothing ever moves); we choose what's visible.
        parts = html.escape(text).split()
        spans = " ".join(f'<span{"" if word == i else " class=h"}>{w}</span>' for i, w in enumerate(parts))
        k = kicker if show_kicker else kicker.replace('class="kicker"', 'class="kicker h"', 1)
        ex = extra if show_extra else extra.replace('<p class="', '<p class="h ', 1)
        return f'<div class="block">{k}<p class="{cls}{" done" if show_bar else ""}" style="font-size:{size}px">{spans}</p>{ex}</div>'

    def crop(img: Image.Image):
        box = img.getbbox()
        return (img.crop(box), (box[0], box[1])) if box else None

    def beat(text: str, cls: str, size: int, extra: str = "", animate_sun: bool = False) -> dict:
        n = len(text.split())
        sun = None
        if animate_sun:
            body = block(text, cls, size, extra, True, None, False).replace("<svg ", '<svg style="visibility:hidden" ', 1)
            base, sun = shot(css, body, True, want_sun=True)
        else:
            base = shot(css, block(text, cls, size, extra, True, None, False), True)
        word_layers = [crop(shot(css, block(text, cls, size, extra, False, i, False), True)) for i in range(n)]
        # After the last word: the yellow bar (swept in) and then the location, each on its own.
        bar = crop(shot(css, block(text, cls, size, extra, False, -1, False, True), True)) if cls == "hook" else None
        extra_layer = crop(shot(css, block(text, cls, size, extra, False, -1, True), True)) if extra else None
        return {"base": base, "words": word_layers, "bar": bar, "extra": extra_layer, "sun": sun}

    plan = [
        beat(r["hook"], "hook", hook_size(r["hook"]), f'<p class="where">{where}</p>' if where else ""),
        beat(beats[0], "fact", fact_size(beats[0])),
        beat(beats[1], "fact", fact_size(beats[1])),
        {"base": shot(css, f'<div class="block">{kicker}<p class="end-cta">Full story: link in bio</p>'
                           f'<p class="end-sub">{html.escape(site_domain or site_name)}</p>{handles_html}</div>', True),
         "words": [], "bar": None, "extra": None},
    ]
    # Intro banner: drops in, the sun rises inside it, then it slides away.
    banner_css = css + """
.banner { position: absolute; top: 200px; left: 50%; transform: translateX(-50%); display: flex; align-items: center;
  gap: 18px; padding: 18px 40px 18px 26px; border-radius: 999px; background: rgba(11,18,32,.66);
  box-shadow: 0 12px 30px -10px rgba(0,0,0,.5), 0 0 0 1.5px rgba(255,210,102,.35); white-space: nowrap;
  font-size: 50px; font-weight: 800; letter-spacing: -0.025em; }
.banner svg { width: 70px; height: 70px; visibility: hidden; }"""
    b_img, b_rect = shot(banner_css, f'<div class="banner">{SUN}<span>{html.escape(site_name)}</span></div>', True,
                         want_sun=True, sun_sel=".banner svg")
    box = b_img.getbbox()
    plan[0]["banner"] = (b_img.crop(box), (box[0], box[1]), (b_rect[0] - box[0], b_rect[1] - box[1], b_rect[2]))
    return bg, plan


SUN_COL, WHITE = (255, 210, 102), (255, 255, 255)
RAYS = [((24, 6), (24, 12)), ((8.4, 14.4), (12.6, 18.6)), ((39.6, 14.4), (35.4, 18.6)), ((3, 30), (8, 30)), ((40, 30), (45, 30))]
RAY_ORDER = [3, 1, 0, 2, 4]  # left, upper-left, top, upper-right, right: fanning across
_sun_cache: dict = {}


def _ease(p: float, power: int = 3) -> float:
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** power


def sun_icon(size: int, t: float) -> Image.Image:
    """The TBN sunrise mark at time t: the sun rises over the horizon, rays fan out, a warm glow
    blooms and settles. Drawn large and shrunk down so the edges stay smooth. Returns a padded
    image (room for the glow) whose centre square is the icon."""
    from PIL import ImageDraw, ImageFilter
    t = min(t, 2.2)  # settled after this, so later frames reuse the last drawing
    key = (size, round(t * FPS))
    if key in _sun_cache:
        return _sun_cache[key]
    ss = 8
    S = size * ss
    pad = size  # room around the icon for the glow (it spills a little beyond the banner, like light)
    canvas = Image.new("RGBA", (S + 2 * pad * ss, S + 2 * pad * ss), (0, 0, 0, 0))
    u = S / 48  # one unit of the 48-unit design
    ox = oy = pad * ss
    P = lambda x, y: (ox + x * u, oy + y * u)  # noqa: E731
    sw = 3.6 * u

    rise = _ease((t - 0.1) / 0.75)                 # sun climbs from below the horizon
    cy = 30 + (1 - rise) * 24
    glow = _ease((t - 0.45) / 0.45) * (1 - 0.6 * _ease((t - 1.2) / 0.9))  # blooms, then settles to a soft warmth

    # Glow: a soft blurred disc behind the sun
    if glow > 0.01:
        g = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(g)
        cx_, cy_ = P(24, cy)
        for r_, a_, blur in ((34, 150, 12), (19, 255, 6)):  # a wide soft halo plus a brighter inner glow
            g = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(g).ellipse((cx_ - r_ * u, cy_ - r_ * u, cx_ + r_ * u, cy_ + r_ * u),
                                      fill=(255, 190, 60, int(a_ * glow)))
            canvas.alpha_composite(g.filter(ImageFilter.GaussianBlur(blur * u)))

    # Sun disc, hidden below the horizon line until it rises
    disc = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    cx_, cy_ = P(24, cy)
    r = 11 * u
    dd.ellipse((cx_ - r, cy_ - r, cx_ + r, cy_ + r), fill=SUN_COL + (255,))
    horizon_y = P(0, 42)[1]
    disc.paste((0, 0, 0, 0), (0, int(horizon_y), disc.width, disc.height))
    canvas.alpha_composite(disc)

    d = ImageDraw.Draw(canvas)
    # Rays fan out one after another, growing from the sun outwards
    for n, i in enumerate(RAY_ORDER):
        p = _ease((t - 0.55 - n * 0.09) / 0.3)
        if p <= 0:
            continue
        (x1, y1), (x2, y2) = RAYS[i]
        sx, sy = x2 + (x1 - x2) * p, y2 + (y1 - y2) * p  # extends from the inner end
        a = int(255 * p)
        d.line([P(x2, y2), P(sx, sy)], fill=SUN_COL + (a,), width=round(sw))
        for (ex, ey) in ((x2, y2), (sx, sy)):
            cxp, cyp = P(ex, ey)
            d.ellipse((cxp - sw / 2, cyp - sw / 2, cxp + sw / 2, cyp + sw / 2), fill=SUN_COL + (a,))
    # Horizon line draws itself from the middle outwards
    h = _ease(t / 0.35)
    if h > 0:
        x1, x2 = 24 - 22 * h, 24 + 22 * h
        d.line([P(x1, 42), P(x2, 42)], fill=WHITE + (255,), width=round(sw))
        for ex in (x1, x2):
            cxp, cyp = P(ex, 42)
            d.ellipse((cxp - sw / 2, cyp - sw / 2, cxp + sw / 2, cyp + sw / 2), fill=WHITE + (255,))

    out = canvas.resize((size + 2 * pad, size + 2 * pad), Image.LANCZOS)
    _sun_cache[key] = out
    return out


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


def frame_at(bg: Image.Image, layers: list[dict], t: float) -> Image.Image:
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
        # Each word fades in on its own while gliding up a few pixels, eased so it settles gently.
        layer = layers[idx]
        reveal = start + (0.12 if idx == 0 else 0.1)
        gap = WORD_GAP if idx == 0 else GROUP_GAP
        frame.alpha_composite(_faded(layer["base"], a), dest=(0, dy))
        for i, item in enumerate(layer["words"]):
            p = (t - (reveal + i * gap)) / WORD_FADE
            if item is None or p <= 0:
                continue
            e = 1 - (1 - min(1.0, p)) ** 3
            img, (x, y) = item
            frame.alpha_composite(_faded(img, e * a), dest=(x, y + dy + round(WORD_RISE * (1 - e))))
        done_at = reveal + max(0, len(layer["words"]) - 1) * gap + WORD_FADE * 0.6
        if layer.get("bar"):
            # The yellow bar sweeps in from the left, fast then easing to a stop.
            p = (t - done_at) / BAR_SWEEP
            if p > 0:
                e = 1 - (1 - min(1.0, p)) ** 4
                img, (x, y) = layer["bar"]
                w = max(1, round(img.width * e))
                frame.alpha_composite(_faded(img.crop((0, 0, w, img.height)), a), dest=(x, y + dy))
        if layer.get("extra"):
            p = (t - done_at - BAR_SWEEP * 0.45) / WORD_FADE
            if p > 0:
                e = 1 - (1 - min(1.0, p)) ** 3
                img, (x, y) = layer["extra"]
                frame.alpha_composite(_faded(img, e * a), dest=(x, y + dy + round(WORD_RISE * (1 - e))))
    draw_banner(frame, layers[0].get("banner"), t)
    return frame.convert("RGB")


def draw_banner(frame: Image.Image, banner, t: float) -> None:
    if not banner or t > BANNER_OUT + BANNER_MOVE:
        return
    img, (x, y), (sx, sy, sw_) = banner
    travel = y + img.height + 40
    if t < BANNER_MOVE:  # drops in with a gentle overshoot
        p = t / BANNER_MOVE
        c = 1.4
        e = 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2
        off, a = -(1 - e) * travel, min(1.0, p * 2.5)
    elif t < BANNER_OUT:
        off, a = 0.0, 1.0
    else:  # slides back up and fades
        p = (t - BANNER_OUT) / BANNER_MOVE
        e = p * p * p
        off, a = -e * travel * 0.6, 1 - e
    yy = round(y + off)
    frame.alpha_composite(_faded(img, a), dest=(x, yy))
    icon = sun_icon(round(sw_), t - 0.25)
    pad = round(sw_)
    frame.alpha_composite(_faded(icon, a), dest=(round(x + sx) - pad, round(yy + sy) - pad))


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
           "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p",
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
    handles = []
    for name, key in (("Instagram", "instagram_url"), ("TikTok", "tiktok_url")):
        url = (cfg["site"].get(key) or "").rstrip("/")
        if url:
            handles.append((name, url.split("/")[-1].lstrip("@")))
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
            key = hashlib.md5((DESIGN_VERSION + str(music) + json.dumps(r, sort_keys=True) + str(handles)).encode()
                              + photo_bytes).hexdigest()[:10]
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
                set_timeline(r)
                bg, layers = render_layers(page, r, site_name, tmp, page_hi, site_domain, handles)
                cover_t = max(0.5, TIMELINE[0][2] - 0.4)  # the hook, fully shown, bar and all
                frame_at(bg, layers, cover_t).save(story, "JPEG", quality=90)
                story.with_suffix(".json").write_text(json.dumps({"cover_ms": int(cover_t * 1000)}))
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
            if story.with_suffix(".json").exists():
                shutil.copyfile(story.with_suffix(".json"), OUT / f"{r['slug']}-reel.json")
    finally:
        if browser is not None:
            browser.close()
            pw.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    for f in (list(CACHE.glob("*-reel-*.mp4")) + list(CACHE.glob("*-tiktok-*.mp4"))
              + list(CACHE.glob("*-story-*.jpg")) + list(CACHE.glob("*-story-*.json"))):
        if f.name not in keep and f.with_suffix(".jpg").name not in keep:
            f.unlink()
    print(f"Reels: {made} made, {reused} reused.")


if __name__ == "__main__":
    main()
