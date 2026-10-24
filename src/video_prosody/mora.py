"""Kana pronunciation to mora units; no duration allocation is inferred."""

import re
import unicodedata

SMALL = set("ゃゅょぁぃぅぇぉゎ")


def hiragana(value):
    value = unicodedata.normalize("NFKC", value)
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)


def split(value):
    value = hiragana(value).strip()
    if not value:
        return []
    if not re.fullmatch("[ぁ-ゖー]+", value):
        raise ValueError(
            "発音はひらがな・カタカナ・長音符で入力してください（〜や句読点は除いてください）"
        )
    units = []
    for ch in value:
        if ch in SMALL and units and units[-1] not in ("ん", "っ", "ー"):
            units[-1] += ch
        else:
            units.append(ch)
    return units
