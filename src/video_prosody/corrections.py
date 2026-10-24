#!/usr/bin/env python3
"""Apply scoped spelling rules and optional explicit contextual edits; never modify raw input."""

import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def approximate_time(segment, before):
    """Use original ASR character/word alignment, never invent new word timestamps."""
    words = segment.get("words", [])
    joined = "".join(w["word"] for w in words)
    offset = segment["text"].find(before)
    if joined != segment["text"] or offset < 0 or segment["text"].count(before) != 1:
        return {
            "start": segment["start"],
            "end": segment["end"],
            "basis": "raw_segment",
        }
    cursor = 0
    overlap = []
    for word in words:
        end = cursor + len(word["word"])
        if cursor < offset + len(before) and end > offset:
            overlap.append(word)
        cursor = end
    if not overlap:
        return {
            "start": segment["start"],
            "end": segment["end"],
            "basis": "raw_segment",
        }
    return {
        "start": overlap[0]["start"],
        "end": overlap[-1]["end"],
        "basis": "overlapping_raw_asr_words",
    }


def replace_spans(text, changes):
    changes = sorted(changes, key=lambda c: c["input_span"][0])
    end = 0
    shift = 0
    pieces = []
    for change in changes:
        start, stop = change["input_span"]
        if start < end or text[start:stop] != change["before"]:
            raise ValueError("Overlapping edits or mismatched original text")
        pieces.extend([text[end:start], change["after"]])
        out_start = start + shift
        change["output_span"] = [out_start, out_start + len(change["after"])]
        shift += len(change["after"]) - (stop - start)
        end = stop
    pieces.append(text[end:])
    return "".join(pieces)


def spelling_pass(raw, dictionary):
    channel = (raw["metadata"].get("acquisition") or {}).get("channel_id")
    video_id = (raw["metadata"].get("acquisition") or {}).get("video_id")
    result = []
    history = []
    for segment in raw["result"]["segments"]:
        text = segment["text"]
        changes = []
        seen = set()
        for rule in dictionary["rules"]:
            if rule.get("channel_id") and rule["channel_id"] != channel:
                continue
            if rule.get("video_ids") and video_id not in rule["video_ids"]:
                continue
            if "segment_ids" in rule and segment["id"] not in rule["segment_ids"]:
                continue
            aliases = "|".join(re.escape(alias) for alias in rule["aliases"])
            for pattern in rule["patterns"]:
                for match in re.finditer(pattern.replace("{aliases}", aliases), text):
                    span = match.span("term")
                    if span in seen:
                        continue
                    seen.add(span)
                    before = match.group("term")
                    changes.append(
                        {
                            "stage": "spelling",
                            "rule_id": rule["id"],
                            "segment_id": segment["id"],
                            "before": before,
                            "after": rule["canonical"],
                            "input_span": list(span),
                            "reason": rule["reason"],
                            "time": approximate_time(segment, before),
                        }
                    )
        corrected = replace_spans(text, changes)
        result.append(
            {
                "id": segment["id"],
                "start": segment["start"],
                "end": segment["end"],
                "raw_text": text,
                "text": corrected,
            }
        )
        history.extend(changes)
    return result, history


def contextual_pass(raw, spelling_segments, plan):
    video_id = (raw["metadata"].get("acquisition") or {}).get("video_id")
    if plan["video_id"] != video_id:
        raise ValueError("Context edit plan is for a different video")
    raw_by_id = {s["id"]: s for s in raw["result"]["segments"]}
    existing_ids = {s["id"] for s in spelling_segments}
    if any(e["segment_id"] not in existing_ids for e in plan["edits"]):
        raise ValueError("Context edit refers to an unknown segment")
    result = []
    history = []
    for segment in spelling_segments:
        text = segment["text"]
        changes = []
        for edit in plan["edits"]:
            if edit["segment_id"] != segment["id"]:
                continue
            if not edit["before"] or text.count(edit["before"]) != 1:
                raise ValueError(
                    f"Expected exactly one match for context edit {edit['id']}"
                )
            start = text.index(edit["before"])
            changes.append(
                {
                    "stage": "context",
                    "rule_id": edit["id"],
                    "segment_id": segment["id"],
                    "before": edit["before"],
                    "after": edit["after"],
                    "input_span": [start, start + len(edit["before"])],
                    "kind": edit["kind"],
                    "confidence": edit["confidence"],
                    "reason": edit["reason"],
                    "audio_verified": False,
                    "requires_audio_review": edit["requires_audio_review"],
                    "time": approximate_time(raw_by_id[segment["id"]], edit["before"]),
                }
            )
        result.append(
            {**segment, "spelling_text": text, "text": replace_spans(text, changes)}
        )
        history.extend(changes)
    return result, history


def correct_file(raw_path, dictionary_path, context_path=None, output_dir=None):
    raw_path = Path(raw_path).resolve()
    raw_bytes = raw_path.read_bytes()
    raw = json.loads(raw_bytes)
    if "".join(s["text"] for s in raw["result"]["segments"]) != raw["result"]["text"]:
        raise ValueError(
            "Segment text does not reconstruct full raw text; align before correcting"
        )
    dictionary_bytes = Path(dictionary_path).read_bytes()
    dictionary = json.loads(dictionary_bytes)
    spelling_segments, spelling_history = spelling_pass(raw, dictionary)
    contextual_segments = None
    contextual_history = []
    plan = None
    context_bytes = None
    if context_path is not None:
        context_bytes = Path(context_path).read_bytes()
        plan = json.loads(context_bytes)
        contextual_segments, contextual_history = contextual_pass(
            raw, spelling_segments, plan
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent = Path(output_dir) if output_dir else raw_path.parent / "corrections"
    destination = parent / f"{stamp}-{uuid.uuid4().hex[:8]}"
    destination.mkdir(parents=True, exist_ok=False)
    provenance = {
        "raw_path": str(raw_path),
        "raw_sha256": sha256(raw_bytes),
        "created_at_utc": stamp,
        "human_verified": False,
        "dictionary_sha256": sha256(dictionary_bytes),
        "processor_sha256": sha256(Path(__file__).read_bytes()),
        "word_timestamps": "Unmodified ASR word timings remain in raw.json; corrected text has no new word alignment.",
    }
    for name, segments in (
        ("normalized", spelling_segments),
        ("contextual", contextual_segments),
    ):
        if segments is None:
            continue
        text = "".join(s["text"] for s in segments)
        (destination / f"{name}.json").write_text(
            json.dumps(
                {
                    "metadata": {**provenance, "stage": name},
                    "text": text,
                    "segments": segments,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        (destination / f"{name}.txt").write_text(text + "\n")
    audit = {
        "metadata": provenance,
        "dictionary_snapshot": dictionary,
        "context_plan_snapshot": plan,
        "context_plan_sha256": sha256(context_bytes) if context_bytes else None,
        "offset_convention": "Python Unicode character offsets, zero-based, end-exclusive; spans reference each stage's segment text.",
        "counts": {
            "spelling": len(spelling_history),
            "context": len(contextual_history),
        },
        "changes": spelling_history + contextual_history,
        "review_flags": plan.get("review_flags", []) if plan else [],
    }
    (destination / "correction-history.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    if raw_path.read_bytes() != raw_bytes:
        raise RuntimeError("Raw input changed during correction")
    print(f"Corrections saved: {destination}")
    print(f"Spelling: {len(spelling_history)}; context: {len(contextual_history)}")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_json", type=Path)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--context-edits", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    correct_file(args.raw_json, args.dictionary, args.context_edits, args.output_dir)


if __name__ == "__main__":
    main()
