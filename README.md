---
title: SSTC MIDI Converter
emoji: 🎹
colorFrom: gray
colorTo: green
sdk: gradio
sdk_version: 5.49.1
python_version: "3.11"
app_file: app.py
pinned: false
short_description: Transcribe recordings to MIDI and offline melody data
---

# SSTC MIDI Converter

Upload a recording, inspect the notes in a piano roll, and download MIDI. Monophonic Melody mode also exports JSON, CSV and a C++ header with musical timing for offline ESP32 development.

## Paste a melody

Open [melody.html](melody.html) in a browser for the standalone note editor. It plays the melody and exports MIDI and an ESP32 LED-test sketch. It works offline after downloading the HTML file.

## Run locally

Use Python 3.11 and install FFmpeg with `ffmpeg` and `ffprobe` on your PATH. On Debian or Ubuntu, install `ffmpeg` with your package manager. On macOS, use `brew install ffmpeg`. On Windows, WSL provides the Linux setup tested by this project.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:7860](http://127.0.0.1:7860). Basic Pitch loads its bundled model on the first conversion.

## Convert a recording

1. Upload an MP3, WAV, FLAC or M4A file up to 40 MiB and 3 minutes. Play the normalized mono preview to check the input.
2. Set tempo, transpose and minimum note duration. At 120 BPM, the original timing is preserved. At 60 BPM, all note starts, durations and rests are doubled. Tempo is a playback setting referenced to 120 BPM.
3. Enable **Monophonic Melody** for one note at a time. The converter picks the strongest active note and uses higher pitch to break ties. It drops fragments below the minimum duration.
4. Select **Convert to MIDI**. Inspect the piano roll and extracted notes. Press play on **Tesla coil-style preview** to hear a buzzy, synthesized rendition of the edited monophonic melody, including its rests. The preview is available with polyphonic MIDI too, and real coil acoustics may differ. Select **Downloads** for MIDI and, in monophonic mode, JSON, CSV and the C++ header. Change the settings and convert again to reuse the existing transcription.

Frequency uses 12-tone equal temperament with A4 at 440 Hz. Timing is rounded to milliseconds before export. The upload is deleted after decoding, and the decoded audio is removed after transcription or session cleanup. Temporary audio previews and generated downloads are retained in the Gradio cache for roughly an hour. Download anything you want to keep before closing the session.

## Example

For a one-second recording, suppose the MIDI contains A4 at 0.10 s for 0.25 s and C5 at 0.50 s for 0.25 s. The monophonic JSON export includes the initial silence, the gap between notes, and the silence through the end of the recording.

```json
[
  {"frequencyHz": 0.0, "durationMs": 100, "rest": true},
  {"frequencyHz": 440.0, "durationMs": 250, "rest": false},
  {"frequencyHz": 0.0, "durationMs": 150, "rest": true},
  {"frequencyHz": 523.251, "durationMs": 250, "rest": false},
  {"frequencyHz": 0.0, "durationMs": 250, "rest": true}
]
```

CSV uses the same fields. The C++11 header declares `sstc_melody::melody` and `sstc_melody::melodyCount`, with each entry defined as follows.

```cpp
struct NoteEvent {
    float frequencyHz;
    uint32_t durationMs;
    bool rest;
};
```

## Accuracy and limitations

Automatic transcription is approximate. Clean, pitched recordings work best. Dense mixes, percussion, reverb and overlapping harmonics can produce extra notes, missed notes or octave errors. Basic Pitch estimates pitches in the mixed signal and does not separate instruments. Monophonic mode selects by note strength and can follow an accompaniment instead of the intended lead. MIDI uses one piano track and discrete pitches. Use a MIDI editor to correct challenging material.

## Deploy on Hugging Face Spaces

1. Sign into Hugging Face and [create a Space](https://huggingface.co/new-space). Choose **Gradio**, **Public** and CPU hardware.
2. Create a Hugging Face write token with access to that Space. On GitHub, open **Settings → Secrets and variables → Actions**. Add the repository secret `HF_TOKEN` and the repository variable `HF_SPACE_ID` with the value `owner/space-name`.
3. Under **Actions**, run **Deploy to Hugging Face Spaces** on the branch you want to publish. The workflow sends only the application files. Alternatively, upload `README.md`, `app.py`, `audition.py`, `music.py`, `transcription.py`, `style.css`, `requirements.txt` and `packages.txt` under the Space’s **Files** tab. The README metadata configures Python and Gradio, and `packages.txt` installs FFmpeg.
4. Wait for **Running** on the Space page. Open the app in a signed-out window to check public access, then upload a short clip and download its MIDI.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests generate audio on the fly and cover FFmpeg decoding, the real Basic Pitch model, MIDI timing and pitch validity, monophonic selection, rest accounting, C++ header compilation when a compiler is installed, and temporary-file cleanup.
