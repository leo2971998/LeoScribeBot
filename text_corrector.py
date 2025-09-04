"""
Three-layer text correction system optimized for Intel N95 CPU
Perfect for real-time transcription with fantasy gaming terms

This module provides a sophisticated three-layer correction system:
- Layer 1: Basic transcription (handled by bot.py)
- Layer 2: Phrase-based corrections using spaCy Matcher
- Layer 3: Fuzzy string matching for individual words using thefuzz

Optimized for:
- Real-time performance (sub-50ms response)
- Low memory usage (~50MB)
- Offline operation
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

logger = logging.getLogger(__name__)

class TextCorrector:
    """Three-layer text corrector optimized for fantasy gaming terms"""
    
    def __init__(self, corrections_file="corrections.txt"):
        self.nlp = None
        self.matcher = None
        self.is_loaded = False
        self.correction_cache: Dict[str, str] = {}
        self.max_cache_size = 1000
        
        # Storage for corrections
        self.phrase_corrections: Dict[str, str] = {}
        self.word_corrections: Dict[str, str] = {}
        
        # Load corrections from file
        self.corrections_file = corrections_file
        self._load_corrections()
        
        # Quick regex patterns for common fixes
        self.patterns = [
            (re.compile(r'\bi\s+', re.IGNORECASE), 'I '),  # Fix lowercase 'i'
            (re.compile(r'\s+', re.MULTILINE), ' '),        # Multiple spaces
            (re.compile(r'([.!?])\s*([a-z])', re.MULTILINE), r'\1 \2'),  # Space after sentence end
        ]
    
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
                        
                        # If the incorrect term has spaces, it's a phrase
                        if ' ' in incorrect:
                            self.phrase_corrections[incorrect] = correct
                        else:
                            self.word_corrections[incorrect] = correct
        except FileNotFoundError:
            logger.warning(f"Warning: {self.corrections_file} not found. Creating empty correction lists.")
        
        logger.info(f"Loaded {len(self.phrase_corrections)} phrase corrections and {len(self.word_corrections)} word corrections")
    
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

    def correct_words(self, text: str, similarity_threshold: int = 80) -> str:
        """Layer 3: Correct individual words using fuzzy matching."""
        if not THEFUZZ_AVAILABLE:
            # Fallback to exact matching for word corrections
            words = text.split()
            corrected_words = []
            
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
                else:
                    corrected_words.append(word)
            
            return ' '.join(corrected_words)
        
        try:
            words = text.split()
            corrected_words = []
            
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
            
            return ' '.join(corrected_words)
        except Exception as e:
            logger.debug(f"Word correction error: {e}")
            return text

    async def correct_transcript(self, text: str) -> str:
        """
        Three-layer text correction optimized for real-time use
        Layer 1: Basic transcription (handled by bot.py)
        Layer 2: Phrase-based corrections using spaCy Matcher
        Layer 3: Fuzzy string matching for individual words
        Target: 20-50ms response time
        """
        if not text or not text.strip():
            return text
            
        start_time = time.time()
        
        # Check cache first (fastest path)
        text_key = text.lower().strip()
        if text_key in self.correction_cache:
            return self.correction_cache[text_key]
        
        try:
            # Start with the input text
            corrected = text.strip()
            
            # Apply quick regex fixes first (very fast)
            for pattern, replacement in self.patterns:
                corrected = pattern.sub(replacement, corrected)
            
            # Layer 2: Phrase corrections (fast pattern matching)
            corrected = self.correct_phrases(corrected)
            
            # Layer 3: Word corrections (fuzzy matching)
            corrected = self.correct_words(corrected)
            
            # Final cleanup
            corrected = re.sub(r'\s+', ' ', corrected).strip()
            
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
            if processing_time > 50:
                logger.debug(f"Text correction took {processing_time:.1f}ms (target: <50ms)")
            
            return corrected
            
        except Exception as e:
            logger.error(f"Text correction error: {e}")
            return text  # Return original on error


# Global instance for efficient reuse
_corrector_instance: Optional[TextCorrector] = None

async def get_corrector() -> TextCorrector:
    """Get the global text corrector instance"""
    global _corrector_instance
    if _corrector_instance is None:
        _corrector_instance = TextCorrector()
        await _corrector_instance.load_model()
    return _corrector_instance

async def correct_transcript(text: str) -> str:
    """
    Convenience function for quick text correction
    Optimized for real-time use with Intel N95 CPU
    """
    corrector = await get_corrector()
    return await corrector.correct_transcript(text)

# Performance test function
async def benchmark_correction(sample_texts: list = None) -> dict:
    """Benchmark the three-layer correction performance"""
    if sample_texts is None:
        sample_texts = [
            "search binding is a prime manifestation of investiture on rochelle",
            "search binders can manipulate 10 fundamental forces locally known as surges",
            "eldrich blast is a powerful cantrip in dungeons and dragons",
            "the be holder is a dangerous monster with eye stalks",
            "Ryan said he likes playing valorant with his discord friends",
        ]
    
    corrector = await get_corrector()
    times = []
    
    print("Testing three-layer correction system:")
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
        'avg_time_ms': sum(times) / len(times),
        'max_time_ms': max(times),
        'min_time_ms': min(times),
        'cache_size': len(corrector.correction_cache),
        'spacy_available': SPACY_AVAILABLE,
        'thefuzz_available': THEFUZZ_AVAILABLE,
        'model_loaded': corrector.nlp is not None,
        'phrase_corrections': len(corrector.phrase_corrections),
        'word_corrections': len(corrector.word_corrections)
    }

if __name__ == "__main__":
    # Quick test of the three-layer system
    async def test():
        print("Testing three-layer text correction system...")
        print(f"spaCy available: {SPACY_AVAILABLE}")
        print(f"thefuzz available: {THEFUZZ_AVAILABLE}")
        
        results = await benchmark_correction()
        print("\nPerformance results:")
        for key, value in results.items():
            print(f"  {key}: {value}")
        
        # Test the specific example from the problem statement
        print("\nTesting problem statement example:")
        print("=" * 60)
        
        problem_text = "Search binding is a prime manifestation of investiture on Roche search binders can manipulate 10 fundamental forces locally known as surges by infusing objects or beings with Stormlight or some other kind of investiture"
        
        corrector = await get_corrector()
        corrected = await corrector.correct_transcript(problem_text)
        
        print(f"Original: {problem_text}")
        print()
        print(f"Corrected: {corrected}")
    
    asyncio.run(test())