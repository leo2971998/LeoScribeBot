#!/usr/bin/env python3
"""
Test script to demonstrate the hybrid correction system
"""

import asyncio
import time
from text_corrector import correct_transcript, benchmark_correction

async def test_problem_examples():
    """Test the specific problematic examples from the issue"""
    
    print("🧪 Testing Hybrid Text Correction System")
    print("=" * 60)
    
    # Examples from the problem statement
    problem_examples = [
        "The Beholder is the Beholder is a dangerous monster would I stocks",
        "Search biting are prime minister Astarion of investigator on Roshar",
        "the be holder is a dangerous monster with eye stalks",
        "eldrich blast is a powerful cantrip in dungeons and dragons",
        "search binding is a prime manifestation of investiture on rochelle"
    ]
    
    print("Testing SPACY method (fast corrections):")
    print("-" * 40)
    
    for text in problem_examples:
        start_time = time.time()
        result = await correct_transcript(text, method="SPACY")
        elapsed_ms = (time.time() - start_time) * 1000
        
        print(f"Input:  '{text}'")
        print(f"Output: '{result}' ({elapsed_ms:.1f}ms)")
        print()
    
    print("\nTesting HYBRID method (spaCy + LLM fallback):")
    print("-" * 40)
    print("Note: LLM requests will fail if Ollama is not running, but spaCy will still work")
    print()
    
    for text in problem_examples:
        start_time = time.time()
        result = await correct_transcript(text, method="HYBRID")
        elapsed_ms = (time.time() - start_time) * 1000
        
        print(f"Input:  '{text}'")
        print(f"Output: '{result}' ({elapsed_ms:.1f}ms)")
        print()

async def main():
    await test_problem_examples()
    
    print("\n📊 Performance Benchmark:")
    print("=" * 60)
    
    # Run benchmarks for both methods
    for method in ["SPACY", "HYBRID"]:
        print(f"\n{method} Method Results:")
        try:
            results = await benchmark_correction(method=method)
            for key, value in results.items():
                print(f"  {key}: {value}")
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())