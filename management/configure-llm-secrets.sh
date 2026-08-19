#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
secret_dir="$project_root/.secrets"

install -d -m 700 "$secret_dir"

read_secret() {
  local filename="$1"
  local label="$2"
  local value

  read -r -s -p "$label (leave blank to keep the existing value): " value
  printf '\n'
  if [[ -z "$value" ]]; then
    return
  fi
  umask 077
  printf '%s' "$value" > "$secret_dir/$filename"
  chmod 600 "$secret_dir/$filename"
  unset value
  printf 'Updated %s\n' "$secret_dir/$filename"
}

read_secret nvidia_api_key "NVIDIA NIM API key"
read_secret ollama_api_key "Ollama Cloud API key"
