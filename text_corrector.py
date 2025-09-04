"""
Hybrid text correction system optimized for Intel N95 CPU
Perfect for real-time transcription with fantasy gaming terms

This module provides a sophisticated multi-layer correction system:
- Layer 1: Basic transcription (handled by bot.py)
- Layer 2: Phrase-based corrections using spaCy Matcher
- Layer 3: Fuzzy string matching for individual words using thefuzz
- Layer 4: LLM fallback using Phi-3 for heavily garbled text

Correction Methods:
- SPACY: Fast spaCy + fuzzy matching (sub-50ms)
- LLM: High-quality Phi-3 corrections (1-3s)
- HYBRID: Smart combination - spaCy first, LLM fallback if low confidence

Optimized for:
- Real-time performance (sub-50ms for most cases)
- Low memory usage (~50MB + 4-5GB for Phi-3)
- Offline operation (both spaCy and local Ollama)
- Intel N95 CPU efficiency
- Fantasy gaming terms (Stormlight Archive, D&D)
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Set, List, Tuple
import re
from pathlib import Path

# spaCy imports with graceful fallback
try:
    import spacy
    from spacy.matcher import Matcher
    from spacy.lang.en import English
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    spacy = None
    Matcher = None
    English = None

# thefuzz imports with graceful fallback
try:
    from thefuzz import fuzz
    THEFUZZ_AVAILABLE = True
except ImportError:
    THEFUZZ_AVAILABLE = False
    fuzz = None

# httpx for LLM communication
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None

logger = logging.getLogger(__name__)

# Configuration constants
CORRECTION_METHOD = "HYBRID"  # Options: SPACY, LLM, HYBRID
CONFIDENCE_THRESHOLD = 70    # Below this %, use LLM in HYBRID mode
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
LLM_MODEL = "phi3"
LLM_TIMEOUT = 10.0  # seconds

class TextCorrector:
    """Multi-layer text corrector with LLM fallback for fantasy gaming terms"""
    
    def __init__(self, corrections_file="corrections.txt", method=CORRECTION_METHOD):
        self.nlp = None
        self.matcher = None
        self.is_loaded = False
        self.correction_cache: Dict[str, str] = {}
        self.max_cache_size = 1000
        self.method = method
        
        # Storage for corrections
        self.phrase_corrections: Dict[str, str] = {}
        self.word_corrections: Dict[str, str] = {}
        self.all_correct_terms: Set[str] = set()  # For LLM context
        
        # Load corrections from file
        self.corrections_file = corrections_file
        self._load_corrections()
        
        # Quick regex patterns for common fixes
        self.patterns = [
            (re.compile(r'\bi\s+', re.IGNORECASE), 'I '),  # Fix lowercase 'i'
            (re.compile(r'\s+', re.MULTILINE), ' '),        # Multiple spaces
            (re.compile(r'([.!?])\s*([a-z])', re.MULTILINE), r'\1 \2'),  # Space after sentence end
        ]
        
        # HTTP client for LLM requests
        self.http_client = None
        if HTTPX_AVAILABLE and method in ["LLM", "HYBRID"]:
            self.http_client = httpx.AsyncClient(timeout=LLM_TIMEOUT)
    
    def _load_corrections(self):
        """Load corrections from the file, separating phrases from single words."""
        corrections_path = Path(__file__).parent / self.corrections_file
        
        try:
            with open(corrections_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if line.startswith('---') or not line or line.startswith('#'):
                        continue
                    
                    if ' > ' in line:
                        incorrect, correct = line.split(' > ', 1)
                        incorrect = incorrect.strip().lower()
                        correct = correct.strip()
                        
                        # Store the correct term for LLM context
                        self.all_correct_terms.add(correct)
                        
                        # If the incorrect term has spaces, it's a phrase
                        if ' ' in incorrect:
                            self.phrase_corrections[incorrect] = correct
                        else:
                            self.word_corrections[incorrect] = correct
        except FileNotFoundError:
            logger.warning(f"Warning: {self.corrections_file} not found. Creating empty correction lists.")
        
        logger.info(f"Loaded {len(self.phrase_corrections)} phrase corrections and {len(self.word_corrections)} word corrections")
        logger.info(f"Compiled {len(self.all_correct_terms)} terms for LLM context")
    
    def _setup_phrase_patterns(self):
        """Set up spaCy patterns for phrase matching."""
        if not SPACY_AVAILABLE or self.nlp is None:
            return
            
        self.matcher = Matcher(self.nlp.vocab)
        
        for incorrect_phrase in self.phrase_corrections:
            # Create a pattern for each phrase
            pattern = []
            words = incorrect_phrase.split()
            for word in words:
                pattern.append({"LOWER": word})
            
            # Add pattern to matcher with phrase as label
            pattern_id = f"PHRASE_{len(self.matcher)}"
            self.matcher.add(pattern_id, [pattern])
        
        logger.info(f"Set up {len(self.phrase_corrections)} spaCy phrase patterns")
        
    async def load_model(self) -> bool:
        """Load spaCy model asynchronously for minimal startup impact"""
        if not SPACY_AVAILABLE:
            logger.warning("spaCy not available, falling back to rule-based correction only")
            self.is_loaded = True  # Still allow rule-based correction
            return True
            
        try:
            # Load the lightweight model in a thread to avoid blocking
            start_time = time.time()
            
            def _load_spacy():
                try:
                    # Use the small model for speed
                    return spacy.load("en_core_web_sm")
                except OSError:
                    logger.warning("en_core_web_sm not found, creating blank model")
                    # Fallback to blank English model
                    nlp = English()
                    # Add minimal pipeline components for efficiency
                    nlp.add_pipe('sentencizer')
                    return nlp
            
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            self.nlp = await loop.run_in_executor(None, _load_spacy)
            
            load_time = time.time() - start_time
            logger.info(f"spaCy model loaded in {load_time:.2f}s")
            
            # Disable unnecessary pipeline components for speed
            if hasattr(self.nlp, 'disable_pipes'):
                # Keep only essential components for phrase matching
                to_disable = []
                for name in self.nlp.pipe_names:
                    if name not in ['tok2vec', 'tagger', 'parser', 'sentencizer']:
                        to_disable.append(name)
                if to_disable:
                    self.nlp.disable_pipes(*to_disable)
            
            # Set up phrase patterns for Layer 2 corrections
            self._setup_phrase_patterns()
            
            self.is_loaded = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to load spaCy model: {e}")
            self.is_loaded = True  # Still allow rule-based correction
            return False
    
    def correct_phrases(self, text: str) -> str:
        """Layer 2: Correct multi-word phrases using spaCy matcher."""
        if not SPACY_AVAILABLE or self.nlp is None or self.matcher is None:
            return text
            
        try:
            doc = self.nlp(text)
            matches = self.matcher(doc)
            
            # Sort matches by start position (reverse order to maintain indices)
            matches = sorted(matches, key=lambda x: x[1], reverse=True)
            
            corrected_text = text
            for match_id, start, end in matches:
                matched_span = doc[start:end]
                matched_text = matched_span.text.lower()
                
                # Find the correction for this phrase
                for incorrect_phrase, correct_phrase in self.phrase_corrections.items():
                    if matched_text == incorrect_phrase:
                        # Replace in the original text (maintaining case context)
                        start_char = matched_span.start_char
                        end_char = matched_span.end_char
                        corrected_text = (corrected_text[:start_char] + 
                                        correct_phrase + 
                                        corrected_text[end_char:])
                        break
            
            return corrected_text
        except Exception as e:
            logger.debug(f"Phrase correction error: {e}")
            return text

    def correct_words(self, text: str, similarity_threshold: int = 80) -> Tuple[str, int]:
        """Layer 3: Correct individual words using fuzzy matching. Returns (corrected_text, confidence_score)."""
        if not THEFUZZ_AVAILABLE:
            # Fallback to exact matching for word corrections
            words = text.split()
            corrected_words = []
            corrections_made = 0
            
            for word in words:
                # Clean the word (remove punctuation for matching)
                clean_word = re.sub(r'[^\w]', '', word.lower())
                
                if clean_word in self.word_corrections:
                    corrected = self.word_corrections[clean_word]
                    # Preserve original punctuation and capitalization context
                    if word[0].isupper():
                        corrected = corrected.capitalize()
                    # Add back any punctuation
                    punctuation = re.findall(r'[^\w]', word)
                    if punctuation:
                        corrected += ''.join(punctuation)
                    corrected_words.append(corrected)
                    corrections_made += 1
                else:
                    corrected_words.append(word)
            
            # Calculate confidence: fewer corrections relative to total words = higher confidence
            confidence = max(0, 100 - (corrections_made * 20))  # Lose 20 points per correction
            return ' '.join(corrected_words), confidence
        
        try:
            words = text.split()
            corrected_words = []
            corrections_made = 0
            low_confidence_corrections = 0
            
            for word in words:
                # Clean the word (remove punctuation for matching)
                clean_word = re.sub(r'[^\w]', '', word.lower())
                best_match = None
                best_score = 0
                
                # Check against known word corrections using fuzzy matching
                for incorrect_word, correct_word in self.word_corrections.items():
                    score = fuzz.ratio(clean_word, incorrect_word)
                    if score > best_score and score >= similarity_threshold:
                        best_score = score
                        best_match = correct_word
                
                if best_match:
                    # Track correction quality
                    corrections_made += 1
                    if best_score < 95:  # Low confidence fuzzy match
                        low_confidence_corrections += 1
                        
                    # Preserve original punctuation and capitalization context
                    if word[0].isupper():
                        best_match = best_match.capitalize()
                    # Add back any punctuation
                    punctuation = re.findall(r'[^\w]', word)
                    if punctuation:
                        best_match += ''.join(punctuation)
                    corrected_words.append(best_match)
                else:
                    corrected_words.append(word)
            
            # Calculate confidence based on correction quality
            total_words = len(words)
            correction_rate = corrections_made / total_words if total_words > 0 else 0
            low_confidence_rate = low_confidence_corrections / total_words if total_words > 0 else 0
            
            # High correction rate or many low-confidence corrections = low overall confidence
            confidence = max(0, 100 - (correction_rate * 60) - (low_confidence_rate * 40))
            
            return ' '.join(corrected_words), int(confidence)
        except Exception as e:
            logger.debug(f"Word correction error: {e}")
            return text, 50  # Medium confidence on error

    async def correct_with_llm(self, text: str) -> str:
        """Layer 4: Use Phi-3 LLM for intelligent text correction."""
        if not HTTPX_AVAILABLE or not self.http_client:
            logger.warning("LLM correction requested but httpx not available")
            return text
            
        try:
            # Build lexicon from corrections.txt
            lexicon_terms = sorted(list(self.all_correct_terms))
            lexicon_str = ', '.join(lexicon_terms[:100])  # Limit to first 100 terms to avoid token limits
            
            # Create the prompt
            prompt = f"""You are an expert editor and transcriber for high-fantasy media, specializing in Dungeons & Dragons and the Cosmere. Your task is to correct a garbled sentence from a voice-to-text system and make it grammatically correct and coherent.

Use the provided lexicon of known fantasy terms to help you. The output should be ONLY the corrected sentence, without any explanations.

Lexicon of Known Terms:
{lexicon_str}

Garbled Text: "{text}"
Corrected Text:"""

            # Prepare the request to Ollama
            request_data = {
                "model": LLM_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # Low temperature for consistent corrections
                    "top_p": 0.9,
                    "stop": ["\n", "Garbled Text:", "Corrected Text:"],
                }
            }
            
            # Make the request
            response = await self.http_client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json=request_data
            )
            
            if response.status_code == 200:
                result = response.json()
                corrected_text = result.get("response", "").strip()
                
                # Clean up the response (remove any leftover prompt artifacts)
                corrected_text = re.sub(r'^Corrected Text:\s*', '', corrected_text)
                corrected_text = re.sub(r'^"(.*)"$', r'\1', corrected_text)  # Remove quotes
                corrected_text = corrected_text.strip()
                
                if corrected_text and corrected_text != text:
                    logger.debug(f"LLM correction: '{text}' -> '{corrected_text}'")
                    return corrected_text
                else:
                    return text
            else:
                logger.error(f"LLM request failed: {response.status_code}")
                return text
                
        except Exception as e:
            logger.error(f"LLM correction error: {e}")
            return text

    async def correct_transcript(self, text: str) -> str:
        """
        Multi-layer text correction with configurable method
        - SPACY: Fast spaCy + fuzzy matching (20-50ms)
        - LLM: High-quality Phi-3 corrections (1-3s)
        - HYBRID: Smart combination - spaCy first, LLM fallback if low confidence
        """
        if not text or not text.strip():
            return text
            
        start_time = time.time()
        
        # Check cache first (fastest path)
        text_key = text.lower().strip()
        if text_key in self.correction_cache:
            return self.correction_cache[text_key]
        
        try:
            # Method selection
            if self.method == "LLM":
                # Pure LLM mode - skip spaCy, go straight to Phi-3
                corrected = await self.correct_with_llm(text)
                
            elif self.method == "SPACY":
                # Pure spaCy mode - original fast path
                corrected = await self._correct_with_spacy(text)
                
            elif self.method == "HYBRID":
                # Hybrid mode - spaCy first, then LLM if confidence is low
                corrected, confidence = await self._correct_with_spacy_confidence(text)
                
                if confidence < CONFIDENCE_THRESHOLD:
                    logger.debug(f"Low confidence ({confidence}%), using LLM fallback")
                    corrected = await self.correct_with_llm(corrected)
                else:
                    logger.debug(f"High confidence ({confidence}%), keeping spaCy result")
            else:
                logger.warning(f"Unknown correction method: {self.method}, falling back to SPACY")
                corrected = await self._correct_with_spacy(text)
            
            # Cache the result (with size limit)
            if len(self.correction_cache) < self.max_cache_size:
                self.correction_cache[text_key] = corrected
            elif len(self.correction_cache) >= self.max_cache_size:
                # Clear oldest entries (simple FIFO)
                keys_to_remove = list(self.correction_cache.keys())[:100]
                for key in keys_to_remove:
                    del self.correction_cache[key]
                self.correction_cache[text_key] = corrected
            
            processing_time = (time.time() - start_time) * 1000  # Convert to ms
            if processing_time > 100:  # Log if slower than expected
                logger.debug(f"Text correction took {processing_time:.1f}ms (method: {self.method})")
            
            return corrected
            
        except Exception as e:
            logger.error(f"Text correction error: {e}")
            return text  # Return original on error

    async def _correct_with_spacy(self, text: str) -> str:
        """Original spaCy-only correction path for compatibility."""
        # Start with the input text
        corrected = text.strip()
        
        # Apply quick regex fixes first (very fast)
        for pattern, replacement in self.patterns:
            corrected = pattern.sub(replacement, corrected)
        
        # Layer 2: Phrase corrections (fast pattern matching)
        corrected = self.correct_phrases(corrected)
        
        # Layer 3: Word corrections (fuzzy matching) - ignore confidence score
        corrected, _ = self.correct_words(corrected)
        
        # Final cleanup
        corrected = re.sub(r'\s+', ' ', corrected).strip()
        
        return corrected

    async def _correct_with_spacy_confidence(self, text: str) -> Tuple[str, int]:
        """spaCy correction with confidence scoring for hybrid mode."""
        # Start with the input text
        corrected = text.strip()
        original_length = len(corrected.split())
        
        # Apply quick regex fixes first (very fast)
        for pattern, replacement in self.patterns:
            corrected = pattern.sub(replacement, corrected)
        
        # Layer 2: Phrase corrections (fast pattern matching)
        phrase_corrected = self.correct_phrases(corrected)
        phrase_changes = 1 if phrase_corrected != corrected else 0
        corrected = phrase_corrected
        
        # Layer 3: Word corrections with confidence scoring
        corrected, word_confidence = self.correct_words(corrected)
        
        # Final cleanup
        corrected = re.sub(r'\s+', ' ', corrected).strip()
        
        # Calculate overall confidence
        # Factors: word-level confidence, phrase changes, overall coherence
        phrase_penalty = phrase_changes * 15  # Penalty for phrase corrections
        
        # Check for obviously garbled patterns
        garbled_patterns = [
            r'\b[a-z]\s+[a-z]\s+[a-z]\b',  # Single letters scattered
            r'\b\w{1,2}\b.*\b\w{1,2}\b.*\b\w{1,2}\b',  # Many tiny words
        ]
        
        garbled_penalty = 0
        for pattern in garbled_patterns:
            if re.search(pattern, corrected.lower()):
                garbled_penalty += 20
        
        # Final confidence calculation
        confidence = max(0, word_confidence - phrase_penalty - garbled_penalty)
        
        return corrected, confidence


# Global instance for efficient reuse
_corrector_instance: Optional[TextCorrector] = None

async def get_corrector(method: str = CORRECTION_METHOD) -> TextCorrector:
    """Get the global text corrector instance with specified method"""
    global _corrector_instance
    if _corrector_instance is None or _corrector_instance.method != method:
        _corrector_instance = TextCorrector(method=method)
        await _corrector_instance.load_model()
    return _corrector_instance

async def correct_transcript(text: str, method: str = CORRECTION_METHOD) -> str:
    """
    Convenience function for quick text correction
    Supports SPACY, LLM, and HYBRID modes
    """
    corrector = await get_corrector(method)
    return await corrector.correct_transcript(text)

# Performance test function
async def benchmark_correction(sample_texts: list = None, method: str = CORRECTION_METHOD) -> dict:
    """Benchmark the correction performance for specified method"""
    if sample_texts is None:
        sample_texts = [
            "search binding is a prime manifestation of investiture on rochelle",
            "search binders can manipulate 10 fundamental forces locally known as surges",
            "eldrich blast is a powerful cantrip in dungeons and dragons",
            "the be holder is a dangerous monster with eye stalks",
            "Search biting are prime minister Astarion of investigator on Roshar",
            "The Beholder is the Beholder is a dangerous monster would I stocks"
        ]
    
    corrector = await get_corrector(method)
    times = []
    
    print(f"Testing {method} correction system:")
    print("=" * 60)
    
    for text in sample_texts:
        start = time.time()
        corrected = await corrector.correct_transcript(text)
        end = time.time()
        time_ms = (end - start) * 1000
        times.append(time_ms)
        print(f"Input:  '{text}'")
        print(f"Output: '{corrected}' ({time_ms:.1f}ms)")
        print()
    
    return {
        'method': method,
        'avg_time_ms': sum(times) / len(times),
        'max_time_ms': max(times),
        'min_time_ms': min(times),
        'cache_size': len(corrector.correction_cache),
        'spacy_available': SPACY_AVAILABLE,
        'thefuzz_available': THEFUZZ_AVAILABLE,
        'httpx_available': HTTPX_AVAILABLE,
        'model_loaded': corrector.nlp is not None,
        'phrase_corrections': len(corrector.phrase_corrections),
        'word_corrections': len(corrector.word_corrections)
    }

if __name__ == "__main__":
    # Test all correction methods
    async def test():
        print("Testing hybrid text correction system...")
        print(f"spaCy available: {SPACY_AVAILABLE}")
        print(f"thefuzz available: {THEFUZZ_AVAILABLE}")
        print(f"httpx available: {HTTPX_AVAILABLE}")
        print()
        
        # Test problematic examples from the issue
        problem_examples = [
            "The Beholder is the Beholder is a dangerous monster would I stocks",
            "Search biting are prime minister Astarion of investigator on Roshar"
        ]
        
        print("Testing problem statement examples:")
        print("=" * 60)
        
        for method in ["SPACY", "HYBRID"]:  # Skip LLM-only for now
            print(f"\n{method} Method:")
            print("-" * 30)
            
            for text in problem_examples:
                try:
                    corrector = await get_corrector(method)
                    start = time.time()
                    corrected = await corrector.correct_transcript(text)
                    end = time.time()
                    time_ms = (end - start) * 1000
                    
                    print(f"Input:  '{text}'")
                    print(f"Output: '{corrected}' ({time_ms:.1f}ms)")
                    print()
                except Exception as e:
                    print(f"Error with {method}: {e}")
                    print()
        
        # Benchmark each method
        print("\nPerformance Benchmarks:")
        print("=" * 60)
        
        for method in ["SPACY", "HYBRID"]:
            try:
                results = await benchmark_correction(method=method)
                print(f"\n{method} Method Results:")
                for key, value in results.items():
                    print(f"  {key}: {value}")
            except Exception as e:
                print(f"Benchmark failed for {method}: {e}")
    
    asyncio.run(test())