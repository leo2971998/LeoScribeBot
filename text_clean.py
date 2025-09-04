# text_clean.py
# General, dependency-light transcript cleaner (no hard-coded vocab).
# Use: polished = clean_transcript(text)

from __future__ import annotations
import re
from typing import Optional, Iterable

# Optional niceties — if not installed, we fall back gracefully.
try:
    from ftfy import fix_text  # unicode/quotes cleanup
except Exception:
    fix_text = lambda s: s  # no-op if ftfy isn't available

try:
    import smartypants as _sp  # curly quotes / dashes
except Exception:
    _sp = None

# ─────────────────────────────
# Core regex
# ─────────────────────────────

# Join sequences like "D i a d e m", "R y a n", "L O L" (>=3 letters)
_SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z]\s+){2,}[A-Za-z]\b")

# Basic punctuation/spacing normalization
_SPACES_BEFORE_PUNCT  = re.compile(r"\s+([,.;:!?])")
_NO_SPACE_AFTER_ENDER = re.compile(r"([.!?])([^\s])")
_MULTI_SPACE          = re.compile(r"\s{2,}")
_SPACED_APOSTROPHE    = re.compile(r"\s+'\s*|\s*'\s+")  # "it ' s" → "it's"
_DASH_FIX             = re.compile(r"\s*[-–—]{1,2}\s*") # normalize to spaced em-dash

_WORD = re.compile(r"\S+")

# ─────────────────────────────
# Helpers
# ─────────────────────────────

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
    s = _DASH_FIX.sub(" — ", s)       # normalize to spaced em-dash
    s = _SPACED_APOSTROPHE.sub("'", s)
    s = _MULTI_SPACE.sub(" ", s)
    return s.strip()

def _split_sentences(s: str) -> list[str]:
    """Split on sentence enders (. ! ?) keeping the ender."""
    return re.split(r"(?<=[.!?])\s+", s)

def _sentence_case_once(chunks: list[str]) -> list[str]:
    """Capitalize the first alphabetic character in each chunk."""
    out = []
    for c in chunks:
        i = 0
        while i < len(c) and c[i].isspace():
            i += 1
        j = i
        while j < len(c) and not c[j].isalpha():
            j += 1
        if j < len(c):
            c = c[:j] + c[j].upper() + c[j+1:]
        out.append(c)
    return out

def _word_count(text: str) -> int:
    return len([m.group(0) for m in _WORD.finditer(text)])

def _insert_commas_by_clause_length(
    s: str,
    *,
    min_left_words: int = 8,
    min_right_words: int = 4,
    max_commas: int = 2,
) -> str:
    """
    Purely structural comma insertion:
    - Look for natural breakpoints inside long runs.
    - A breakpoint is between tokens where:
        • left clause has ≥ min_left_words
        • right clause has ≥ min_right_words
        • the char before the break isn't already punctuation
        • the next token starts with a lowercase letter (indicating continuation)
    - Insert at most `max_commas` per sentence.
    No keyword lists; language-agnostic heuristic.
    """
    if not s:
        return s

    # Work sentence by sentence so we don't create comma chains across boundaries.
    parts = _split_sentences(s)
    new_parts: list[str] = []

    for sent in parts:
        if not sent or _word_count(sent) < (min_left_words + min_right_words + 2):
            new_parts.append(sent)
            continue

        # Tokenize with positions
        tokens = list(_WORD.finditer(sent))
        if len(tokens) < 3:
            new_parts.append(sent)
            continue

        inserted = 0
        offset = 0  # running offset from prior insertions
        sent_mutable = sent

        # Scan for breakpoints; allow multiple (up to max_commas)
        for i in range(1, len(tokens)):
            if inserted >= max_commas:
                break

            t = tokens[i]
            cut = t.start() + offset

            # Skip if at very start
            if cut <= 0 or cut >= len(sent_mutable):
                continue

            # Preceding character cannot be punctuation
            prev_char = sent_mutable[cut - 1]
            if prev_char in ",;:—-(":
                continue

            # Next token should likely be a continuation, not a new sentence:
            tok_text = t.group(0)
            if not tok_text or not tok_text[0].islower():
                # Require lowercase start for "continuation" feel (generic).
                continue

            # Word counts on either side
            left_wc = _word_count(sent_mutable[:cut])
            right_wc = _word_count(sent_mutable[cut:])

            if left_wc >= min_left_words and right_wc >= min_right_words:
                # Insert comma at boundary, cleaning spaces around insertion.
                left = sent_mutable[:cut].rstrip()
                right = sent_mutable[cut:].lstrip()
                new_sent = f"{left}, {right}"

                # Update bookkeeping: how many chars did we add?
                delta = len(new_sent) - len(sent_mutable)
                sent_mutable = new_sent
                offset += delta
                inserted += 1

        new_parts.append(sent_mutable)

    # Re-join sentences with a single space (they already include enders).
    s2 = " ".join(new_parts)
    # Final spacing cleanup
    s2 = _normalize_spacing_punct(s2)
    return s2

# ─────────────────────────────
# Public API
# ─────────────────────────────

def clean_transcript(
    text: str,
    *,
    collapse_spelled: bool = True,
    enforce_sentence_case: bool = True,
    normalize_punct: bool = True,
    glossary: Optional[Iterable[str]] = None,          # you can pass terms at runtime if you want
    protected_spelled: Optional[Iterable[str]] = None, # terms to *not* collapse if needed
    smart_quotes: bool = False,                        # enable if `smartypants` installed
    light_punct: bool = True,                          # structural commas (no keyword lists)
    min_left_words: int = 8,
    min_right_words: int = 4,
    max_commas_per_sentence: int = 2,
) -> str:
    """
    General ASR post-processing:
      • Join spaced letters  (D i a d e m → Diadem)
      • Normalize spacing/punctuation
      • (Optional) add ≤ `max_commas_per_sentence` commas using clause-length heuristics
      • Sentence-case the start of sentences
      • Optional glossary to enforce casing (runtime, not hard-coded)
      • Optional smart quotes/dashes

    All offline; no word- or story-specific lists.
    """
    if not text:
        return text

    out = fix_text(text)

    if collapse_spelled:
        out = _collapse_spelled_words(out, set(protected_spelled or []))

    if normalize_punct:
        out = _normalize_spacing_punct(out)

    if light_punct:
        out = _insert_commas_by_clause_length(
            out,
            min_left_words=min_left_words,
            min_right_words=min_right_words,
            max_commas=max_commas_per_sentence,
        )

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
