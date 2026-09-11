#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly ENTRYPOINT_RELATIVE_PATH=".venv/bin/second-brain"

RELEASES_ROOT=""
RELEASE_SHA=""
CANDIDATE_RELEASE=""

usage() {
  cat <<'USAGE'
Использование:
  deploy/runtime-entrypoint-check.sh \
    --releases-root PATH \
    --release-sha APPLICATION_SHA \
    --candidate PATH

Проверяет exact immutable candidate .venv и установленный executable
application entrypoint, который будет запущен systemd напрямую.
USAGE
}

die() {
  printf 'RUNTIME_ENTRYPOINT_NOT_READY / HUMAN_REQUIRED: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
}

assert_absolute_path() {
  [[ "$2" == /* ]] || die "$1 должен быть absolute path"
}

assert_runtime_entrypoint() {
  local candidate_real releases_real venv_root venv_real entrypoint entrypoint_real
  local shebang interpreter

  [[ -d "$CANDIDATE_RELEASE" && ! -L "$CANDIDATE_RELEASE" ]] \
    || die "candidate release должен быть directory без symlink"
  candidate_real="$(readlink -f -- "$CANDIDATE_RELEASE" 2>/dev/null)" \
    || die "candidate path cannot be resolved"
  releases_real="$(readlink -f -- "$RELEASES_ROOT" 2>/dev/null)" \
    || die "releases root cannot be resolved"
  [[ "$candidate_real" == "$releases_real/$RELEASE_SHA" ]] \
    || die "candidate path escaped exact releases/<SHA> directory"

  venv_root="$CANDIDATE_RELEASE/.venv"
  [[ -d "$venv_root" && ! -L "$venv_root" ]] \
    || die "candidate .venv должен быть directory без symlink"
  venv_real="$(readlink -f -- "$venv_root" 2>/dev/null)" \
    || die "candidate .venv path cannot be resolved"
  [[ "$venv_real" == "$candidate_real/.venv" ]] \
    || die "candidate .venv escaped exact candidate"

  entrypoint="$venv_root/$ENTRYPOINT_RELATIVE_PATH"
  [[ -f "$entrypoint" && ! -L "$entrypoint" && -x "$entrypoint" ]] \
    || die "candidate runtime entrypoint отсутствует, unsafe или не executable"
  entrypoint_real="$(readlink -f -- "$entrypoint" 2>/dev/null)" \
    || die "candidate runtime entrypoint path cannot be resolved"
  [[ "$entrypoint_real" == "$candidate_real/$ENTRYPOINT_RELATIVE_PATH" ]] \
    || die "candidate runtime entrypoint escaped exact candidate environment"

  IFS= read -r shebang < "$entrypoint" \
    || die "candidate runtime entrypoint не содержит readable interpreter header"
  [[ "$shebang" == '#!'* && "$shebang" != *$'\r'* ]] \
    || die "candidate runtime entrypoint имеет unsupported interpreter header"
  interpreter="${shebang#\#!}"
  [[ "$interpreter" == "$candidate_real/.venv/bin/"* ]] \
    || die "candidate runtime entrypoint interpreter не относится к exact candidate .venv"
  [[ "$interpreter" != *"/../"* && "$interpreter" != *"/./"* ]] \
    || die "candidate runtime entrypoint interpreter содержит path escape"
  [[ -x "$interpreter" ]] \
    || die "candidate .venv interpreter отсутствует или не executable"
}

while (( $# > 0 )); do
  case "$1" in
    --releases-root)
      (( $# >= 2 )) || die "для --releases-root нужен PATH"
      RELEASES_ROOT="$2"
      shift 2
      ;;
    --release-sha)
      (( $# >= 2 )) || die "для --release-sha нужен APPLICATION_SHA"
      RELEASE_SHA="$2"
      shift 2
      ;;
    --candidate)
      (( $# >= 2 )) || die "для --candidate нужен PATH"
      CANDIDATE_RELEASE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "неизвестная опция: $1"
      ;;
  esac
done

[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || die "--release-sha должен быть exact 40-character lowercase Git SHA"
for argument in \
  "releases-root:$RELEASES_ROOT" \
  "candidate:$CANDIDATE_RELEASE"; do
  name="${argument%%:*}"
  value="${argument#*:}"
  assert_absolute_path "$name" "$value"
done

require_command readlink
assert_runtime_entrypoint

printf 'Runtime entrypoint passed for release %s.\n' "$RELEASE_SHA"
