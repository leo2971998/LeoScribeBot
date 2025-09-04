# text_clean.py
import re

# Optional niceties: work even if not installed
try:
    from ftfy import fix_text       # unicode cleanup
except Exception:
    fix_text = lambda s: s

try:
    import smartypants as _sp       # curly quotes/dashes (optional)
except Exception:
    _sp = None

# Join spaced letters like "D i a d e m" -> "Diadem", "R y a n" -> "Ryan"
_SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z]\s){2,}[A-Za-z]\b")

def _collapse_spelled_words(s: str, protected: set | None = None) -> str:
    if not s:
        return s
    def repl(m):
        token = m.group(0)
        joined = token.replace(" ", "")
        if protected and (token in protected or joined in protected):
            return token
        return joined
    return _SPACED_LETTERS.sub(repl, s)

def _normalize_spacing_punct(s: str) -> str:
    # remove spaces before punctuation, ensure one space after sentence enders, collapse doubles
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([.!?])([^\s])", r"\1 \2", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def _sentence_case(s: str) -> str:
    # capitalize first alphabetic character in a sentence
    for i, ch in enumerate(s.lstrip()):
        if ch.isalpha():
            lead = s[:len(s) - len(s.lstrip())]
            return lead + s.lstrip()[:i] + ch.upper() + s.lstrip()[i+1:]
    return s

def _split_sentences(s: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", s)

def clean_transcript(
    text: str,
    collapse_spelled: bool = True,
    enforce_sentence_case: bool = True,
    normalize_punct: bool = True,
    glossary: list[str] | None = None,      # e.g. ["Diadem", "Vurndharth", "Saphryn"]
    protected_spelled: list[str] | None = None,  # spelled forms you DON'T want joined
    smart_quotes: bool = False
) -> str:
    """
    Light, fast post-process for ASR output. No network calls.
    """
    if not text:
        return text

    out = fix_text(text)  # unicode / weird quotes, if ftfy available

    if collapse_spelled:
        out = _collapse_spelled_words(out, set(protected_spelled or []))

    if normalize_punct:
        out = _normalize_spacing_punct(out)

    if enforce_sentence_case:
        sents = [_sentence_case(s) for s in _split_sentences(out)]
        out = " ".join(sents)

    if glossary:
        # Canonicalize key terms’ casing (case-insensitive replace)
        for term in glossary:
            out = re.sub(rf"\b{re.escape(term)}\b", term, out, flags=re.IGNORECASE)

    if smart_quotes and _sp:
        out = _sp.smartypants(out)

    return out
