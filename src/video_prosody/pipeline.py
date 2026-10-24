"""Local-file analysis and a resumable inference engine for external adapters."""

import hashlib
import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse

from . import Project

PIPELINE_VERSION = 2


def default_cache_dir():
    if os.environ.get("VIDEO_PROSODY_CACHE_DIR"):
        return Path(os.environ["VIDEO_PROSODY_CACHE_DIR"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/video-prosody"
    return (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "video-prosody"
    )


def default_output_dir():
    """Human edits live in durable application data, outside disposable audio caches."""
    if os.environ.get("VIDEO_PROSODY_OUTPUT_DIR"):
        return Path(os.environ["VIDEO_PROSODY_OUTPUT_DIR"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/video-prosody"
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "video-prosody"
    )


def find_models(model_path=None, alignment_model=None):
    candidates = []
    if os.environ.get("VIDEO_PROSODY_MODEL_DIR"):
        candidates.append(Path(os.environ["VIDEO_PROSODY_MODEL_DIR"]).expanduser())
    candidates += [
        default_cache_dir() / "models",
        Path(__file__).resolve().parents[2] / "work/models",
        Path.cwd() / "work/models",
    ]
    model = (
        Path(model_path).expanduser()
        if model_path
        else next(
            (
                p / "large-v3"
                for p in candidates
                if (p / "large-v3/config.json").exists()
            ),
            candidates[0] / "large-v3",
        )
    )
    aligner = (
        Path(alignment_model).expanduser()
        if alignment_model
        else next(
            (
                p / "narabas/narabas-v0.onnx"
                for p in candidates
                if (p / "narabas/narabas-v0.onnx").exists()
            ),
            candidates[0] / "narabas/narabas-v0.onnx",
        )
    )
    if not (model / "config.json").is_file() or not aligner.is_file():
        raise FileNotFoundError(
            "Models are not ready. Prepare Whisper and narabas; set VIDEO_PROSODY_MODEL_DIR or use --model-path and --alignment-model. See README."
        )
    return model.resolve(), aligner.resolve()


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


@contextmanager
def cache_lock(folder):
    # OS locks release on crashes; a killed analysis never leaves a stale lock.
    import fcntl

    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("This video's analysis is already running") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def checked_file(root, entry):
    path = (root / entry["path"]).resolve()
    if (
        not path.is_relative_to(root.resolve())
        or not path.is_file()
        or digest(path) != entry["sha256"]
    ):
        raise RuntimeError("Cached artifact is missing or changed: " + str(path))
    return path


def playback_audio(original, folder, cached, progress):
    """Decode for listening at source rate/channels; never use the ASR downsample."""
    if cached.get("playback"):
        return checked_file(folder, cached["playback"])
    import imageio_ffmpeg

    progress("Preparing playback audio at the source sample rate and channels…")
    output = folder / "playback.wav"
    temporary = output.with_name(f".playback-{uuid.uuid4().hex}.wav")
    try:
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(original),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "pcm_s24le",
                str(temporary),
            ],
            check=True,
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    cached["playback"] = {
        "path": str(output.relative_to(folder)),
        "sha256": digest(output),
    }
    cached["playback_preprocessing"] = (
        "source sample rate and channels; PCM24 WAV; no lossy re-encoding"
    )
    return output


def model_signature(model, aligner, language):
    files = sorted(
        p
        for p in model.iterdir()
        if p.name == "config.json" or p.suffix in (".npz", ".safetensors")
    )
    if not any(p.suffix in (".npz", ".safetensors") for p in files):
        raise FileNotFoundError("Whisper model weights are missing")
    return {
        "pipeline_version": PIPELINE_VERSION,
        "language": language,
        "whisper": {p.name: digest(p) for p in files},
        "narabas_sha256": digest(aligner),
        "packages": {
            name: version(name)
            for name in (
                "mlx-whisper",
                "librosa",
                "janome",
                "fugashi",
                "unidic-lite",
                "pyopenjtalk",
                "onnxruntime",
                "numpy",
                "soundfile",
            )
        },
    }


def analyze(
    media,
    *,
    output_dir=None,
    model_path=None,
    alignment_model=None,
    language="ja",
    progress=print,
):
    """Analyze an existing local file and return its resumable review Project."""
    if urlparse(str(media)).scheme in ("http", "https"):
        raise ValueError(
            "Provide a local audio/video file; remote acquisition is external"
        )
    audio = Path(media).expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(audio)
    if language != "ja":
        raise ValueError(
            "The combined word/mora pipeline currently supports Japanese only"
        )
    model, aligner = find_models(model_path, alignment_model)
    source_hash = digest(audio)
    vid = "local_" + source_hash[:16]
    results = Path(output_dir).expanduser() if output_dir else default_output_dir()
    folder = results.resolve() / "local" / source_hash
    with cache_lock(folder):
        pointer = folder / "playback.json"
        cached = json.loads(pointer.read_text()) if pointer.exists() else {}
        playback_folder = folder / "audio"
        playback_folder.mkdir(exist_ok=True)
        playback = playback_audio(audio, playback_folder, cached, progress)
        if digest(audio) != source_hash:
            raise RuntimeError("Local source changed while preparing playback")
        write_json(pointer, cached)
        return analyze_prepared(
            audio,
            playback=playback,
            video_id=vid,
            title=audio.name,
            analyses_dir=folder / "analyses",
            model=model,
            aligner=aligner,
            language=language,
            progress=progress,
            record_metadata={"media": str(audio)},
        )


def analyze_prepared(
    audio,
    *,
    playback,
    video_id,
    title,
    analyses_dir,
    model,
    aligner,
    language="ja",
    acquisition=None,
    record_metadata=None,
    progress=print,
):
    """Analyze prepared local media; external adapters own acquisition and locking.

    Keep analyses_dir scoped to this source/record identity. record_metadata may
    supply provenance/review fields, while generated artifact paths stay authoritative.
    """
    audio, playback = Path(audio).resolve(), Path(playback).resolve()
    if not audio.is_file() or not playback.is_file():
        raise FileNotFoundError("Prepared analysis and playback files must exist")
    if language != "ja":
        raise ValueError(
            "The combined word/mora pipeline currently supports Japanese only"
        )
    vid = video_id
    signature = {
        **model_signature(model, aligner, language),
        "source_audio_sha256": digest(audio),
    }
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[
        :24
    ]
    run = Path(analyses_dir).resolve() / key
    run.mkdir(parents=True, exist_ok=True)
    checkpoint = run / "state.json"
    state = (
        json.loads(checkpoint.read_text())
        if checkpoint.exists()
        else {"signature": signature, "stages": {}}
    )
    if state["signature"] != signature:
        raise RuntimeError("Analysis cache signature mismatch")

    def stage(name, action):
        if name in state["stages"]:
            progress(f"Using cached {name}")
            return {k: checked_file(run, v) for k, v in state["stages"][name].items()}
        progress(f"Running {name}…")
        result = action()
        state["stages"][name] = {
            k: {"path": str(p.relative_to(run)), "sha256": digest(p)}
            for k, p in result.items()
        }
        write_json(checkpoint, state)
        return result

    from .alignment import align_moras
    from .measurement import measure
    from .readings import add_readings
    from .transcribe import transcribe

    def asr():
        target = transcribe(
            audio,
            model_path=model,
            output_dir=run / "transcripts",
            language=language,
            acquisition=acquisition,
        )
        return {"raw": target / "raw.json"}

    raw = stage("transcript", asr)["raw"]
    manifest = run / "project.json"
    config = {
        "schema_version": 1,
        "name": title,
        "videos": [
            {
                **(record_metadata or {}),
                "video_id": vid,
                "title": title,
                "raw_json": str(raw.relative_to(run)),
                "audio": os.path.relpath(playback, run),
                "analysis_audio": os.path.relpath(audio, run),
            }
        ],
    }
    write_json(manifest, config)
    project = Project(manifest)
    store = project.prosody_dir
    store.mkdir(exist_ok=True)
    measured = store / f"{vid}-measured.json"
    readings = store / f"{vid}-readings.json"
    estimated = store / f"{vid}-moras-estimated.json"
    active = store / f"{vid}-moras-active.json"

    def publish(output, action):
        # Work off to the side: interruption never leaves a partial immutable layer.
        temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.json")
        try:
            result = action(temporary)
            if output.exists():
                # Retain an uncheckpointed artifact from a previous interrupted run.
                output.rename(
                    output.with_name(
                        f"{output.stem}-uncheckpointed-{uuid.uuid4().hex}.json"
                    )
                )
            temporary.replace(output)
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def measurement():
        publish(
            measured,
            lambda target: measure(
                project.document(vid), json.loads(raw.read_text()), audio, target
            ),
        )
        return {"measured": measured}

    stage("prosody", measurement)

    def dictionary():
        publish(readings, lambda target: add_readings(measured, target))
        return {"readings": readings}

    stage("readings", dictionary)

    def alignment():
        result = publish(
            estimated,
            lambda target: align_moras(
                project.prosody.document(vid), audio, aligner, target
            ),
        )
        write_json(active, {"active": estimated.name, "sha256": digest(estimated)})
        if result.get("unaligned_segments"):
            progress(
                f"Mora alignment needs review: {len(result['unaligned_segments'])} unaligned utterances"
            )
        return {"estimated": estimated}

    stage("moras", alignment)
    progress(f"Ready: {manifest}")
    return project
