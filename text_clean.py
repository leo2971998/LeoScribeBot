# text_clean.py
# Lightweight, dependency-free(ish) transcript cleaner for ASR output.
# Use: polished = clean_transcript(text)

from __future__ import annotations
import re
from typing import Iterable, Optional

# Optional niceties — if not installed, we fall back gracefully.
try:
    from ftfy import fix_text  # unicode/quotes cleanup
except Exception:
    fix_text = lambda s: s  # no-op if ftfy isn't available

try:
    import smartypants as _sp  # curly quotes / dashes
except Exception:
    _sp = None

# Match sequences like "D i a d e m", "R y a n", "L O L" (>=3 letters)
# Allow one or more spaces between letters to be robust to ASR spacing.
_SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z]\s+){2,}[A-Za-z]\b")

# Basic punctuation/spacing normalisation
_SPACES_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_NO_SPACE_AFTER_ENDER = re.compile(r"([.!?])([^\s])")
_MULTI_SPACE = re.compile(r"\s{2,}")
_SPACED_APOSTROPHE = re.compile(r"\s+'\s*|\s*'\s+")  # e.g., "it ' s" → "it's"
_DASH_FIX = re.compile(r"\s*[-–—]{1,2}\s*")          # normalize dashes to " — "

def _collapse_spelled_words(s: str, protected: set[str] | None = None) -> str:
    """Join space-separated letters into words, except those in `protected`."""
    if not s:
        return s

    def repl(m: re.Match) -> str:
        token = m.group(0)
        joined = re.sub(r"\s+", "", token)
        if protected and (token in protected or joined in protected):
            return token
        return joined

    return _SPACED_LETTERS.sub(repl, s)

def _normalize_spacing_punct(s: str) -> str:
    """Fix spacing around punctuation and dashes; collapse multiple spaces."""
    if not s:
        return s
    s = _SPACES_BEFORE_PUNCT.sub(r"\1", s)
    s = _NO_SPACE_AFTER_ENDER.sub(r"\1 \2", s)
    # Normalize em/en dashes to spaced em-dash style (ASCII-safe if smart quotes disabled)
    s = _DASH_FIX.sub(" — ", s)
    # Fix "it ' s" → "it's"
    s = _SPACED_APOSTROPHE.sub("'", s)
    # Collapse extra spaces
    s = _MULTI_SPACE.sub(" ", s)
    return s.strip()

def _sentence_case_once(chunks: list[str]) -> list[str]:
    """Capitalize the first alphabetic character in each chunk (sentence)."""
    out = []
    for c in chunks:
        i = 0
        # Preserve leading whitespace
        while i < len(c) and c[i].isspace():
            i += 1
        # Find first alpha to capitalize
        j = i
        while j < len(c) and not c[j].isalpha():
            j += 1
        if j < len(c):
            c = c[:j] + c[j].upper() + c[j+1:]
        out.append(c)
    return out

def _split_sentences(s: str) -> list[str]:
    """Lightweight split on sentence enders followed by whitespace."""
    # Keep enders . ! ? and split when followed by space/newline
    return re.split(r"(?<=[.!?])\s+", s)

def clean_transcript(
    text: str,
    *,
    collapse_spelled: bool = True,
    enforce_sentence_case: bool = True,
    normalize_punct: bool = True,
    glossary: Optional[Iterable[str]] = None,        # e.g. ["Diadem", "Vurndharth", "Saphryn"]
    protected_spelled: Optional[Iterable[str]] = None, # e.g. ["S M H"] to keep spaced
    smart_quotes: bool = False                         # set True if you installed smartypants
) -> str:
    """
    Post-process raw ASR text into something cleaner:

    - Collapses spaced-out letters ("D i a d e m" → "Diadem")
    - Normalizes punctuation & spacing
    - Capitalizes sentence starts
    - Optionally enforces preferred casing for specific terms (glossary)
    - Optionally applies smart quotes/dashes (if `smartypants` installed)

    All operations are local (no network calls).
    """
    if not text:
        return text

    # Unicode cleanup if ftfy exists (smart quotes, broken accents, etc.)
    out = fix_text(text)

    if collapse_spelled:
        out = _collapse_spelled_words(out, set(protected_spelled or []))

    if normalize_punct:
        out = _normalize_spacing_punct(out)

    if enforce_sentence_case:
        sentences = _split_sentences(out)
        out = " ".join(_sentence_case_once(sentences))

    if glossary:
        # Canonicalize key terms’ casing (case-insensitive word-boundary replace)
        for term in glossary:
            out = re.sub(rf"\b{re.escape(term)}\b", term, out, flags=re.IGNORECASE)

    if smart_quotes and _sp:
        out = _sp.smartypants(out)

    return out
