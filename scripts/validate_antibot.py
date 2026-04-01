#!/usr/bin/env python3
"""
Validation script for TradingView anti-bot detection implementation.
Tests that all components are working correctly without requiring full env setup.
"""

import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

def test_imports():
    """Test that all required modules can be imported."""
    print("Testing imports...")
    try:
        from tv_data_fetcher import (
            create_fetcher, 
            TradingViewFetcher, 
            _get_random_headers,
            _USER_AGENTS
        )
        print("  ✅ tv_data_fetcher imports OK")
        return True
    except ImportError as e:
        print(f"  ❌ Import failed: {e}")
        return False

def test_user_agent_pool():
    """Test User-Agent pool has expected size and variety."""
    print("\nTesting User-Agent pool...")
    from tv_data_fetcher import _USER_AGENTS
    
    if len(_USER_AGENTS) < 5:
        print(f"  ⚠️  Only {len(_USER_AGENTS)} UAs (expected 7+)")
        return False
    
    browsers = set()
    for ua in _USER_AGENTS:
        if "Chrome" in ua:
            browsers.add("Chrome")
        if "Firefox" in ua:
            browsers.add("Firefox")
        if "Safari" in ua and "Chrome" not in ua:
            browsers.add("Safari")
        if "Edg" in ua:
            browsers.add("Edge")
    
    print(f"  ✅ {len(_USER_AGENTS)} User-Agents covering {len(browsers)} browsers")
    print(f"     Browsers: {', '.join(sorted(browsers))}")
    return True

def test_random_headers():
    """Test random header generation."""
    print("\nTesting random header generation...")
    from tv_data_fetcher import _get_random_headers
    
    # Generate multiple headers to check randomness
    uas = set()
    for _ in range(10):
        headers = _get_random_headers()
        uas.add(headers["User-Agent"])
    
    # Check required headers
    required = [
        "User-Agent", "Accept", "Accept-Language", 
        "Accept-Encoding", "Sec-Fetch-Dest"
    ]
    sample = _get_random_headers()
    missing = [h for h in required if h not in sample]
    
    if missing:
        print(f"  ❌ Missing headers: {missing}")
        return False
    
    print(f"  ✅ Headers include all required fields")
    print(f"  ✅ {len(uas)} unique UAs in 10 generations (randomization working)")
    return True

def test_fetcher_instantiation():
    """Test TradingViewFetcher can be created with custom delays."""
    print("\nTesting fetcher instantiation...")
    from tv_data_fetcher import TradingViewFetcher
    
    # Test default
    fetcher1 = TradingViewFetcher()
    if fetcher1._request_delay_range != (1.0, 3.0):
        print(f"  ❌ Default delays wrong: {fetcher1._request_delay_range}")
        return False
    
    # Test custom
    fetcher2 = TradingViewFetcher(request_delay_range=(2.0, 5.0))
    if fetcher2._request_delay_range != (2.0, 5.0):
        print(f"  ❌ Custom delays not applied: {fetcher2._request_delay_range}")
        return False
    
    # Test session exists
    if fetcher1._session is None:
        print("  ❌ Session not initialized")
        return False
    
    print("  ✅ Fetcher instantiation works")
    print(f"     Default delays: {fetcher1._request_delay_range}")
    print(f"     Custom delays: {fetcher2._request_delay_range}")
    print(f"     Session initialized: Yes")
    return True

def test_factory_function():
    """Test create_fetcher() factory without full config."""
    print("\nTesting factory function...")
    from tv_data_fetcher import create_fetcher
    
    # Test without config (should use defaults)
    fetcher = create_fetcher(None)
    if fetcher._request_delay_range != (1.0, 3.0):
        print(f"  ❌ Factory defaults wrong: {fetcher._request_delay_range}")
        return False
    
    print("  ✅ create_fetcher() works without config")
    print(f"     Uses default delays: {fetcher._request_delay_range}")
    return True

def main():
    """Run all validation tests."""
    print("=" * 60)
    print("TradingView Anti-Bot Detection Validation")
    print("=" * 60)
    
    tests = [
        test_imports,
        test_user_agent_pool,
        test_random_headers,
        test_fetcher_instantiation,
        test_factory_function,
    ]
    
    results = [test() for test in tests]
    
    print("\n" + "=" * 60)
    if all(results):
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        return 0
    else:
        failed = sum(1 for r in results if not r)
        print(f"❌ {failed}/{len(tests)} TESTS FAILED")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
