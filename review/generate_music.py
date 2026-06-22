"""Synthesize an original, royalty-free ambient bed for the demo video.

Because it is generated from scratch here, there are no licensing concerns. A
calm I-V-vi-IV pad progression with a soft arpeggio, gentle envelopes, light
stereo, and a short delay for air. Output: a WAV the caller muxes under the video.

    uv run python -m review.generate_music <seconds> <out.wav>
"""

from __future__ import annotations

import sys
import wave

import numpy as np

SR = 44100

# Calm, uplifting progression (Cadd9, G, Am7, Fmaj7), 4s per chord.
CHORDS = [
    [261.63, 329.63, 392.00, 587.33],   # Cadd9
    [196.00, 246.94, 392.00, 293.66],   # G
    [220.00, 261.63, 329.63, 392.00],   # Am7
    [174.61, 261.63, 329.63, 440.00],   # Fmaj7
]
CHORD_LEN = 4.0


def _adsr(n, a, d, s, r, sus=0.7):
    env = np.ones(n)
    ai, di, ri = int(a * SR), int(d * SR), int(r * SR)
    if ai:
        env[:ai] = np.linspace(0, 1, ai)
    if di:
        env[ai:ai + di] = np.linspace(1, sus, di)
    env[ai + di:n - ri] = sus
    if ri:
        env[n - ri:] = np.linspace(sus, 0, ri)
    return env


def _voice(freq, n, detune=0.0):
    t = np.arange(n) / SR
    f = freq * (1 + detune)
    # fundamental + soft harmonics + slow vibrato
    vib = 1 + 0.003 * np.sin(2 * np.pi * 5 * t)
    sig = (np.sin(2 * np.pi * f * t * vib)
           + 0.45 * np.sin(2 * np.pi * 2 * f * t)
           + 0.2 * np.sin(2 * np.pi * 3 * f * t))
    return sig / 1.65


def build(seconds: float) -> np.ndarray:
    total = int(seconds * SR)
    left = np.zeros(total)
    right = np.zeros(total)
    n_chord = int(CHORD_LEN * SR)

    pos = 0
    ci = 0
    while pos < total:
        chord = CHORDS[ci % len(CHORDS)]
        seg = min(n_chord, total - pos)
        env = _adsr(seg, a=0.8, d=0.4, s=0.0, r=1.2, sus=0.6)
        # pad: each note, slightly detuned across stereo
        for k, fr in enumerate(chord):
            vl = _voice(fr, seg, detune=-0.0015) * env * 0.16
            vr = _voice(fr, seg, detune=+0.0015) * env * 0.16
            left[pos:pos + seg] += vl
            right[pos:pos + seg] += vr
        # arpeggio: one note per second, an octave up, soft pluck
        for b in range(int(CHORD_LEN)):
            ap = pos + int(b * SR)
            if ap >= total:
                break
            an = min(int(0.9 * SR), total - ap)
            fr = chord[b % len(chord)] * 2
            penv = _adsr(an, a=0.01, d=0.3, s=0.0, r=0.5, sus=0.25)
            pluck = _voice(fr, an) * penv * 0.07
            left[ap:ap + an] += pluck * 0.9
            right[ap:ap + an] += pluck
        pos += seg
        ci += 1

    # light stereo delay for air
    delay = int(0.28 * SR)
    left[delay:] += 0.18 * right[:-delay]
    right[delay:] += 0.18 * left[:-delay]

    stereo = np.stack([left, right], axis=1)
    peak = np.max(np.abs(stereo)) or 1.0
    stereo = stereo / peak * 0.7
    return stereo


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    out = sys.argv[2] if len(sys.argv) > 2 else "assets/music.wav"
    audio = (build(seconds) * 32767).astype(np.int16)
    with wave.open(out, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(audio.tobytes())
    print(f"wrote {out} ({seconds:.1f}s)")


if __name__ == "__main__":
    main()
