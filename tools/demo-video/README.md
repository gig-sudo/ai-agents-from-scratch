# Demo video maker

Turns a silent screen recording (1-3 min) into a ~30 second captioned video for LinkedIn.
No voiceover: a title card, one captioned step per key moment, sped-up dead time, optional zoom, an outro card and a progress bar.

## Setup

```bash
pip install imageio-ffmpeg pillow numpy   # ffmpeg is picked up from PATH, or from imageio-ffmpeg
```

## Workflow

```bash
# 1. Find the busy parts of the recording, extract a keyframe per step, draft steps.json
python tools/demo-video/make_demo.py analyze recording.mp4 --out work

# 2. Look at work/step*.jpg and edit work/steps.json (title, captions, which steps to keep)

# 3. Check a single frame quickly (output time in seconds), then render
python tools/demo-video/make_demo.py render recording.mp4 work/steps.json --still 12 -o check.png
python tools/demo-video/make_demo.py render recording.mp4 work/steps.json -o demo.mp4
```

Tip: with Claude Code, share the keyframes and a one-line description of the demo and ask it to write `steps.json`.

## steps.json

```json
{
  "title": "Build an AI agent from scratch in 30 seconds",
  "subtitle": "A quick demo of the workflow",
  "outro": "Follow for more AI agent demos",
  "outro_sub": "Link to the repo in the comments",
  "target_seconds": 30,
  "format": "4:5",
  "accent": "#0A66C2",
  "segments": [
    {"start": 3.9, "end": 16.1, "caption": "Define the agent's goal and tools"},
    {"start": 23.9, "end": 36.1, "caption": "Watch it plan and call tools", "zoom": [0.25, 0.4, 1.8]},
    {"start": 43.9, "end": 56.1, "caption": "Review the result", "speed": 2}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `target_seconds` | Final length. Segments without `speed`/`duration` share the time left after the title and outro cards, so speed is picked automatically. |
| `format` | `4:5` (1080x1350, default), `1:1` or `16:9`. Override with `--format`. |
| `intro_seconds` / `outro_seconds` | Card lengths (default 2 and 2.5). Set to 0 to drop a card. |
| `segments[].start/end` | Seconds in the source recording. |
| `segments[].caption` | Text shown under the video. Keep it under about 60 characters. |
| `segments[].speed` | Fixed speed multiplier (never below 1x). |
| `segments[].duration` | Fixed on-screen seconds instead of speed. |
| `segments[].zoom` | `[centre_x, centre_y, scale]`, centre as 0-1 fractions, scale 1 or more. |
| `segments[].label` | Replaces the "Step N" badge. |
| `accent`, `font` | Brand colour and a `.ttf` path. |

The script warns if the result is more than 10% off the target or a caption is on screen for under 2 seconds.

## Posting on LinkedIn

Output is H.264/AAC MP4 with a silent audio track. Most people watch muted, so the captions carry the story. 4:5 takes the most feed space on mobile.
