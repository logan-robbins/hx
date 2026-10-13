# Provider model registry

Latest models per provider, web-verified October 2026. Skeleton defaults
ship the Anthropic IDs; other providers are named here because adapters,
companions, or decision calls can be pointed at them by operator config.
Model IDs in instance configs are operator choices — fixtures and existing
instances keep theirs; this registry governs what new skeletons suggest.

| Provider | Latest | ID | hx use |
|---|---|---|---|
| Anthropic | Opus 5.5 (2026-09-22) | `claude-opus-5-5` | Partner and worker default |
| Anthropic | Sonnet 5.5 (2026-09-28) | `claude-sonnet-5-5` | Live tests, escalation tier |
| Anthropic | Haiku 5.5 (2026-10-07) | `claude-haiku-5-5` | Companion default, effort low, 8k cap |
| Anthropic | Fable 5.1 | `claude-fable-5-1` | Available, not a default |
| OpenAI | GPT-6 Astra | `gpt-6-astra` | Flagship / frontier |
| OpenAI | GPT-6.1 Sol | `gpt-6.1-sol` | Codex coding lanes (strongest current coder) |
| OpenAI | GPT-6 Luna | `gpt-6-luna` | Small / fast |
| OpenAI | GPT-5.6 Sol | `gpt-5.6-sol` | Codex adapter when configured |
| OpenAI | GPT-5.5 | `gpt-5.5` | Previous Codex default |
| OpenAI | Codex-specialized (deprecated 2027-04) | `gpt-5.3-codex` | Legacy; migrate to `gpt-6-sol` |
| xAI | Grok 4.7 (2026-09-21) | `grok-4.7` | Grok adapter when configured |
| xAI | Grok coding agent | `grok-build-0.1` | Grok Build lanes |
| Google | Gemini 3.8 Flash (2026-09-02) | `gemini-3.8-flash` | Reference only, no hx adapter |
| Google | Gemini 3.5 Flash | `gemini-3.5-flash` | Reference only |
| Meta | Muse Wonder (2026-10-12) | `muse-wonder-1.0` | Meta Model API lanes |
| Meta | Muse Wonder 1.0 Contributor (2026-10-12) | `muse-wonder-1.0-contributor` | Contributor mode default (Muse sessions) |
| Meta | Muse Spark 1.3 (2026-09-02) | `muse-spark-1.3` | Previous Meta default |
| TypeSafe | Jev (System One) | `jev-1.13.0` | Decision model, pinned in `hx.jev` |

Rules: pin versioned IDs, never aliases (`jev-latest`, `*-latest`), in
shipped config; tuned thresholds are version-specific. Jev remains
`jev-1.13.0` — the only real hosted model at verification time, with
`jev-latest` as its alias. Companion stays on the cheapest current Haiku
at low effort; escalation for tests and workers is Sonnet 5.5, then Opus 5.5.
Contributor-mode agents on the Meta adapter use `muse-wonder-1.0-contributor`;
API-configured Meta lanes use `muse-wonder-1.0`.
