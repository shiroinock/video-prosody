#!/usr/bin/env python3
"""Local audio/video transcription using optional MLX Whisper (Apple Silicon)."""

import hashlib
import importlib.metadata
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO = "mlx-community/whisper-large-v3-mlx"


def prepare_model(model_path, repository=REPO, revision=None):
    """Download an explicit revision, or resolve and record the current revision."""
    from huggingface_hub import HfApi, snapshot_download

    model = Path(model_path).resolve()
    revision = revision or HfApi().model_info(repository).sha
    snapshot_download(
        repository,
        revision=revision,
        local_dir=model,
        allow_patterns=["config.json", "weights.npz", "*.safetensors"],
    )
    (model / "provenance.json").write_text(
        json.dumps({"repository": repository, "revision": revision}, indent=2) + "\n"
    )
    return model


def transcribe(
    audio,
    *,
    model_path,
    output_dir,
    language="ja",
    dictionary=None,
    acquisition=None,
):
    """Transcribe a local audio/video file into a new provenance-bearing run.

    No domain dictionary is applied unless supplied explicitly. Returns the run path.
    """
    if urlparse(str(audio)).scheme in ("http", "https"):
        raise ValueError(
            "Provide a local audio/video file; remote acquisition is external"
        )
    model = Path(model_path).resolve()
    if not (model / "config.json").is_file():
        raise FileNotFoundError("Model is not prepared; run prepare-model first")
    output_dir = Path(output_dir)
    import imageio_ffmpeg
    import mlx.core as mx
    import mlx_whisper
    import numpy as np

    if not mx.metal.is_available():
        raise RuntimeError("Metal GPU is unavailable in this execution environment")
    source = Path(audio).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    decoded = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    samples = np.frombuffer(decoded.stdout, dtype="<f4").copy()
    if not samples.size:
        raise ValueError("No audio samples decoded")
    settings = {
        "language": language,
        "task": "transcribe",
        "word_timestamps": True,
        "temperature": 0.0,
        "condition_on_previous_text": True,
        "initial_prompt": None,
        "fp16": True,
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "no_speech_threshold": 0.6,
    }
    result = mlx_whisper.transcribe(
        samples, path_or_hf_repo=str(model), verbose=False, **settings
    )
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    provenance_file = model / "provenance.json"
    provenance = (
        json.loads(provenance_file.read_text()) if provenance_file.exists() else None
    )
    versions = {
        name: importlib.metadata.version(name)
        for name in ("mlx", "mlx-metal", "mlx-whisper", "numpy", "imageio-ffmpeg")
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_dir / f"{source.stem}-{stamp}-{uuid.uuid4().hex[:8]}"
    destination.mkdir(parents=True, exist_ok=False)
    payload = {
        "metadata": {
            "source": str(source),
            "source_sha256": digest.hexdigest(),
            "acquisition": acquisition,
            "duration_seconds": len(samples) / 16000,
            "created_at_utc": stamp,
            "model_path": str(model),
            "model_provenance": provenance,
            "package_versions": versions,
            "settings": settings,
            "audio_preprocessing": "first audio stream; mono; 16000 Hz float32",
            "ffmpeg_version": imageio_ffmpeg.get_ffmpeg_version(),
            "human_verified": False,
        },
        "result": result,
    }
    (destination / "raw.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    (destination / "transcript.txt").write_text(result["text"] + "\n")
    print(f"Saved: {destination}")
    if dictionary is not None:
        from .corrections import correct_file

        correct_file(destination / "raw.json", dictionary)
    return destination
