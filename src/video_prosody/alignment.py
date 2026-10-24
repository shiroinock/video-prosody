"""Local Japanese phoneme CTC alignment, grouped into mora spans (immutable)."""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SYMBOLS = [
    "[PAD]",
    "[BOS]",
    "[EOS]",
    "pau",
    "N",
    "a",
    "b",
    "by",
    "ch",
    "cl",
    "d",
    "dy",
    "e",
    "f",
    "g",
    "gw",
    "gy",
    "h",
    "hy",
    "i",
    "j",
    "k",
    "kw",
    "ky",
    "m",
    "my",
    "n",
    "ny",
    "o",
    "p",
    "py",
    "r",
    "ry",
    "s",
    "sh",
    "t",
    "ts",
    "ty",
    "u",
    "v",
    "w",
    "y",
    "z",
]


def phones(mora, last_vowel=None):
    if mora == "は":
        return ["h", "a"]
    if mora == "へ":
        return ["h", "e"]
    if mora == "ー":
        if last_vowel is None:
            raise ValueError("長音の直前母音がありません")
        return [last_vowel]
    import pyopenjtalk

    p = pyopenjtalk.g2p(mora).split()
    p = [
        s.lower() if s in ["A", "I", "U", "E", "O"] else s
        for s in p
        if s not in ["sil", "pau"]
    ]
    if not p or any(s not in SYMBOLS for s in p):
        raise ValueError(f"Unsupported phonemes: {mora} {p}")
    return p


def align(logits, ids):
    if (
        logits.ndim != 2
        or not len(logits)
        or not ids
        or logits.shape[1] != len(SYMBOLS)
    ):
        raise ValueError("Expected nonempty CTC frames and phoneme sequence")
    logits = logits.astype(np.float64)
    logits -= logits.max(axis=1, keepdims=True)
    logp = logits - np.log(np.exp(logits).sum(axis=1, keepdims=True))
    labels = np.array([0] + [x for i in ids for x in (i, 0)], dtype=int)
    S = len(labels)
    T = len(logp)
    prev = np.full(S, -np.inf)
    prev[0] = 0
    back = np.zeros((T, S), dtype=np.int8)
    skips = np.array(
        [s > 1 and labels[s] != 0 and labels[s] != labels[s - 2] for s in range(S)]
    )
    for t in range(T):
        candidates = np.stack(
            [prev, np.r_[-np.inf, prev[:-1]], np.r_[[-np.inf, -np.inf], prev[:-2]]]
        )
        candidates[2, ~skips] = -np.inf
        choose = np.argmax(candidates, axis=0)
        back[t] = choose
        prev = candidates[choose, np.arange(S)] + logp[t, labels]
    s = S - 1 if prev[-1] > prev[-2] else S - 2
    if not np.isfinite(prev[s]):
        raise ValueError("音素列をアラインできません")
    path = np.empty(T, dtype=int)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(back[t, s])
    spans = []
    for i, token in enumerate(ids):
        f = np.flatnonzero(path == i * 2 + 1)
        if not len(f):
            raise ValueError("音素が欠落しています")
        spans.append(
            {
                "frame_start": int(f[0]),
                "frame_end": int(f[-1] + 1),
                "score": float(np.exp(logp[f, token]).mean()),
                "phoneme": SYMBOLS[token],
            }
        )
    return spans


def align_moras(document, audio, model, output, active_manifest=None):
    """Align a word/pronunciation document to audio without overwriting estimates."""
    import imageio_ffmpeg
    import onnxruntime as ort

    ort.disable_telemetry_events()
    doc = document
    audio = Path(audio)
    model = Path(model)
    OUT = Path(output)
    VID = doc["video_id"]
    if OUT.exists():
        raise FileExistsError(OUT)
    if (
        active_manifest is not None
        and Path(active_manifest).resolve().parent != OUT.resolve().parent
    ):
        raise ValueError("Active manifest must share the alignment directory")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    opts.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
    meta = sess.get_modelmeta().custom_metadata_map
    sr = int(meta["sample_rate"])
    hop = int(meta["hop_length"])
    step = hop / sr
    pcm = subprocess.check_output(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-i",
            str(audio),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(sr),
            "-",
        ]
    )
    y = np.frombuffer(pcm, dtype="<f4").copy()
    morarecords = []
    groups = []
    failures = []
    for sid in sorted({w["segment_id"] for w in doc["words"]}):
        ws = [w for w in doc["words"] if w["segment_id"] == sid]
        units = []
        phonemes = []
        vowel = None
        try:
            for w in ws:
                if not w["moras"]:
                    raise ValueError(f"発音未取得: {w['text']}")
                for i, m in enumerate(w["moras"]):
                    p = phones(m, vowel)
                    vowel = next(
                        (x for x in reversed(p) if x in "aiueo" and len(x) == 1), vowel
                    )
                    units.append(
                        {
                            "word_id": w["id"],
                            "word_text": w["text"],
                            "mora_index": i,
                            "label": m,
                            "phoneme_start": len(phonemes),
                            "phoneme_end": len(phonemes) + len(p),
                            "phonemes": p,
                            "pronunciation": w["pronunciation"],
                            "word_start": w["start"],
                            "word_end": w["end"],
                            "reading_requires_review": w["dictionary"].get(
                                "needs_review", False
                            )
                            and not w["pronunciation_verified"],
                        }
                    )
                    phonemes += p
            a = max(0, min(w["start"] for w in ws) - 0.35)
            b = min(len(y) / sr, max(w["end"] for w in ws) + 0.35)
            clip = y[int(a * sr) : int(b * sr)][None, :]
            logits = sess.run(["output"], {"input": clip})[0][0]
            spans = align(logits, [SYMBOLS.index(p) for p in phonemes])
            # Retain complete phone spans; use adjacent label-gap midpoints only for short gaps.
            rawspans = []
            for u in units:
                ss = spans[u["phoneme_start"] : u["phoneme_end"]]
                rawspans.append(
                    [
                        a + ss[0]["frame_start"] * step,
                        a + ss[-1]["frame_end"] * step,
                        float(np.mean([s["score"] for s in ss])),
                    ]
                )
            starts = [v[0] for v in rawspans]
            ends = [v[1] for v in rawspans]
            for i in range(len(units) - 1):
                if 0 <= starts[i + 1] - ends[i] <= 0.2:
                    mid = (ends[i] + starts[i + 1]) / 2
                    ends[i] = mid
                    starts[i + 1] = mid
            for i, u in enumerate(units):
                flags = []
                if rawspans[i][2] < 0.3:
                    flags.append("低いCTC一致度：音声で境界を確認")
                if u["reading_requires_review"]:
                    flags.append("読みが未確認：音素列も要確認")
                if ends[i] - starts[i] < 0.04:
                    flags.append("非常に短い推定区間")
                morarecords.append(
                    {
                        **u,
                        "id": len(morarecords),
                        "segment_id": sid,
                        "start": round(starts[i], 4),
                        "end": round(min(ends[i], len(y) / sr), 4),
                        "ctc_score": round(rawspans[i][2], 4),
                        "verified": False,
                        "review_flags": flags,
                        "raw_ctc_span": {
                            "start": round(rawspans[i][0], 4),
                            "end": round(rawspans[i][1], 4),
                        },
                    }
                )
            groups.append(
                {
                    "segment_id": sid,
                    "crop_start": a,
                    "crop_end": b,
                    "phonemes": phonemes,
                    "phoneme_alignment": [
                        {
                            **p,
                            "start": round(a + p["frame_start"] * step, 4),
                            "end": round(a + p["frame_end"] * step, 4),
                        }
                        for p in spans
                    ],
                }
            )
            print(
                f"segment {sid}: {len(units)} moras, score {np.mean([x[2] for x in rawspans]):.3f}",
                flush=True,
            )
        except ValueError as e:
            failures.append({"segment_id": sid, "error": str(e)})
            print("Unaligned:", sid, str(e), flush=True)
    result = {
        "video_id": VID,
        "title": doc["title"],
        "duration": len(y) / sr,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_word_revision": doc["revision"],
        "source_words": [
            {
                k: w.get(k)
                for k in [
                    "id",
                    "text",
                    "start",
                    "end",
                    "pronunciation",
                    "pronunciation_verified",
                ]
            }
            for w in doc["words"]
        ],
        "source_audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "method": {
            "model": "darashi/narabas-models narabas-v0.onnx",
            "source": "https://github.com/darashi/narabas",
            "g2p": "pyopenjtalk; kana は/へ use ha/he (not particle wa/e); long vowel uses preceding vowel",
            "alignment": "standard CTC Viterbi, blank/label states, explicit repeated-label constraint",
            "frame_step_seconds": step,
            "boundaries": "first/last aligned phoneme frames per mora; midpoint of adjacent gaps <=0.2s",
            "segment_padding_seconds": 0.35,
            "runtime": "onnxruntime CPU, local only",
            "score": "mean posterior on aligned phone frames; not calibrated accuracy",
            "equal_duration_split": False,
        },
        "moras": morarecords,
        "segments": groups,
        "unaligned_segments": failures,
    }
    with OUT.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
    if active_manifest is not None:
        manifest = Path(active_manifest)
        if manifest.resolve().parent != OUT.resolve().parent:
            raise ValueError("Active manifest must share the alignment directory")
        tmp = manifest.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "active": OUT.name,
                    "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
                },
                indent=2,
            )
            + "\n"
        )
        tmp.replace(manifest)
    return result
