"""SSTC MIDI Converter browser interface."""

import logging
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

import gradio as gr
import plotly.graph_objects as go
import pretty_midi
from gradio.processing_utils import save_file_to_cache

from music import (REFERENCE_BPM, midi_notes, monophonic, note_events,
                   prepare_notes, write_melody, write_midi)
from transcription import MAX_MB, decode_audio, media_command, transcribe

LOGGER = logging.getLogger(__name__)
CACHE = Path(os.environ.get("GRADIO_TEMP_DIR", "/tmp/gradio"))
TTL_SECONDS = 3600
HEADERS = ["Note", "MIDI pitch", "Frequency (Hz)", "Start (s)", "Duration (s)"]


def discard_audio(recording):
    if recording and recording.get("wav"):
        shutil.rmtree(Path(recording["wav"]).parent, ignore_errors=True)


def piano_roll(notes=(), length_ms=10000):
    names = [pretty_midi.note_number_to_name(n.pitch) for n in notes]
    figure = go.Figure(go.Bar(
        orientation="h", y=[n.pitch for n in notes], width=0.72,
        x=[n.duration_ms / 1000 for n in notes],
        base=[n.start_ms / 1000 for n in notes],
        customdata=names,
        marker={"color": "#edfc83", "line": {"width": 0}},
        hovertemplate="%{customdata} · %{base:.3f} s<br>Duration %{x:.3f} s<extra></extra>",
    ))
    pitches = [n.pitch for n in notes]
    lower, upper = (min(pitches) - 2, max(pitches) + 2) if pitches else (58, 73)
    ticks = list(range(max(0, lower), min(127, upper) + 1, max(1, (upper - lower) // 8)))
    figure.update_layout(
        autosize=True, height=330, margin=dict(l=45, r=18, t=16, b=42),
        paper_bgcolor="#161a1c", plot_bgcolor="#161a1c",
        font=dict(color="#b8c1bd", family="monospace", size=11),
        dragmode="pan", showlegend=False,
        xaxis=dict(title="Time (s)", gridcolor="#30383a", zeroline=False,
                   range=[0, max(0.1, length_ms / 1000)]),
        yaxis=dict(range=[lower, upper], gridcolor="#30383a", zeroline=False,
                   tickmode="array", tickvals=ticks,
                   ticktext=[pretty_midi.note_number_to_name(pitch) for pitch in ticks]),
    )
    if not notes:
        figure.add_annotation(
            text="Convert a recording to see its notes", x=0.5, y=0.5,
            xref="paper", yref="paper", showarrow=False, font=dict(size=13),
        )
    return figure



NO_RESULTS = (piano_roll(), [], None, None, None, None)


def stage_upload(upload, previous, progress=gr.Progress()):
    discard_audio(previous)
    if not upload:
        return None, None, None, "Choose a recording to start.", *NO_RESULTS
    source = Path(upload)
    stage = Path(mkdtemp(prefix="sstc-audio-"))
    decoded = stage / "recording.wav"
    try:
        progress(0.2, desc="Checking and decoding audio")
        seconds = decode_audio(source, decoded)
        progress(0.75, desc="Preparing playback")
        preview = stage / "preview.mp3"
        media_command("ffmpeg", "-nostdin", "-v", "error", "-i", str(decoded),
                      "-c:a", "libmp3lame", "-b:a", "64k", str(preview))
        preview_cached = save_file_to_cache(preview, CACHE)
        preview.unlink()
        return (
            None, preview_cached, {"wav": str(decoded), "seconds": seconds, "events": None},
            f"Audio ready · {seconds:.2f} seconds", *NO_RESULTS,
        )
    except Exception as error:
        shutil.rmtree(stage, ignore_errors=True)
        if not isinstance(error, (ValueError, RuntimeError)):
            LOGGER.exception("Audio preparation failed")
            error = ValueError("Audio preparation failed. Try uploading the recording again.")
        return None, None, None, str(error), *NO_RESULTS
    finally:
        source.unlink(missing_ok=True)


def convert(recording, bpm, transpose, minimum_ms, mono, progress=gr.Progress()):
    if not recording:
        return None, "Upload a recording before converting.", *NO_RESULTS
    if recording.get("wav"):
        progress(0.15, desc="Transcribing with Spotify Basic Pitch")
        try:
            events = transcribe(recording["wav"])
        except Exception:
            LOGGER.exception("Basic Pitch inference failed")
            return None, "Transcription failed. Try uploading a shorter recording.", *NO_RESULTS
        finally:
            discard_audio(recording)
        recording = {**recording, "events": events, "wav": None}

    try:
        progress(0.78, desc="Applying note and tempo settings")
        bpm, transpose, minimum_ms = int(bpm), int(transpose), int(minimum_ms)
        notes = prepare_notes(recording["events"], bpm, transpose, minimum_ms)
        notes = monophonic(notes, minimum_ms) if mono else midi_notes(notes)
        if not notes:
            return recording, "No notes match these settings. Try a lower minimum duration or a clearer recording.", *NO_RESULTS
        end_ms = round(recording["seconds"] * 1000 * REFERENCE_BPM / bpm)
        progress(0.9, desc="Writing downloads")
        with TemporaryDirectory(prefix="sstc-export-") as folder:
            midi = Path(folder) / "transcription.mid"
            write_midi(notes, bpm, midi)
            offline = write_melody(note_events(notes, end_ms), folder) if mono else (None,) * 3
            downloads = [save_file_to_cache(path, CACHE) if path else None for path in (midi, *offline)]
        rows = [
            [pretty_midi.note_number_to_name(n.pitch), n.pitch, n.frequency_hz,
             n.start_ms / 1000, n.duration_ms / 1000]
            for n in notes
        ]
        kind = "monophonic" if mono else "polyphonic"
        progress(1, desc="Downloads ready")
        return (recording, f"{len(notes):,} {'note' if len(notes) == 1 else 'notes'} · {kind} · {bpm} BPM",
                piano_roll(notes, max(end_ms, max(n.end_ms for n in notes))), rows, *downloads)
    except (ValueError, TypeError, OverflowError):
        return recording, "Check the tempo, transposition and duration settings.", *NO_RESULTS
    except Exception:
        LOGGER.exception("Export failed")
        return recording, "Could not create the downloads. Try converting again.", *NO_RESULTS


THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.lime, neutral_hue=gr.themes.colors.zinc,
    font=["Inter", "system-ui", "sans-serif"],
    font_mono=["IBM Plex Mono", "ui-monospace", "monospace"],
).set(
    body_background_fill="#101314", body_background_fill_dark="#101314",
    body_text_color="#f0f3ec", body_text_color_dark="#f0f3ec",
    body_text_color_subdued="#b8c1bd", body_text_color_subdued_dark="#b8c1bd",
    block_background_fill="#161a1c", block_background_fill_dark="#161a1c",
    block_border_color="#30383a", block_border_color_dark="#30383a",
    block_label_background_fill="#252d2b", block_label_background_fill_dark="#252d2b",
    table_even_background_fill="#1c2224", table_even_background_fill_dark="#1c2224",
    table_odd_background_fill="#161a1c", table_odd_background_fill_dark="#161a1c",
    table_border_color="#30383a", table_border_color_dark="#30383a",
    table_row_focus="#282f2d", table_row_focus_dark="#282f2d",
    color_accent="#bcd643", color_accent_soft="#23301b",
    block_label_text_color="#d6ddd6", block_label_text_color_dark="#d6ddd6",
    block_title_text_color="#f0f3ec", block_title_text_color_dark="#f0f3ec",
    input_background_fill="#1c2224", input_background_fill_dark="#1c2224",
    button_primary_background_fill="#edfc83", button_primary_background_fill_dark="#edfc83",
    button_primary_background_fill_hover="#d9e970", button_primary_background_fill_hover_dark="#d9e970",
    button_primary_text_color="#11170a", button_primary_text_color_dark="#11170a",
)

CSS = Path(__file__).with_name("style.css").read_text(encoding="utf-8")

with gr.Blocks(title="SSTC MIDI Converter", theme=THEME, css=CSS,
               delete_cache=(300, TTL_SECONDS), analytics_enabled=False) as demo:
    recording = gr.State(time_to_live=TTL_SECONDS, delete_callback=discard_audio)
    gr.HTML("""
    <header class="masthead">
      <div class="brand"><span class="brand-icon" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
        <span>SSTC MIDI Converter</span></div>
      <span class="limit">40 MiB max · 3 min max</span>
    </header>
    <section class="intro">
      <h1>Turn recordings<br>into <em>musical notes.</em></h1>
      <p>Transcribe an audio file, inspect its notes, and download MIDI or an offline ESP32 melody.</p>
    </section>
    """)
    with gr.Row(elem_id="workspace"):
        with gr.Column(scale=4, min_width=300, elem_classes="converter-panel"):
            gr.Markdown("## Recording")
            upload = gr.File(label="Upload audio", file_types=[".mp3", ".wav", ".flac", ".m4a"],
                             type="filepath", height=150, elem_id="audio-upload")
            gr.Markdown("MP3, WAV, FLAC or M4A", elem_classes="muted")
            preview = gr.Audio(label="Audio preview", interactive=False,
                               show_download_button=False, waveform_options=gr.WaveformOptions(
                                   waveform_color="#626d65", waveform_progress_color="#edfc83"))
            gr.Markdown("## Conversion settings", elem_classes="section-heading")
            bpm = gr.Slider(40, 240, value=120, step=1, label="Tempo (BPM)",
                            info="120 preserves the recording's timing.")
            transpose = gr.Slider(-24, 24, value=0, step=1, label="Transpose (semitones)")
            minimum = gr.Slider(0, 500, value=70, step=5, label="Minimum note duration (ms)")
            mono = gr.Checkbox(label="Monophonic Melody",
                              info="Keep one note at a time and enable offline melody downloads.")
            run = gr.Button("Convert to MIDI", variant="primary", elem_id="convert")
        with gr.Column(scale=7, min_width=300, elem_classes="converter-panel"):
            with gr.Row(elem_classes="result-heading"):
                gr.Markdown("## Piano roll")
                status = gr.Markdown("Choose a recording to start.", elem_id="status")
            plot = gr.Plot(value=piano_roll(), show_label=False, elem_id="piano-roll")
            with gr.Tabs():
                with gr.Tab("Extracted notes"):
                    table = gr.Dataframe(headers=HEADERS, datatype=["str"] + ["number"] * 4,
                                         interactive=False, max_height=318, show_label=False)
                with gr.Tab("Downloads"):
                    midi_file = gr.File(label="MIDI")
                    gr.Markdown("Enable **Monophonic Melody** to export offline note data with rests.")
                    with gr.Row():
                        json_file = gr.File(label="JSON")
                        csv_file = gr.File(label="CSV")
                        header_file = gr.File(label="C++ header")
    gr.Markdown("Automatic transcription is approximate. Dense mixes and overlapping instruments may need manual correction.",
                elem_classes="footer")
    results = [status, plot, table, midi_file, json_file, csv_file, header_file]
    upload.upload(stage_upload, [upload, recording], [upload, preview, recording, *results],
                  concurrency_id="audio", api_name="prepare_audio")
    run.click(convert, [recording, bpm, transpose, minimum, mono], [recording, *results],
              concurrency_id="audio", api_name="convert")

demo.queue(max_size=12)

if __name__ == "__main__":
    demo.launch(max_file_size=MAX_MB * 1024 * 1024)
