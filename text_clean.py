# text_clean.py
from __future__ import annotations
import re
from typing import Iterable, Optional, Set

_WS_RE = re.compile(r"[ \t]+")
_NL_WS_RE = re.compile(r" ?\n ?")
_ELLIPSIS_RE = re.compile(r"\.\.\.+")
_DOUBLE_DASH_RE = re.compile(r"--")
_PUNCT_WS_BEFORE = re.compile(r"\s+([,.:;!?…])")       # space before punctuation
_PUNCT_WS_AFTER = re.compile(r"([,.:;!?…])([^\s\W])")  # missing space after punctuation

# >=3 letters like "D i a d e m" or "S a p h r y n"
_SPELLED_WORD_RE = re.compile(r"\b(?:[A-Za-z]\s){2,}[A-Za-z]\b")

def _sentence_case(text: str) -> str:
    out = []
    cap_next = True
    for ch in text:
        if cap_next and ch.isalpha():
            out.append(ch.upper())
            cap_next = False
        else:
            out.append(ch)
        if ch in ".!?…\n":
            cap_next = True
    return "".join(out)

def _apply_glossary(text: str, glossary: Optional[Iterable[str]]) -> str:
    if not glossary:
        return text
    for term in sorted(glossary, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(term)}\b", term, flags=re.IGNORECASE)
    return text

def _collapse_spelled_words(
    text: str,
    glossary: Optional[Iterable[str]] = None,
    protected: Optional[Iterable[str]] = None
) -> str:
    gl_map = {g.lower(): g for g in (glossary or [])}
    prot_set: Set[str] = set(p.lower() for p in (protected or []))

    def repl(m: re.Match) -> str:
        raw = m.group(0)
        if raw.lower() in prot_set:
            return raw
        joined = raw.replace(" ", "")
        if joined.lower() in gl_map:
            return gl_map[joined.lower()]
        # simple “looks like a name/word” heuristic
        return joined[0].upper() + joined[1:] if joined[:1].isalpha() else joined

    return _SPELLED_WORD_RE.sub(repl, text)

def clean_transcript(
    raw: str,
    *,
    collapse_spelled: bool = True,      # join D i a d e m → Diadem in CONTENT
    enforce_sentence_case: bool = True,
    normalize_punct: bool = True,
    glossary: Optional[Iterable[str]] = None,
    protected_spelled: Optional[Iterable[str]] = None,
) -> str:
    if not raw:
        return raw

    text = raw

    # 1) whitespace tidy
    text = _WS_RE.sub(" ", text)
    text = _NL_WS_RE.sub("\n", text).strip()

    # 2) punctuation tidy
    if normalize_punct:
        text = _ELLIPSIS_RE.sub("…", text)        # ... → …
        text = _DOUBLE_DASH_RE.sub("—", text)     # -- → —
        text = _PUNCT_WS_BEFORE.sub(r"\1", text)  # no spaces before ,.!?…
        text = _PUNCT_WS_AFTER.sub(r"\1 \2", text)

    # 3) optionally join s p a c e d  w o r d s
    if collapse_spelled:
        text = _collapse_spelled_words(text, glossary=glossary, protected=protected_spelled)

    # 4) sentence case
    if enforce_sentence_case:
        text = _sentence_case(text)

    # 5) glossary casing (optional)
    text = _apply_glossary(text, glossary)

    return text
