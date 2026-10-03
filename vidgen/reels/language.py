"""Language inference and caption sanity. Idea text beats a default --language=english."""
from __future__ import annotations

import re

from vidgen.reels.duration import normalize_language

_BN_MARKERS = ("bengali", "bangla", "বাংলা", "bangladeshi", "banglish", "dhaka bangla")
# Do not use re.I — [A-Z]{8,} must mean ALL-CAPS junk, not "Someone".
_GARBLED_TOKEN = re.compile(
    r"(.)\1{3,}|[A-Z]{8,}|[aeiouAEIOU]{5,}|[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{7,}"
)
_KNOWN_JUNK = {
    "youroscent", "neverer", "louded", "alwaysatheree", "bely", "blyely",
    "ligg", "shang", "stiof", "ayeeye", "lolojww", "heag", "walliam",
}


def infer_language(language: str = "", idea: str = "") -> str:
    """
    If the idea asks for Bengali and --language was left at the english default,
    Bengali wins. Explicit --language=english with no Bengali in the idea stays English.
    """
    idea_l = (idea or "").lower()
    lang_l = (language or "").strip().lower()
    idea_bn = has_bengali(idea) or any(k in idea_l for k in _BN_MARKERS)
    explicit_en = lang_l in {"english", "en", "eng"}
    explicit_bn = lang_l in {"bn", "bangla", "bengali", "বাংলা", "bengali_english",
                             "bilingual", "banglish", "bengali+english"}
    if idea_bn and (not lang_l or explicit_en or lang_l == "auto"):
        if "english" in idea_l and any(k in idea_l for k in ("bengali", "bangla", "বাংলা", "banglish")):
            return "bengali_english"
        return "bengali"
    if explicit_bn:
        return normalize_language(language)
    if idea_bn:
        return "bengali"
    return normalize_language(language or "english")


def has_bengali(text: str) -> bool:
    return bool(re.search(r"[\u0980-\u09FF]", text or ""))


def is_garbled_caption(text: str, language: str = "") -> bool:
    """Catch Veo/TTS/polish garbage like 'Youroscent. Neverer louded'."""
    t = (text or "").strip()
    if not t:
        return True
    lang = normalize_language(language) if language else ""
    if lang.startswith("bengali") and not has_bengali(t):
        return True
    tokens = re.findall(r"[A-Za-z\u0980-\u09FF']+", t)
    if not tokens:
        return True
    bad = 0
    for tok in tokens:
        low = tok.lower()
        if low in _KNOWN_JUNK or _GARBLED_TOKEN.search(tok):
            bad += 2
        elif tok.isascii() and len(tok) > 14:
            bad += 1
        elif tok.isascii() and len(tok) > 6 and not re.search(r"[aeiou]", low):
            bad += 1
    return (bad / max(1, len(tokens))) >= 0.25
