#!/usr/bin/env python3
"""Turn a silent screen recording into a short, captioned LinkedIn demo video.

    make_demo.py analyze recording.mp4            # find the active moments, draft steps.json
    make_demo.py render  recording.mp4 steps.json # build the captioned video

See README.md for the workflow and the steps.json format.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Missing dependencies. Run: pip install imageio-ffmpeg pillow numpy")

FORMATS = {"4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}
FPS = 30
BG_TOP, BG_BOTTOM = (14, 18, 28), (24, 32, 48)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
]


# --------------------------------------------------------------------------- ffmpeg helpers

def ffmpeg_exe():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found. Install it, or run: pip install imageio-ffmpeg")


def probe(path):
    """Return (duration_s, width, height) by parsing `ffmpeg -i` output."""
    out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    size = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", out)
    if not dur or not size:
        sys.exit(f"Could not read video info from {path}:\n{out[-500:]}")
    h, m, s = dur.groups()
    return int(h) * 3600 + int(m) * 60 + float(s), int(size.group(1)), int(size.group(2))


def read_frames(path, start, length, rate, width, height):
    """Yield RGB frames (H, W, 3) sampled `rate` times per source second."""
    cmd = [ffmpeg_exe(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-i", str(path), "-vf", f"fps={rate:.5f},scale={width}:{height}",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = width * height * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                return
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


# --------------------------------------------------------------------------- analyze

def activity_curve(path, duration, width, height, rate=2.0):
    """Mean absolute frame difference per sample: how much the screen changes."""
    w = 160
    h = max(2, round(w * height / width))
    prev, diffs = None, []
    for fr in read_frames(path, 0, duration, rate, w, h):
        gray = fr.mean(axis=2)
        diffs.append(0.0 if prev is None else float(np.abs(gray - prev).mean()))
        prev = gray
    return np.array(diffs), rate


def propose_segments(diffs, rate, duration, max_segments, gap=2.0, pad=0.6):
    """Group the busy parts of the recording into at most `max_segments` spans."""
    if len(diffs) == 0:
        return [(0.0, duration)]
    smooth = np.convolve(diffs, np.ones(3) / 3, mode="same")
    thresh = max(0.25, 0.3 * float(np.percentile(smooth, 90)))
    active = smooth > thresh
    spans, start, last = [], None, None
    for i, on in enumerate(active):
        t = i / rate
        if on:
            if start is None:
                start = t
            elif t - last > gap:
                spans.append((start, last))
                start = t
            last = t
    if start is not None:
        spans.append((start, last))
    spans = [(max(0.0, a - pad), min(duration, b + pad)) for a, b in spans if b - a >= 0.5]
    if not spans:
        step = duration / max_segments
        return [(i * step, (i + 1) * step) for i in range(max_segments)]
    if len(spans) > max_segments:
        def energy(span):
            a, b = int(span[0] * rate), int(span[1] * rate) + 1
            return float(smooth[a:b].sum())
        spans = sorted(sorted(spans, key=energy, reverse=True)[:max_segments])
    return spans


def cmd_analyze(args):
    src = Path(args.input)
    duration, w, h = probe(src)
    out = Path(args.out or src.with_suffix("").name + "_work")
    out.mkdir(parents=True, exist_ok=True)
    print(f"Source: {w}x{h}, {duration:.1f}s. Analysing activity...")
    diffs, rate = activity_curve(src, duration, w, h)
    spans = propose_segments(diffs, rate, duration, args.max_segments)

    steps = []
    for i, (a, b) in enumerate(spans, 1):
        mid = (a + b) / 2
        frame = out / f"step{i:02d}.jpg"
        subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", f"{mid:.2f}", "-i", str(src),
                        "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", str(frame)], check=True)
        steps.append({"start": round(a, 1), "end": round(b, 1),
                      "caption": f"TODO: describe step {i}"})
        print(f"  step {i}: {a:6.1f}s -> {b:6.1f}s  ({b - a:.1f}s)  keyframe: {frame}")

    draft = {"title": "TODO: hook title", "subtitle": "TODO: one-line subtitle",
             "outro": "TODO: call to action", "target_seconds": 30, "format": "4:5",
             "segments": steps}
    (out / "steps.json").write_text(json.dumps(draft, indent=2) + "\n")
    print(f"\nDraft written to {out / 'steps.json'}")
    print("Next: look at the keyframes, fill in the captions, then run `render`.")


# --------------------------------------------------------------------------- drawing

def load_font(size, path=None):
    for cand in ([path] if path else []) + FONT_CANDIDATES:
        if cand and os.path.exists(cand):
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default(size)


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def wrap(draw, text, font, max_w):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def fit_text(draw, text, max_w, max_h, start_size, font_path, max_lines):
    """Largest font size (down to 60% of start) whose wrapped text fits the box."""
    size = start_size
    while True:
        font = load_font(size, font_path)
        lines = wrap(draw, text, font, max_w)
        line_h = int(size * 1.25)
        if (len(lines) <= max_lines and len(lines) * line_h <= max_h) or size <= start_size * 0.6:
            return font, lines, line_h
        size -= 2


def draw_centered(draw, lines, font, line_h, cx, top, fill):
    for i, line in enumerate(lines):
        draw.text((cx, top + i * line_h), line, font=font, fill=fill, anchor="mt")


def make_background(w, h):
    t = np.linspace(0, 1, h)[:, None, None]
    top, bottom = np.array(BG_TOP), np.array(BG_BOTTOM)
    grad = (top + (bottom - top) * t).astype(np.uint8)
    return Image.fromarray(np.repeat(grad, w, axis=1), "RGB")


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


class Layout:
    """Where the recording and caption live on the canvas."""

    def __init__(self, fmt, src_w, src_h, font_path, accent):
        self.W, self.H = FORMATS[fmt]
        self.font_path, self.accent = font_path, accent
        self.margin = round(self.W * 0.04)
        scale = (self.W - 2 * self.margin) / src_w
        scale = min(scale, (self.H * (0.62 if fmt != "16:9" else 0.72)) / src_h)
        self.vw, self.vh = round(src_w * scale), round(src_h * scale)
        gap = round(self.H * 0.03)
        self.cap_h = round(self.H * (0.2 if fmt != "16:9" else 0.16))
        block = self.vh + gap + self.cap_h
        self.vx = (self.W - self.vw) // 2
        self.vy = (self.H - block) // 2
        self.cap_top = self.vy + self.vh + gap
        self.bg = make_background(self.W, self.H)
        self.mask = rounded_mask((self.vw, self.vh), round(self.W * 0.012))
        border = Image.new("RGBA", (self.vw + 4, self.vh + 4), (0, 0, 0, 0))
        ImageDraw.Draw(border).rounded_rectangle(
            (0, 0, self.vw + 3, self.vh + 3), round(self.W * 0.014), outline=(255, 255, 255, 40), width=2)
        self.border = border

    def caption_layer(self, label, text):
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        badge_font = load_font(round(self.W * 0.026), self.font_path)
        d.text((self.W // 2, self.cap_top), label.upper(), font=badge_font,
               fill=self.accent + (255,), anchor="mt")
        text_top = self.cap_top + round(self.W * 0.05)
        font, lines, line_h = fit_text(d, text, self.W - 2 * self.margin - 20,
                                       self.cap_h - (text_top - self.cap_top),
                                       round(self.W * 0.055), self.font_path, 3)
        draw_centered(d, lines, font, line_h, self.W // 2, text_top, (255, 255, 255, 255))
        return layer

    def card(self, title, subtitle):
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        max_w = self.W - 2 * self.margin - 40
        font, lines, line_h = fit_text(d, title, max_w, self.H * 0.4, round(self.W * 0.085),
                                       self.font_path, 4)
        block_h = len(lines) * line_h
        sub_font, sub_lines, sub_h = fit_text(d, subtitle or "", max_w, self.H * 0.2,
                                              round(self.W * 0.04), self.font_path, 3)
        sub_total = len(sub_lines) * sub_h if subtitle else 0
        gap = round(self.H * 0.04) if subtitle else 0
        top = (self.H - block_h - gap - sub_total) // 2
        bar_w = round(self.W * 0.14)
        d.rounded_rectangle((self.W // 2 - bar_w // 2, top - round(self.H * 0.05),
                             self.W // 2 + bar_w // 2, top - round(self.H * 0.05) + 8),
                            4, fill=self.accent + (255,))
        draw_centered(d, lines, font, line_h, self.W // 2, top, (255, 255, 255, 255))
        if subtitle:
            draw_centered(d, sub_lines, sub_font, sub_h, self.W // 2, top + block_h + gap,
                          (190, 200, 220, 255))
        return layer


def fade(layer, a):
    if a >= 0.999:
        return layer
    out = layer.copy()
    alpha = out.getchannel("A").point(lambda v: int(v * a))
    out.putalpha(alpha)
    return out


def smoothstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


# --------------------------------------------------------------------------- render

def plan_segments(cfg, content_seconds):
    """Give every segment an output duration and a speed multiplier."""
    segs = []
    for s in cfg["segments"]:
        src_len = s["end"] - s["start"]
        if src_len <= 0:
            sys.exit(f"Segment has end <= start: {s}")
        dur = src_len / s["speed"] if s.get("speed") else s.get("duration")
        segs.append({**s, "src_len": src_len, "dur": dur})
    fixed = sum(s["dur"] for s in segs if s["dur"])
    flex = [s for s in segs if not s["dur"]]
    if flex:
        left = max(content_seconds - fixed, 1.5 * len(flex))
        total_w = sum(math.sqrt(s["src_len"]) for s in flex)
        for s in flex:
            s["dur"] = left * math.sqrt(s["src_len"]) / total_w
    for s in segs:
        s["speed"] = max(s["src_len"] / s["dur"], 1.0)
        s["dur"] = s["src_len"] / s["speed"]
    return segs


def zoom_box(zoom):
    """zoom = [cx, cy, scale] (normalised centre, scale >= 1)."""
    if not zoom:
        return (0.5, 0.5, 1.0)
    cx, cy, sc = zoom
    return (cx, cy, max(sc, 1.0))


def render_frames(src, cfg, layout, segs, intro_s, outro_s, total_s, src_w, src_h):
    n_intro, n_outro = round(intro_s * FPS), round(outro_s * FPS)
    n_total = round(total_s * FPS)
    accent = layout.accent
    sw = min(src_w, 1920) // 2 * 2
    sh = round(sw * src_h / src_w) // 2 * 2
    bar_h = 8
    emitted = 0

    def finish(img):
        nonlocal emitted
        emitted += 1
        d = ImageDraw.Draw(img)
        d.rectangle((0, layout.H - bar_h, round(layout.W * emitted / n_total), layout.H), fill=accent)
        return img

    def card_frames(layer, n):
        for i in range(n):
            a = min(1.0, i / FPS / 0.3, (n - 1 - i) / FPS / 0.3)
            img = layout.bg.convert("RGBA")
            img.alpha_composite(fade(layer, max(a, 0.0)))
            yield finish(img.convert("RGB"))

    if n_intro:
        yield from card_frames(layout.card(cfg["title"], cfg.get("subtitle", "")), n_intro)

    prev = (0.5, 0.5, 1.0)
    for idx, s in enumerate(segs, 1):
        n = round(s["dur"] * FPS)
        cap = layout.caption_layer(s.get("label", f"Step {idx}"), s["caption"])
        target = zoom_box(s.get("zoom"))
        reader = read_frames(src, s["start"], s["src_len"], FPS / s["speed"], sw, sh)
        last = None
        for i in range(n):
            frame = next(reader, last)
            if frame is None:
                break
            last = frame
            k = smoothstep(i / FPS / 0.8)
            cx, cy, sc = (p + (q - p) * k for p, q in zip(prev, target))
            bw, bh = sw / sc, sh / sc
            x0 = min(max(cx * sw - bw / 2, 0), sw - bw)
            y0 = min(max(cy * sh - bh / 2, 0), sh - bh)
            vid = Image.fromarray(frame).resize((layout.vw, layout.vh), Image.BILINEAR,
                                                box=(x0, y0, x0 + bw, y0 + bh))
            img = layout.bg.copy().convert("RGBA")
            img.alpha_composite(layout.border, (layout.vx - 2, layout.vy - 2))
            img.paste(vid, (layout.vx, layout.vy), layout.mask)
            a = min(1.0, (i / FPS - 0.25) / 0.35, (n - 1 - i) / FPS / 0.2)
            img.alpha_composite(fade(cap, max(a, 0.0)))
            yield finish(img.convert("RGB"))
        reader.close()
        prev = target

    if n_outro:
        yield from card_frames(layout.card(cfg.get("outro", "Thanks for watching"),
                                           cfg.get("outro_sub", "")), n_outro)
    while emitted < n_total:  # rounding can leave us a frame short
        yield finish(layout.bg.copy())


def cmd_render(args):
    src = Path(args.input)
    cfg = json.loads(Path(args.steps).read_text())
    for field in ("title", "segments"):
        if field not in cfg:
            sys.exit(f"steps.json is missing '{field}'")
    for i, s in enumerate(cfg["segments"], 1):
        if "start" not in s or "end" not in s or not s.get("caption"):
            sys.exit(f"Segment {i} needs start, end and caption")
        if str(s["caption"]).startswith("TODO"):
            sys.exit(f"Segment {i} still has a TODO caption. Fill it in first.")
    if str(cfg["title"]).startswith("TODO"):
        sys.exit("The title is still a TODO. Fill it in first.")

    fmt = args.format or cfg.get("format", "4:5")
    if fmt not in FORMATS:
        sys.exit(f"Unknown format {fmt}. Choose from {', '.join(FORMATS)}")
    duration, src_w, src_h = probe(src)
    for s in cfg["segments"]:
        if s["end"] > duration + 0.5 or s["start"] < 0:
            sys.exit(f"Segment {s['start']}-{s['end']}s is outside the {duration:.1f}s recording")
        s["end"] = min(s["end"], duration)

    intro_s = cfg.get("intro_seconds", 2.0)
    outro_s = cfg.get("outro_seconds", 2.5)
    target = cfg.get("target_seconds", 30)
    segs = plan_segments(cfg, target - intro_s - outro_s)
    total = intro_s + outro_s + sum(s["dur"] for s in segs)

    accent = hex_rgb(cfg.get("accent", "#0A66C2"))
    layout = Layout(fmt, src_w, src_h, args.font or cfg.get("font"), accent)

    print(f"Format {fmt} ({layout.W}x{layout.H}), target {target}s, planned {total:.1f}s")
    for i, s in enumerate(segs, 1):
        print(f"  step {i}: {s['start']:.1f}-{s['end']:.1f}s at {s['speed']:.1f}x -> {s['dur']:.1f}s  {s['caption'][:50]}")
        if s["dur"] < 2.0:
            print(f"    warning: only {s['dur']:.1f}s on screen, the caption may be hard to read")
    if abs(total - target) > 0.1 * target:
        print(f"  warning: total {total:.1f}s is more than 10% off the {target}s target "
              f"(some segments are fixed or capped at 1x speed)")

    frames = render_frames(src, cfg, layout, segs, intro_s, outro_s, total, src_w, src_h)

    if args.still is not None:
        idx = round(args.still * FPS)
        for i, img in enumerate(frames):
            if i == idx:
                out = Path(args.output or "still.png")
                img.save(out)
                print(f"Saved frame at {args.still}s to {out}")
                return
        sys.exit(f"--still {args.still}s is past the end of the video ({total:.1f}s)")

    out = Path(args.output or src.with_suffix("").name + "_demo.mp4")
    cmd = [ffmpeg_exe(), "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{layout.W}x{layout.H}", "-r", str(FPS), "-i", "-",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-movflags", "+faststart",
           "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for img in frames:
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait() != 0:
        sys.exit("ffmpeg failed while encoding")
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {total:.1f}s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="find active moments, extract keyframes, draft steps.json")
    a.add_argument("input")
    a.add_argument("--out", help="work directory (default: <input>_work)")
    a.add_argument("--max-segments", type=int, default=6)
    a.set_defaults(fn=cmd_analyze)

    r = sub.add_parser("render", help="render the captioned video from steps.json")
    r.add_argument("input")
    r.add_argument("steps")
    r.add_argument("-o", "--output")
    r.add_argument("--format", choices=list(FORMATS), help="override the format in steps.json")
    r.add_argument("--font", help="path to a .ttf/.ttc font")
    r.add_argument("--still", type=float, metavar="SECONDS",
                   help="save one PNG at this output time instead of rendering the video")
    r.set_defaults(fn=cmd_render)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
