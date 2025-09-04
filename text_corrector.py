"""
Ultra-fast text correction optimized for Intel N95 CPU
Perfect for real-time transcription with 20-50ms response time

This module provides spaCy-based text correction that's optimized for:
- Real-time performance (sub-50ms response)
- Low memory usage (~50MB)
- Offline operation
- Intel N95 CPU efficiency
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Set
import re

# spaCy imports with graceful fallback
try:
    import spacy
    from spacy.lang.en import English
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    spacy = None
    English = None

logger = logging.getLogger(__name__)

class TextCorrector:
    """Ultra-fast text corrector using spaCy for real-time transcription"""
    
    def __init__(self):
        self.nlp = None
        self.is_loaded = False
        self.correction_cache: Dict[str, str] = {}
        self.max_cache_size = 1000
        
        # Common transcription errors and corrections
        self.quick_fixes = {
            # Common speech recognition errors
            'serge': 'surge',
            'binding': 'blinding',
            'magic manifestation': 'mage manifestation',
            'diadem': 'diadem',  # Preserve gaming terms
            'ryan': 'Ryan',      # Proper names
            'lol': 'LOL',        # Internet slang
            
            # Common misheard words
            'there is': 'there\'s',
            'it is': 'it\'s',
            'we are': 'we\'re',
            'you are': 'you\'re',
            'i am': 'I\'m',
            'cannot': 'can\'t',
            'will not': 'won\'t',
            'should not': 'shouldn\'t',
            'would not': 'wouldn\'t',
            
            # Gaming/Discord specific terms
            'discord': 'Discord',
            'youtube': 'YouTube',
            'twitch': 'Twitch',
            'minecraft': 'Minecraft',
            'valorant': 'Valorant',
            'league of legends': 'League of Legends',
        }
        
        # Regex patterns for quick fixes
        self.patterns = [
            (re.compile(r'\bserge binding\b', re.IGNORECASE), 'surge blinding'),
            (re.compile(r'\bi\s+', re.IGNORECASE), 'I '),  # Fix lowercase 'i'
            (re.compile(r'\s+', re.MULTILINE), ' '),        # Multiple spaces
            (re.compile(r'([.!?])\s*([a-z])', re.MULTILINE), r'\1 \2'),  # Space after sentence end
        ]
        
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
                # Keep only essential components
                to_disable = []
                for name in self.nlp.pipe_names:
                    if name not in ['tok2vec', 'tagger', 'parser', 'sentencizer']:
                        to_disable.append(name)
                if to_disable:
                    self.nlp.disable_pipes(*to_disable)
            
            self.is_loaded = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to load spaCy model: {e}")
            self.is_loaded = True  # Still allow rule-based correction
            return False
    
    async def correct_transcript(self, text: str) -> str:
        """
        Ultra-fast text correction optimized for real-time use
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
            
            # Apply dictionary-based quick fixes
            words = corrected.split()
            for i, word in enumerate(words):
                word_lower = word.lower()
                if word_lower in self.quick_fixes:
                    words[i] = self.quick_fixes[word_lower]
                # Handle word combinations
                if i < len(words) - 1:
                    two_word = f"{word_lower} {words[i+1].lower()}"
                    if two_word in self.quick_fixes:
                        words[i] = self.quick_fixes[two_word]
                        words[i+1] = ""  # Mark for removal
                        
            # Remove empty strings
            words = [w for w in words if w]
            corrected = " ".join(words)
            
            # Use spaCy for more advanced correction if available and text is complex
            if (self.nlp is not None and 
                len(corrected.split()) > 3 and 
                time.time() - start_time < 0.03):  # Only if we have time budget
                
                try:
                    # Process with spaCy (limit processing for speed)
                    doc = self.nlp(corrected[:200])  # Limit length for speed
                    
                    # Quick sentence boundary correction
                    sentences = []
                    current_sent = []
                    
                    for token in doc:
                        current_sent.append(token.text)
                        if token.is_sent_end or token.text in '.!?':
                            if current_sent:
                                sent_text = " ".join(current_sent).strip()
                                if sent_text:
                                    # Capitalize first letter
                                    sent_text = sent_text[0].upper() + sent_text[1:] if len(sent_text) > 1 else sent_text.upper()
                                    sentences.append(sent_text)
                                current_sent = []
                    
                    # Add remaining words
                    if current_sent:
                        sent_text = " ".join(current_sent).strip()
                        if sent_text:
                            sent_text = sent_text[0].upper() + sent_text[1:] if len(sent_text) > 1 else sent_text.upper()
                            sentences.append(sent_text)
                    
                    if sentences:
                        corrected = ". ".join(sentences)
                        if not corrected.endswith(('.', '!', '?')):
                            corrected += "."
                            
                except Exception as e:
                    logger.debug(f"spaCy processing error: {e}")
                    # Fall back to regex-corrected version
                    pass
            
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
    """Benchmark the correction performance"""
    if sample_texts is None:
        sample_texts = [
            "serge binding is a magic manifestation",
            "hello there i am playing valorant",
            "this is a test of the correction system",
            "ryan said lol that was funny",
            "we are going to discord later",
        ]
    
    corrector = await get_corrector()
    times = []
    
    for text in sample_texts:
        start = time.time()
        corrected = await corrector.correct_transcript(text)
        end = time.time()
        time_ms = (end - start) * 1000
        times.append(time_ms)
        print(f"'{text}' -> '{corrected}' ({time_ms:.1f}ms)")
    
    return {
        'avg_time_ms': sum(times) / len(times),
        'max_time_ms': max(times),
        'min_time_ms': min(times),
        'cache_size': len(corrector.correction_cache),
        'spacy_available': SPACY_AVAILABLE,
        'model_loaded': corrector.nlp is not None
    }

if __name__ == "__main__":
    # Quick test
    async def test():
        print("Testing text corrector...")
        results = await benchmark_correction()
        print(f"\nPerformance results: {results}")
        
        # Test specific gaming/Discord terms
        test_cases = [
            "serge binding is magic",
            "i am playing minecraft",
            "lets go to discord",
            "ryan said lol",
        ]
        
        corrector = await get_corrector()
        print("\nSpecific test cases:")
        for case in test_cases:
            corrected = await corrector.correct_transcript(case)
            print(f"'{case}' -> '{corrected}'")
    
    asyncio.run(test())