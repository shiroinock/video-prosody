#!/usr/bin/env python3
"""Apply a reviewed punctuation plan. Only punctuation insertion is allowed."""

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


def punctuate(source, plan_path):
    source = source.resolve()
    source_bytes = source.read_bytes()
    data = json.loads(source_bytes)
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    segments = data["segments"]
    edits = {edit["id"]: edit for edit in plan["segments"]}
    if len(edits) != len(plan["segments"]):
        raise ValueError("Duplicate segment IDs")
    ids = {s["id"] for s in segments}
    if not set(edits).issubset(ids) or not set(plan["paragraph_end_segments"]).issubset(
        ids
    ):
        raise ValueError("Unknown segment ID")
    output = []
    history = []
    readable = []
    for segment in segments:
        before = segment["text"]
        after = before
        if segment["id"] in edits:
            edit = edits[segment["id"]]
            if edit["before"] != before:
                raise ValueError(f"Stale punctuation plan for segment {segment['id']}")
            after = edit["after"]
            for tag, i, j, a, b in SequenceMatcher(
                None, before, after, autojunk=False
            ).get_opcodes():
                if tag == "equal":
                    continue
                if tag != "insert" or any(c not in "。、！？?!" for c in after[a:b]):
                    raise ValueError(
                        "Plan changes words rather than inserting punctuation"
                    )
                history.append(
                    {
                        "segment_id": segment["id"],
                        "operation": "insert_punctuation",
                        "before": "",
                        "after": after[a:b],
                        "input_span": [i, j],
                        "output_span": [a, b],
                        "segment_time": {
                            "start": segment["start"],
                            "end": segment["end"],
                        },
                        "reason": "文脈に基づく読みやすさのための句読点。音声の間を測定したものではない。",
                    }
                )
        output.append({**segment, "unpunctuated_text": before, "text": after})
        readable.append(after)
        if segment["id"] in plan["paragraph_end_segments"]:
            readable.append("\n\n")
            history.append(
                {
                    "segment_id": segment["id"],
                    "operation": "paragraph_break",
                    "after": "\n\n",
                    "reason": "読みやすさのための段落区切り",
                }
            )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = source.parent / "punctuation" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    destination.mkdir(parents=True, exist_ok=False)
    metadata = {
        **data["metadata"],
        "stage": "punctuated",
        "audio_verified": False,
        "punctuation_source": str(source),
        "punctuation_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "punctuation_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "processor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "punctuation_created_at_utc": stamp,
    }
    (destination / "punctuated.json").write_text(
        json.dumps(
            {
                "metadata": metadata,
                "text": "".join(s["text"] for s in output),
                "segments": output,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    (destination / "readable.txt").write_text("".join(readable).rstrip() + "\n")
    (destination / "punctuation-history.json").write_text(
        json.dumps(
            {
                "metadata": metadata,
                "plan_snapshot": plan,
                "changes": history,
                "offset_convention": "Python Unicode character offsets, zero-based, end-exclusive; per segment.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    assert source.read_bytes() == source_bytes
    print(destination)
    print(
        "Punctuation insertions:",
        sum(e["operation"] == "insert_punctuation" for e in history),
    )
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_json", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    punctuate(args.source_json, args.plan)
