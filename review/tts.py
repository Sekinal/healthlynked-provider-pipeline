"""Generate the demo voiceover with ElevenLabs (one clip per section).

Reads the API key from ELEVENLABS_API_KEY. Writes assets/vo/<key>.mp3 and a
manifest with measured durations so the recorder can pace each section to its line.

    ELEVENLABS_API_KEY=... uv run python -m review.tts
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

VOICE_ID = os.getenv("ELEVENLABS_VOICE", "IKne3meq5aSn9XLyUdCD")  # Charlie
MODEL = "eleven_multilingual_v2"
OUT = Path("assets/vo")

# (key, text) — one spoken line per visual section.
LINES = [
    ("title", "Healthcare provider data goes stale fast. This pipeline keeps it accurate, automatically, and cheap."),
    ("dash", "This is the live review dashboard, driven by the running pipeline."),
    ("metrics", "Every record is scored. Only safe, high-confidence updates apply automatically."),
    ("funnel", "Most records resolve for free. Only the uncertain ones ever reach a human."),
    ("review", "Each flagged record shows the proposed change, its sources, and a confidence score."),
    ("approve", "One click applies the update and writes a full audit trail."),
    ("cost", "It leads with free authoritative data, and the cheapest accurate model handles conflicts. Under two dollars per thousand records."),
    ("history", "Confidence decides everything. Only the high-confidence tail updates on its own."),
    ("close", "Repeatable. Verifiable. Cheap. And running in production today."),
]


def _dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def main() -> None:
    key = os.environ["ELEVENLABS_API_KEY"]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    with httpx.Client(timeout=90) as client:
        for k, text in LINES:
            mp3 = OUT / f"{k}.mp3"
            resp = client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": text, "model_id": MODEL,
                      "voice_settings": {"stability": 0.45, "similarity_boost": 0.8,
                                         "style": 0.25, "use_speaker_boost": True}},
            )
            resp.raise_for_status()
            mp3.write_bytes(resp.content)
            d = _dur(mp3)
            manifest.append({"key": k, "text": text, "file": str(mp3), "dur": round(d, 2)})
            print(f"{k:9} {d:5.2f}s  {text[:50]}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("total speech:", round(sum(m["dur"] for m in manifest), 1), "s")


if __name__ == "__main__":
    main()
