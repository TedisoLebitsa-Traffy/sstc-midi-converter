"""Bounded FFmpeg decoding and lazy Basic Pitch inference."""

import json
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

MAX_MB = 40
MAX_SECONDS = 180
SAMPLE_RATE = 22050
FORMATS = {
    ".mp3": {"mp3"},
    ".wav": {"wav"},
    ".flac": {"flac"},
    ".m4a": {"m4a", "mov", "mp4"},
}


def media_command(*args, timeout=25):
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=timeout).stdout
    except FileNotFoundError as error:
        raise RuntimeError("FFmpeg and ffprobe must be installed on the server.") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("Audio decoding timed out. Try a shorter clip.") from error
    except subprocess.CalledProcessError as error:
        raise ValueError("Could not decode the audio. Check that the file plays and is a supported format.") from error


def decode_audio(source, decoded):
    suffix = source.suffix.lower()
    if suffix not in FORMATS:
        raise ValueError("Upload an MP3, WAV, FLAC or M4A recording.")
    if not 0 < source.stat().st_size <= MAX_MB * 1024 * 1024:
        raise ValueError(f"Upload a non-empty file up to {MAX_MB} MiB.")
    details = json.loads(media_command(
        "ffprobe", "-v", "error", "-protocol_whitelist", "file",
        "-format_whitelist", "mp3,wav,flac,mov", "-select_streams", "a:0",
        "-show_entries", "format=format_name:stream=index", "-of", "json", str(source),
    ))
    formats = details.get("format", {}).get("format_name", "").split(",")
    if not details.get("streams") or not FORMATS[suffix].intersection(formats):
        raise ValueError("The file contents must match the extension and contain an audio stream.")
    container_options = ("-enable_drefs", "0") if suffix == ".m4a" else ()
    media_command(
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file",
        "-format_whitelist", "mp3,wav,flac,mov", *container_options,
        "-i", str(source), "-map", "0:a:0", "-vn", "-t", str(MAX_SECONDS + 1),
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", "-y", str(decoded),
    )
    with wave.open(str(decoded), "rb") as audio:
        frames = audio.getnframes()
    if frames > MAX_SECONDS * SAMPLE_RATE:
        raise ValueError(f"Trim the recording to {MAX_SECONDS // 60} minutes or less.")
    if frames < SAMPLE_RATE // 10:
        raise ValueError("Upload at least 0.1 seconds of audio.")
    return frames / SAMPLE_RATE


@lru_cache(maxsize=1)
def load_model():
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import Model

    return Model(Path(ICASSP_2022_MODEL_PATH))


def transcribe(path):
    from basic_pitch.inference import predict

    _, _, events = predict(str(path), model_or_model_path=load_model(), minimum_note_length=32)
    return events
