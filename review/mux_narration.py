"""Assemble final audio (voiceover placed by timeline + side-chain ducked music)
and mux it onto the recorded video.

    MUSIC=/path/to.mp3 uv run python -m review.mux_narration <video.webm> <out.mp4>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LEAD = 0.10  # small global offset so VO doesn't pre-empt the visual


def dur(path: str) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path], capture_output=True, text=True).stdout.strip())


def main() -> None:
    video = sys.argv[1] if len(sys.argv) > 1 else sorted(
        Path("demo_video").glob("*.webm"))[-1].as_posix()
    out = sys.argv[2] if len(sys.argv) > 2 else "assets/demo.mp4"
    music = os.getenv("MUSIC", "/home/ieqr/Downloads/alexguz-funk-amp-breakbeat-541097.mp3")

    timeline = json.loads(Path("demo_video/timeline.json").read_text())
    manifest = json.loads(Path("assets/vo/manifest.json").read_text())
    vdur = dur(video)

    inputs = ["-i", video, "-i", music]
    for m in manifest:
        inputs += ["-i", m["file"]]

    parts = []
    labels = []
    for i, m in enumerate(manifest):
        idx = i + 2  # inputs 0=video,1=music
        delay = max(0, int((timeline.get(m["key"], 0) + LEAD) * 1000))
        parts.append(f"[{idx}]aresample=44100,aformat=channel_layouts=stereo,"
                     f"adelay={delay}|{delay}[a{i}]")
        labels.append(f"[a{i}]")
    n = len(manifest)
    parts.append("".join(labels) + f"amix=inputs={n}:normalize=0:dropout_transition=0[narr]")
    # split narration: one copy mixed on top, one used as the duck sidechain.
    # Boost voice well above the bed, with a limiter to avoid clipping.
    parts.append("[narr]volume=3.0,alimiter=limit=0.97,asplit=2[narrsc][narrmix]")
    parts.append("[1]aresample=44100,aformat=channel_layouts=stereo,"
                 "loudnorm=I=-26:TP=-3:LRA=11[mus]")
    # duck the music hard whenever narration is present
    parts.append("[mus][narrsc]sidechaincompress=threshold=0.02:ratio=14:attack=8:release=320[musd]")
    parts.append("[musd][narrmix]amix=inputs=2:normalize=0[mix]")
    parts.append(f"[mix]afade=t=out:st={vdur-3:.2f}:d=3,atrim=0:{vdur:.2f}[aout]")
    # VP8 webm -> H.264 for mp4
    parts.append("[0:v]scale=1280:720:flags=lanczos,fps=30,format=yuv420p[v]")
    fc = ";".join(parts)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", fc, "-map", "[v]", "-map", "[aout]",
           "-c:v", "libx264", "-crf", "20", "-preset", "medium",
           "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k",
           "-shortest", "/tmp/_narr.mp4"]
    subprocess.run(cmd, check=True)
    subprocess.run(["mv", "/tmp/_narr.mp4", out], check=True)
    print(f"wrote {out} ({vdur:.1f}s)")


if __name__ == "__main__":
    main()
