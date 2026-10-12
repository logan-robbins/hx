"""Compaction reshaping: 5k seam threshold, three lanes stubbed off one interface.

The live version of this scenario builds a small app with frontend, API, and
backend lanes in parallel. It needs valid native credentials, so here the
contracts are proven with fixtures: the interface unit unblocks three lanes
in one wave, a 5k-token seam threshold is honoured by token controls, and a
cold-cache seam is the free moment to restructure context.
"""
from __future__ import annotations

import json
from pathlib import Path

from hx import planning, prompt_cache, token_controls
from .test_planning import plan, unit
from .test_appmap import mapped  # noqa: F401 — fixture chain
from .test_map_updates import active  # noqa: F401 — fixture


def test_interface_stub_unblocks_three_lanes(active):
    store, repo, *_ = active
    interface = unit(repo, "contract")
    lanes = [unit(repo, name, prerequisites=[
        {"task_id": "contract", "output_id": "proof", "version": 1}])
        for name in ("frontend", "api", "backend")]
    result = planning.validate(store, plan(active, [interface, *lanes]))
    assert result["waves"] == 2
    assert result["level_widths"] == [1, 3]
    assert result["initial_units"] == ["contract"]
    assert result["interface_warnings"] == []


def test_five_k_autocompact_threshold_is_honoured(tmp_path):
    root = tmp_path
    (root / "config" / "models.json").parent.mkdir(parents=True)
    (root / "config" / "models.json").write_text(json.dumps(
        {"test-model": {"window": 200000, "threshold": 5000}}))
    worker = root / "config" / "eng-001"
    worker.mkdir(parents=True)
    (worker / "harness.json").write_text(json.dumps(
        {"id": "eng-001", "pod": "engineers", "role": "backend-engineer",
         "model": "test-model", "effort": "low", "workdir": str(tmp_path)}))
    limits = token_controls.limits(root, "eng-001")
    assert limits["threshold"] <= 5000
    assert limits["max_output_tokens"] > 0


def test_cold_seam_is_the_restructure_moment():
    cold = prompt_cache.assess(prefix_bytes=80_000, age_s=7200, ttl="1h")
    assert cold["recommendation"] == "restructure-free"
    warm = prompt_cache.assess(prefix_bytes=80_000, age_s=60, ttl="1h")
    assert warm["recommendation"] == "keep-prefix"


def test_compaction_prompt_keeps_invariants_stable():
    from hx.compaction_policy import compact_instructions
    text = compact_instructions()
    assert "invariant" in text.lower()
    assert "cold seam" in text.lower()
