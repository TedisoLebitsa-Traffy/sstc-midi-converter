"""Render an audible synth preview from offline melody data."""

import wave

import numpy as np

SAMPLE_RATE = 24000
SAMPLES_PER_MS = SAMPLE_RATE // 1000


def write_preview(events, path):
    """Write a mono WAV with a buzzy timbre and sample-accurate rests."""
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for event in events:
            frames = event["durationMs"] * SAMPLES_PER_MS
            if event["rest"]:
                output.writeframesraw(bytes(2 * frames))
                continue
            frequency = event["frequencyHz"]
            phase = np.arange(frames) * (2 * np.pi * frequency / SAMPLE_RATE)
            signal = np.sin(phase)
            for harmonic in (3, 5, 7):
                if frequency * harmonic >= SAMPLE_RATE / 2:
                    break
                signal += np.sin(harmonic * phase) / harmonic
            fade = min(SAMPLE_RATE // 200, frames // 2)
            if fade:
                envelope = np.linspace(0, 1, fade)
                signal[:fade] *= envelope
                signal[-fade:] *= envelope[::-1]
            output.writeframesraw((signal * 10000).astype("<i2").tobytes())
