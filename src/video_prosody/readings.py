"""Generate an immutable dictionary reading layer for a measured document."""

import hashlib
import json
import re
from importlib.metadata import version
from pathlib import Path

from .mora import hiragana, split


def add_readings(source, output):
    """Create a separate immutable Japanese dictionary pronunciation layer."""
    import fugashi
    import unidic_lite

    source = Path(source)
    out = Path(output)
    if out.exists():
        raise FileExistsError(out)
    data = json.loads(source.read_text())
    tagger = fugashi.Tagger("-d " + unidic_lite.DICDIR)
    entries = {}
    # Analyse whole utterances to retain context for homographs and particles.
    for sid in sorted({w["segment_id"] for w in data["words"]}):
        words = [w for w in data["words"] if w["segment_id"] == sid]
        text = "".join(w["text"] for w in words)
        tokens = []
        offset = 0
        for t in tagger(text):
            start = text.find(t.surface, offset)
            end = start + len(t.surface)
            offset = end
            tokens.append((start, end, t))
        offset = 0
        for w in words:
            start = offset
            end = start + len(w["text"])
            offset = end
            pron = []
            reading = []
            flags = []
            for a, b, t in tokens:
                if b <= start or a >= end:
                    continue
                p = getattr(t.feature, "pron", None)
                r = getattr(t.feature, "kana", None)
                if a < start or b > end:
                    # Never split a dictionary pronunciation by character count.
                    flags.append("辞書の単語境界と計測の境界が異なるため、読みは要確認")
                    piece = text[max(a, start) : min(b, end)]
                    if re.fullmatch("[ぁ-ゖァ-ヶー〜～]+", piece):
                        p = r = piece
                    else:
                        subtokens = list(tagger(piece))
                        ps = [getattr(st.feature, "pron", None) for st in subtokens]
                        rs = [getattr(st.feature, "kana", None) for st in subtokens]
                        p = (
                            "".join(ps)
                            if ps and all(x and x != "*" for x in ps)
                            else None
                        )
                        r = "".join(rs) if rs and all(x and x != "*" for x in rs) else p
                if not p or p == "*":
                    flags.append("辞書外語：仮名表記からの読み、または手動入力が必要")
                    p = r = (
                        t.surface
                        if re.fullmatch("[ぁ-ゖァ-ヶー〜～]+", t.surface)
                        else None
                    )
                if p:
                    if "〜" in p or "～" in p:
                        flags.append(
                            "引き伸ばし記号を除外。伸ばした時間は単語の時刻で確認"
                        )
                    pron.append(hiragana(re.sub("[〜～]", "", p)))
                    reading.append(hiragana(re.sub("[〜～]", "", r or p)))
                else:
                    pron.append("")
                    reading.append("")
            pronunciation = "".join(pron) or None
            dictionary_reading = "".join(reading) or None
            try:
                units = split(pronunciation) if pronunciation else []
            except ValueError:
                pronunciation = None
                units = []
                flags.append("発音を自動分解できません")
            entries[str(w["id"])] = {
                "text": w["text"],
                "reading": dictionary_reading,
                "pronunciation": pronunciation,
                "moras": units,
                "mora_count": len(units) if units else None,
                "needs_review": bool(flags),
                "notes": list(dict.fromkeys(flags)),
            }
    result = {
        "video_id": data["video_id"],
        "dictionary": f"unidic-lite {version('unidic-lite')}",
        "analyser": f"fugashi {version('fugashi')}",
        "source_measured_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "words": entries,
        "method": "UniDic context pronunciation; kana fallback for unknown forms. No per-mora times inferred.",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
