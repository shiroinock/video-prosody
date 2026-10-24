import difflib
import hashlib
import io
import json
import re
import subprocess
from importlib.metadata import version
from pathlib import Path


def measure(document, raw, audio, output):
    """Measure F0/RMS frames and estimate word times from corrected ASR text.

    Word times remain estimates, not forced alignment. Existing outputs are immutable.
    """
    import imageio_ffmpeg
    import librosa
    import numpy as np
    from janome.tokenizer import Tokenizer

    audio = Path(audio)
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    doc = document
    vid = doc["video_id"]
    import soundfile as sf

    # Decode once at the source's rate and channels, keeping floating-point samples.
    pcm = subprocess.check_output(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(audio),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "pcm_f32le",
            "-f",
            "wav",
            "pipe:1",
        ]
    )
    samples, sr = sf.read(io.BytesIO(pcm), dtype="float32", always_2d=True)
    if not samples.size:
        raise ValueError("No audio samples decoded")
    channels = np.ascontiguousarray(samples.T)
    hop = max(1, round(sr * 0.01))
    frame_length = max(2, round(sr * 0.064))
    print("Estimating pitch at the source sample rate, per channel…", flush=True)
    channel_f0, _, channel_prob = librosa.pyin(
        channels, sr=sr, fmin=65, fmax=600, frame_length=frame_length, hop_length=hop
    )
    # Analyze each channel rather than canceling out opposite-phase stereo signals.
    best = np.argmax(channel_prob, axis=0)
    indices = np.arange(channel_f0.shape[-1])
    f0 = channel_f0[best, indices]
    prob = channel_prob[best, indices]
    channel_rms = librosa.feature.rms(
        y=channels, frame_length=frame_length, hop_length=hop
    )[:, 0, :]
    rms = np.sqrt(np.mean(channel_rms.astype(np.float64) ** 2, axis=0))
    db = 20 * np.log10(np.maximum(rms, 1e-8))
    times = np.arange(len(f0)) * hop / sr
    frames = [
        {
            "time": round(float(t), 3),
            "pitch_hz": round(float(f), 2) if np.isfinite(f) else None,
            "pitch_channel": int(channel),
            "voiced_probability": round(float(p), 3),
            "rms_dbfs": round(float(d), 2),
        }
        for t, f, p, d, channel in zip(times, f0, prob, db, best)
    ]
    tok = Tokenizer()
    words = []
    rawseg = {s["id"]: s for s in raw["result"]["segments"]}
    for seg in doc["segments"]:
        text = re.sub(r"[\s。、！？!?]", "", seg["text"])
        if not text:
            continue
        chars = []
        spans = []
        for w in rawseg[seg["id"]]["words"]:
            cleaned = re.sub(r"[\s。、！？!?]", "", w["word"])
            for i, ch in enumerate(cleaned):
                chars.append(ch)
                spans.append(
                    (
                        w["start"] + (w["end"] - w["start"]) * i / len(cleaned),
                        w["start"] + (w["end"] - w["start"]) * (i + 1) / len(cleaned),
                    )
                )
        mapped = [None] * len(text)
        for tag, a, b, c, d in difflib.SequenceMatcher(
            None, "".join(chars), text, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                mapped[c:d] = spans[a:b]
            elif d > c:
                start = spans[a][0] if a < len(spans) else seg["end"]
                end = spans[b - 1][1] if b > a else start
                mapped[c:d] = [
                    (
                        start + (end - start) * i / (d - c),
                        start + (end - start) * (i + 1) / (d - c),
                    )
                    for i in range(d - c)
                ]
        tokens = [t.surface for t in tok.tokenize(text)]
        merged = []
        for t in tokens:
            if merged and re.fullmatch("[〜～ー]+", t):
                merged[-1] += t
            else:
                merged.append(t)
        offset = 0
        for t in merged:
            part = mapped[offset : offset + len(t)]
            offset += len(t)
            if not part:
                continue
            words.append(
                {
                    "id": len(words),
                    "segment_id": seg["id"],
                    "text": t,
                    "start": round(part[0][0], 4),
                    "end": round(part[-1][1], 4),
                    "verified": False,
                    "alignment": "ASR時刻と訂正文の文字対応による推定",
                }
            )
    result = {
        "video_id": vid,
        "title": doc["title"],
        "duration": len(samples) / sr,
        "source_audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "transcript_revision": doc["revision"],
        "transcript_sha256": hashlib.sha256(
            json.dumps(doc["segments"], ensure_ascii=False).encode()
        ).hexdigest(),
        "method": {
            "pitch": f"librosa {version('librosa')} pYIN",
            "sample_rate": sr,
            "channels": channels.shape[0],
            "audio_preprocessing": "source rate/channels; float32 PCM decode; no intermediate PCM16 file",
            "pitch_channel_selection": "per-channel pYIN; select highest voiced probability at each frame",
            "hop_seconds": hop / sr,
            "frame_seconds": frame_length / sr,
            "fmin_hz": 65,
            "fmax_hz": 600,
            "volume": "20 log10 RMS, full scale=1; dBFS; mean energy across original channels (no waveform downmix)",
            "gap": "adjacent word timestamp gap, not guaranteed silence",
            "alignment": "Janome; corrected text mapped onto original ASR character spans; estimated, not forced alignment",
            "pitch_source": "mixed source audio; background music may affect estimates",
        },
        "frames": frames,
        "words": words,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
    return result
