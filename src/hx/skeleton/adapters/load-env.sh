#!/usr/bin/env bash
# Load configured dotenv values inside the agent process, never through tmux argv.
auth_config=$root/config/auth.json
if [ -f "$auth_config" ]; then
  env_file=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("env_file") or "")' "$auth_config")
  if [ -n "$env_file" ]; then
    while IFS= read -r -d '' assignment; do
      export "$assignment"
    done < <("$python" -m hx.envfile "$env_file")
  fi
fi
