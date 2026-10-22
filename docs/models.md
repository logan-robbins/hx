# Provider models

Every current model ID per provider, taken from each provider's own
documentation and models API. Listed, not ranked:
which one a given agent uses is instance config. Pin versioned IDs, never
aliases, in shipped config.

## Anthropic

Source: [platform.claude.com/docs/en/models/overview](https://platform.claude.com/docs/en/models/overview)
and `GET api.anthropic.com/v1/models` (both agree).

- `claude-fable-5-1`
- `claude-opus-5-5`
- `claude-sonnet-5-5`
- `claude-haiku-5-5`
- `claude-fable-5`
- `claude-opus-5`
- `claude-sonnet-5`

All current-generation models have a 1M-token context window per the
official overview. Older generations returned by the API (Opus 4.x,
Sonnet 4.x, Haiku 4.5) remain listed there for existing deployments.

## OpenAI

Source: [developers.openai.com/api/docs/models](https://developers.openai.com/api/docs/models)
and `GET api.openai.com/v1/models` (both agree).

- `gpt-6-astra`
- `gpt-6.1-sol`
- `gpt-6-sol`
- `gpt-6-luna`
- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `gpt-5.5`
- `gpt-5.4`
- `gpt-5.3-codex`

## Meta

Source: [developers.meta.com](https://developers.meta.com/resources/blog/)
(Muse Wonder and Muse Wonder Full, 12 October 2026) and the Meta Model API.

- `muse-wonder-1.0`
- `muse-wonder-1.0-contributor`
- `muse-spark-1.3`

## TypeSafe

- `jev-1.13.0` (decision model, pinned in `hx.jev`; `jev-latest` is its alias)

## hx defaults

New skeletons suggest: Partner and workers on `claude-opus-5-5`, companion
on `claude-haiku-5-5` (low effort, 8k output cap), tests on
`claude-sonnet-5-5`, and contributor-mode workers from
`templates/contributor/` on `muse-wonder-1.0-contributor` via the Meta
adapter. Configure other supported provider model IDs explicitly in the instance model registry.
