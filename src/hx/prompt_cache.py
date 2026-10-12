"""Prompt-cache accounting: keep invariants cached, restructure when cold.

The provider cache rewards a stable prefix (system prompt, persona,
learnings, tool schemas) and expires on a TTL — 5 minutes by default, one
hour when the harness configures ``cache_ttl: "1h"``. While the cache is
warm, changing the prefix pays a full rewrite, so context changes should be
append-only deltas. Once it is cold, restructuring costs nothing extra:
that is the moment to compress, reorder, or rebuild the context. This
module makes that limit explicit so seams and companion passes can choose.
"""
from __future__ import annotations

import json
import re

from .errors import ValidationError

DEFAULT_TTL_S = 300
MIN_CACHEABLE_TOKENS = 1024  # provider minimum prefix for a cache breakpoint


def ttl_seconds(value) -> int:
    """Parse a harness ``cache_ttl`` ("1h", "30m", "300", seconds) to seconds."""
    if value is None:
        return DEFAULT_TTL_S
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            raise ValidationError("cache_ttl must be positive")
        return int(value)
    match = re.fullmatch(r"\s*(\d+)\s*([smh])?\s*", str(value))
    if not match:
        raise ValidationError(f"unparseable cache_ttl: {value!r}")
    amount = int(match.group(1))
    return amount * {"s": 1, "m": 60, "h": 3600}[match.group(2) or "s"]


def assess(*, prefix_bytes: int, age_s: float, ttl="5m") -> dict:
    """Cache state for an invariant prefix last written ``age_s`` ago.

    Token accounting follows hx convention: one UTF-8 byte is charged as
    one token until an adapter tokenizer is supplied (conservative).
    """
    if prefix_bytes < 0 or age_s < 0:
        raise ValidationError("prefix_bytes and age_s must be nonnegative")
    ttl_s = ttl_seconds(ttl)
    prefix_tokens = prefix_bytes
    cacheable = prefix_tokens >= MIN_CACHEABLE_TOKENS
    if not cacheable:
        state = "cold"
    elif age_s < ttl_s * 0.8:
        state = "warm"
    elif age_s < ttl_s:
        state = "expiring"
    else:
        state = "cold"
    recommendation = {"warm": "keep-prefix", "expiring": "defer-to-expiry",
                      "cold": "restructure-free"}[state]
    return {"state": state, "cacheable": cacheable,
            "prefix_tokens": prefix_tokens, "ttl_s": ttl_s,
            "age_s": age_s, "recommendation": recommendation,
            "note": "Warm: append deltas only, prefix edits pay a rewrite. "
                    "Cold: restructure freely, the rewrite is already owed."}


def invariant_budget(*, window: int, threshold: int) -> dict:
    """How much invariant prefix a model can carry under its seam threshold."""
    if window <= 0 or threshold <= 0 or threshold >= window:
        raise ValidationError("invariant budget needs 0 < threshold < window")
    return {"invariant_ceiling_tokens": threshold,
            "working_room_tokens": window - threshold,
            "note": "Invariants (persona, learnings, policy, tool schemas) must fit "
                    "under the seam threshold; the rest of the window is working room "
                    "for the current task, which is restructured at cold seams."}


def main(argv, root, *, env=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="hx cache")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("assess")
    p.add_argument("--prefix-bytes", type=int, required=True)
    p.add_argument("--age-s", type=float, required=True)
    p.add_argument("--ttl", default="5m")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p = sub.add_parser("budget")
    p.add_argument("--window", type=int, required=True)
    p.add_argument("--threshold", type=int, required=True)
    p.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "assess":
        result = assess(prefix_bytes=args.prefix_bytes, age_s=args.age_s, ttl=args.ttl)
    else:
        result = invariant_budget(window=args.window, threshold=args.threshold)
    print(json.dumps(result, indent=2))
    return 0
