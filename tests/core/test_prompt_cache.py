"""Prompt caching: know the limit, keep invariants cached, restructure when cold."""
from __future__ import annotations

from hx import prompt_cache


def test_ttl_parsing():
    assert prompt_cache.ttl_seconds("1h") == 3600
    assert prompt_cache.ttl_seconds("5m") == 300
    assert prompt_cache.ttl_seconds(None) == 300
    assert prompt_cache.ttl_seconds(600) == 600


def test_warm_cache_keeps_prefix():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=60, ttl="5m")
    assert result["state"] == "warm"
    assert result["recommendation"] == "keep-prefix"


def test_cold_cache_restructures_freely():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=4000, ttl="1h")
    assert result["state"] == "cold"
    assert result["recommendation"] == "restructure-free"


def test_tiny_prefix_has_no_cache_benefit():
    result = prompt_cache.assess(prefix_bytes=500, age_s=10, ttl="5m")
    assert result["cacheable"] is False
    assert result["recommendation"] == "restructure-free"


def test_expiring_cache_defers_small_changes():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=1700, ttl="30m")
    assert result["state"] == "expiring"
    assert result["recommendation"] == "defer-to-expiry"


def test_invariant_budget_leaves_working_room():
    budget = prompt_cache.invariant_budget(window=1_000_000, threshold=200_000)
    assert budget["invariant_ceiling_tokens"] <= 200_000
    assert budget["working_room_tokens"] >= 800_000
