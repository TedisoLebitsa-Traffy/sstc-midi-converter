import csv
import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pretty_midi
import pytest

from music import midi_notes, monophonic, note_events, prepare_notes, write_melody, write_midi
from transcription import SAMPLE_RATE, decode_audio, transcribe


def make_melody(path):
    signal = np.zeros(int(SAMPLE_RATE * 2.1))
    for start, frequency in ((0.2, 440), (0.8, 523.25), (1.4, 659.25)):
        time = np.arange(int(SAMPLE_RATE * 0.36)) / SAMPLE_RATE
        envelope = np.minimum(1, np.minimum(time, time[-1] - time) / 0.015)
        tone = envelope * (np.sin(2 * np.pi * frequency * time)
                           + 0.25 * np.sin(4 * np.pi * frequency * time))
        offset = round(start * SAMPLE_RATE)
        signal[offset:offset + len(tone)] = tone / 2
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        stream.writeframes((signal * 32767).astype("<i2").tobytes())


@pytest.mark.parametrize("extension", ["wav", "mp3", "flac", "m4a"])
def test_supported_audio_formats(tmp_path, extension):
    original = tmp_path / "synthetic.wav"
    make_melody(original)
    encoded = tmp_path / f"recording.{extension}"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(original), str(encoded)], check=True)
    assert decode_audio(encoded, tmp_path / "decoded.wav") == pytest.approx(2.1, abs=0.1)


def test_invalid_audio(tmp_path):
    bogus = tmp_path / "broken.mp3"
    bogus.write_bytes(b"broken")
    with pytest.raises(ValueError, match="Could not decode"):
        decode_audio(bogus, tmp_path / "decoded.wav")
    wrong = tmp_path / "wrong.flac"
    make_melody(wrong)
    with pytest.raises(ValueError, match="contents must match"):
        decode_audio(wrong, tmp_path / "decoded.wav")


def test_duration_limit(tmp_path):
    source = tmp_path / "long.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        stream.writeframes(bytes(SAMPLE_RATE * 2 * 181))
    with pytest.raises(ValueError, match="3 minutes"):
        decode_audio(source, tmp_path / "decoded.wav")


def test_synthetic_audio_to_midi(tmp_path):
    original = tmp_path / "synthetic.wav"
    decoded = tmp_path / "decoded.wav"
    midi_path = tmp_path / "synthetic.mid"
    make_melody(original)
    decode_audio(original, decoded)
    notes = midi_notes(prepare_notes(transcribe(decoded), 120, 0, 70))
    write_midi(notes, 120, midi_path)
    midi = pretty_midi.PrettyMIDI(str(midi_path))
    output = midi.instruments[0].notes
    assert {69, 72, 76}.issubset(n.pitch for n in output)
    assert all(0 <= n.pitch <= 127 and n.end > n.start >= 0 for n in output)


def test_transpose_tempo_and_polyphony(tmp_path):
    notes = prepare_notes([(0.2, 0.8, 60, 0.4, None), (0.4, 0.9, 60, 0.7, None),
                           (0.4, 0.9, 64, 0.3, None), (0, 0.01, 70, 1, None),
                           (0, 1, 127, 1, None)], 60, 2, 40)
    rendered = midi_notes(notes)
    assert [(n.pitch, n.start_ms, n.end_ms) for n in rendered] == [
        (62, 400, 800), (62, 800, 1800), (66, 800, 1800)]
    midi_path = tmp_path / "edited.mid"
    write_midi(rendered, 60, midi_path)
    readback = pretty_midi.PrettyMIDI(str(midi_path))
    assert readback.get_tempo_changes()[1][0] == pytest.approx(60)
    assert sorted(n.end for n in readback.instruments[0].notes) == pytest.approx([0.8, 1.8, 1.8])


def test_monophonic_and_offline_exports(tmp_path):
    notes = prepare_notes([(0.1, 0.5, 69, 0.4, None), (0.3, 0.4, 72, 0.9, None),
                           (0.7, 0.9, 69, 0.6, None)], 120, 0, 50)
    melody = monophonic(notes, 50)
    assert [(n.pitch, n.start_ms, n.end_ms) for n in melody] == [
        (69, 100, 300), (72, 300, 400), (69, 400, 500), (69, 700, 900)]
    events = note_events(melody, 1000)
    assert [e["durationMs"] for e in events if e["rest"]] == [100, 200, 100]
    assert sum(e["durationMs"] for e in events) == 1000
    json_file, csv_file, header = write_melody(events, tmp_path)
    assert json.loads(Path(json_file).read_text()) == events
    with open(csv_file, newline="") as stream:
        assert next(csv.DictReader(stream)) == {
            "frequencyHz": "0.0", "durationMs": "100", "rest": "true"}
    assert "{440.000f, 200u, false}" in Path(header).read_text()
    compiler = shutil.which("c++")
    if compiler:
        subprocess.run([compiler, "-std=c++11", "-pedantic-errors", "-fsyntax-only",
                        "-x", "c++", "-"], cwd=tmp_path, check=True, text=True, input="""
#include "melody.h"
static_assert(sstc_melody::melodyCount == 7, "Unexpected number of events");
static_assert(sstc_melody::melody[0].rest, "Starts with a rest");
static_assert(sstc_melody::melody[1].frequencyHz == 440.0f, "Expected A4");
""")


def test_app_stages_upload_and_cleans_raw_and_decoded_files(tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(app, "CACHE", tmp_path / "cache")
    upload = tmp_path / "uploaded.wav"
    make_melody(upload)
    _, preview, recording, status, *_ = app.stage_upload(str(upload), None)
    assert status.startswith("Audio ready")
    assert Path(preview).exists()
    assert not upload.exists()
    staged = Path(recording["wav"])
    monkeypatch.setattr(app, "transcribe", lambda _: [(0.2, 0.5, 69, 0.8, None)])
    saved, status, _, rows, midi, *offline = app.convert(recording, 120, 0, 70, True)
    assert "1 note" in status
    assert not staged.parent.exists()
    assert rows == [["A4", 69, 440.0, 0.2, 0.3]]
    assert all(Path(path).is_file() for path in (midi, *offline))
    _, _, _, edited, *_ = app.convert(saved, 60, 3, 70, False)
    assert edited[0][:2] == ["C5", 72]
