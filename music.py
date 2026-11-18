"""Musical timing, MIDI, and offline melody serialization."""

import csv
import heapq
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import pretty_midi

REFERENCE_BPM = 120


@dataclass(frozen=True)
class Note:
    start_ms: int
    end_ms: int
    pitch: int
    velocity: int

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms

    @property
    def frequency_hz(self):
        return round(440 * 2 ** ((self.pitch - 69) / 12), 3)


def prepare_notes(events, bpm, transpose, minimum_ms):
    """Filter in output time and transpose, discarding out-of-range notes."""
    if not (40 <= bpm <= 240 and -24 <= transpose <= 24 and 0 <= minimum_ms <= 500):
        raise ValueError("Use the supported ranges for tempo, transpose and minimum duration.")
    scale = 1000 * REFERENCE_BPM / bpm
    notes = []
    for start, end, pitch, amplitude, _ in events:
        if not all(math.isfinite(value) for value in (start, end, pitch, amplitude)):
            continue
        first, last = round(max(0, start) * scale), round(end * scale)
        pitch = int(pitch) + transpose
        if 0 <= pitch <= 127 and last - first >= max(1, minimum_ms):
            notes.append(Note(first, last, pitch, min(127, max(1, round(amplitude * 127)))))
    return sorted(notes, key=lambda n: (n.start_ms, n.pitch, n.end_ms, -n.velocity))


def monophonic(notes, minimum_ms=0):
    """Choose the strongest active note; prefer higher pitch on ties."""
    starts = {}
    for index, note in enumerate(notes):
        starts.setdefault(note.start_ms, []).append(index)
    boundaries = sorted(set(starts).union(note.end_ms for note in notes))
    active = []
    segments = []
    for timestamp, following in zip(boundaries, boundaries[1:]):
        for index in starts.get(timestamp, ()):
            note = notes[index]
            heapq.heappush(active, (-note.velocity, -note.pitch, index))
        while active and notes[active[0][2]].end_ms <= timestamp:
            heapq.heappop(active)
        if not active:
            continue
        index = active[0][2]
        if segments and segments[-1][0] == index and segments[-1][1].end_ms == timestamp:
            segments[-1] = (index, replace(segments[-1][1], end_ms=following))
        else:
            segments.append((index, replace(notes[index], start_ms=timestamp, end_ms=following)))
    return [note for _, note in segments if note.duration_ms >= max(1, minimum_ms)]


def midi_notes(notes, minimum_ms=0):
    """Keep one active event per pitch so identical pitches can retrigger safely."""
    by_pitch = {}
    for note in notes:
        track = by_pitch.setdefault(note.pitch, [])
        if track and note.start_ms == track[-1].start_ms:
            previous = track[-1]
            track[-1] = replace(previous, end_ms=max(note.end_ms, previous.end_ms),
                                velocity=max(note.velocity, previous.velocity))
            continue
        if track and note.start_ms < track[-1].end_ms:
            track[-1] = replace(track[-1], end_ms=note.start_ms)
        track.append(note)
    return sorted((note for track in by_pitch.values() for note in track
                   if note.duration_ms >= max(1, minimum_ms)),
                  key=lambda note: (note.start_ms, note.pitch))


def write_midi(notes, bpm, path):
    midi = pretty_midi.PrettyMIDI(initial_tempo=bpm, resolution=960)
    piano = pretty_midi.Instrument(program=0, name="Transcription")
    piano.notes = [
        pretty_midi.Note(n.velocity, n.pitch, n.start_ms / 1000, n.end_ms / 1000)
        for n in notes
    ]
    midi.instruments.append(piano)
    midi.write(str(path))


def note_events(notes, end_ms):
    """Include initial silence, gaps and remaining recording time."""
    cursor = 0
    events = []
    for note in notes:
        if note.start_ms < cursor:
            raise ValueError("Offline melody exports require non-overlapping notes.")
        if note.start_ms > cursor:
            events.append(dict(frequencyHz=0.0, durationMs=note.start_ms - cursor, rest=True))
        events.append(dict(frequencyHz=note.frequency_hz, durationMs=note.duration_ms, rest=False))
        cursor = note.end_ms
    if end_ms > cursor:
        events.append(dict(frequencyHz=0.0, durationMs=end_ms - cursor, rest=True))
    return events


def write_melody(events, directory):
    directory = Path(directory)
    json_path, csv_path, header_path = (directory / name for name in
                                      ("melody.json", "melody.csv", "melody.h"))
    json_path.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["frequencyHz", "durationMs", "rest"])
        writer.writeheader()
        for event in events:
            writer.writerow({**event, "rest": str(event["rest"]).lower()})
    rows = [
        f'    {{{event["frequencyHz"]:.3f}f, {event["durationMs"]}u, '
        f'{str(event["rest"]).lower()}}},'
        for event in events
    ]
    header_path.write_text("\n".join([
        "#pragma once", "", "#include <stddef.h>", "#include <stdint.h>", "",
        "namespace sstc_melody {", "struct NoteEvent {", "    float frequencyHz;",
        "    uint32_t durationMs;", "    bool rest;", "};", "",
        "static constexpr NoteEvent melody[] = {", *rows, "};",
        "static constexpr size_t melodyCount = sizeof(melody) / sizeof(melody[0]);",
        "}  // namespace sstc_melody", "",
    ]), encoding="utf-8")
    return str(json_path), str(csv_path), str(header_path)
