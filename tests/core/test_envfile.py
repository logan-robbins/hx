from __future__ import annotations

import json
import stat

from hx.envfile import configure, parse, sync_seed


def test_env_file_seeds_missing_provider_keys_and_keeps_local_oauth(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "ANTRHOPIC_API_KEY=sk-ant-api-example\n"
        "OPENAI_API_KEY=openai-example\n"
        "GROK_API_KEY=grok-example\n"
        "ZERNIO_API_KEY=zernio-example\n"
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "token").write_text("sk-ant-oat01-local\n")

    configure(tmp_path, str(env))
    sync_seed(tmp_path)

    assert json.loads((tmp_path / "config/auth.json").read_text())["mode"] == "auto"
    assert (seed / "token").read_text() == "sk-ant-oat01-local\n"
    assert (seed / "codex-token").read_text() == "openai-example\n"
    assert (seed / "grok-token").read_text() == "grok-example\n"
    assert stat.S_IMODE((seed / "codex-token").stat().st_mode) == 0o600
    assert parse(env)["ZERNIO_API_KEY"] == "zernio-example"


def test_env_file_falls_back_to_anthropic_api_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTRHOPIC_API_KEY=sk-ant-api-example\n")
    configure(tmp_path, str(env))
    sync_seed(tmp_path)
    assert (tmp_path / "seed/token").read_text() == "sk-ant-api-example\n"
