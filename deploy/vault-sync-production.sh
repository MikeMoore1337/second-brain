#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PRODUCTION_ROOT=""
TARGET_SHA=""

usage() {
  cat <<'USAGE'
Использование:
  deploy/vault-sync-production.sh \
    --production-root PRODUCTION_ROOT \
    --target-sha VAULT_SHA

Узкий непривилегированный wrapper запускает существующий
second_brain.adapters.vault.sync для bounded production layout,
производного от явно переданного trusted PRODUCTION_ROOT.
USAGE
}

die() {
  printf 'Vault production sync wrapper STOP / HUMAN_REQUIRED: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
}

assert_bounded_production_root() {
  local path="$1"

  [[ ${#path} -ge 2 && ${#path} -le 200 ]] \
    || die "production root должен иметь длину от 2 до 200 символов"
  [[ "$path" =~ ^/([A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$ ]] \
    || die "production root должен быть bounded absolute path"
  case "$path" in
    */./*|*/../*|*/.|*/..) die "production root не должен содержать . или .. path component" ;;
  esac
}

assert_path_within_root() {
  local path="$1"
  local label="$2"

  [[ "$path" == "$PRODUCTION_ROOT/"* ]] \
    || die "$label находится вне configured production root"
}

assert_directory() {
  local path="$1"
  local label="$2"

  [[ -d "$path" && ! -L "$path" ]] \
    || die "$label должен быть обычным directory без symlink"
}

assert_private_directory() {
  local path="$1"
  local label="$2"
  local mode

  mode="$(stat -c '%a' -- "$path" 2>/dev/null)" \
    || die "не удалось проверить permissions: $label"
  [[ "$mode" =~ ^[0-7]+$ ]] || die "не удалось разобрать permissions: $label"
  (( (8#$mode & 077) == 0 )) \
    || die "$label не должен быть доступен group/other"
}

assert_production_layout() {
  local current_target current_real release_real releases_real release_sha
  local production_real vault_real runtime_real

  assert_directory "$PRODUCTION_ROOT" "production root"
  production_real="$(readlink -f -- "$PRODUCTION_ROOT" 2>/dev/null)" \
    || die "не удалось разрешить production root"
  [[ "$production_real" == "$PRODUCTION_ROOT" ]] \
    || die "production root path escaped configured root"
  assert_directory "$VAULT_ROOT" "production vault"
  assert_directory "$RELEASES_ROOT" "application releases root"
  assert_directory "$RUNTIME_ROOT" "production runtime"
  assert_private_directory "$RUNTIME_ROOT" "production runtime"

  [[ -L "$APP_ROOT" ]] \
    || die "current должен быть symlink на immutable release"
  current_target="$(readlink -- "$APP_ROOT" 2>/dev/null)" \
    || die "не удалось прочитать current symlink"
  [[ "$current_target" =~ ^releases/[0-9a-f]{40}$ ]] \
    || die "current должен указывать на releases/<40-character SHA>"
  release_sha="${current_target#releases/}"
  [[ -d "$RELEASES_ROOT/$release_sha" && ! -L "$RELEASES_ROOT/$release_sha" ]] \
    || die "current указывает на отсутствующий или symlink release"

  releases_real="$(readlink -f -- "$RELEASES_ROOT" 2>/dev/null)" \
    || die "не удалось разрешить releases root"
  current_real="$(readlink -f -- "$APP_ROOT" 2>/dev/null)" \
    || die "не удалось разрешить current"
  release_real="$(readlink -f -- "$RELEASES_ROOT/$release_sha" 2>/dev/null)" \
    || die "не удалось разрешить current release"
  [[ "$current_real" == "$release_real" && "$current_real" == "$releases_real/$release_sha" ]] \
    || die "current path escaped exact releases/<SHA> directory"

  [[ "$(git -C "$APP_ROOT" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" == "$release_sha" ]] \
    || die "current release Git HEAD does not match its immutable directory SHA"
  [[ -d "$APP_ROOT/.venv" && ! -L "$APP_ROOT/.venv" ]] \
    || die "current .venv должен быть directory без symlink"
  [[ -f "$PYTHON" && -x "$PYTHON" ]] \
    || die "current .venv/bin/python отсутствует или не executable"
  [[ -r "$APP_ROOT" && -x "$APP_ROOT" ]] \
    || die "current application runtime недоступен"

  [[ -r "$VAULT_ROOT" && -w "$VAULT_ROOT" && -x "$VAULT_ROOT" ]] \
    || die "production vault недоступен для bounded sync"
  vault_real="$(readlink -f -- "$VAULT_ROOT" 2>/dev/null)" \
    || die "не удалось разрешить production vault"
  [[ "$vault_real" == "$VAULT_ROOT" ]] \
    || die "production vault path escaped fixed layout"

  [[ -r "$RUNTIME_ROOT" && -w "$RUNTIME_ROOT" && -x "$RUNTIME_ROOT" ]] \
    || die "production runtime недоступен для lock/backup"
  runtime_real="$(readlink -f -- "$RUNTIME_ROOT" 2>/dev/null)" \
    || die "не удалось разрешить production runtime"
  [[ "$runtime_real" == "$RUNTIME_ROOT" ]] \
    || die "production runtime path escaped fixed layout"

  if [[ -e "$BACKUP_ROOT" || -L "$BACKUP_ROOT" ]]; then
    [[ -d "$BACKUP_ROOT" && ! -L "$BACKUP_ROOT" ]] \
      || die "backup root должен быть обычным directory без symlink"
  fi
  if [[ -e "$LOCK_PATH" || -L "$LOCK_PATH" ]]; then
    [[ -f "$LOCK_PATH" && ! -L "$LOCK_PATH" ]] \
      || die "vault operation lock должен быть обычным file без symlink"
  fi
}

while (( $# > 0 )); do
  case "$1" in
    --production-root)
      (( $# >= 2 )) || die "для --production-root нужен PRODUCTION_ROOT"
      PRODUCTION_ROOT="$2"
      shift 2
      ;;
    --target-sha)
      (( $# >= 2 )) || die "для --target-sha нужен VAULT_SHA"
      TARGET_SHA="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "неизвестная опция"
      ;;
  esac
done

[[ -n "$PRODUCTION_ROOT" ]] \
  || die "нужен explicit --production-root из trusted configuration"
assert_bounded_production_root "$PRODUCTION_ROOT"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || die "--target-sha должен быть exact 40-character lowercase Git SHA"
[[ "$(uname -s)" == "Linux" ]] || die "wrapper поддерживает только Linux"
[[ "$(id -u)" != "0" ]] || die "production sync нельзя запускать от root"

readonly VAULT_ROOT="$PRODUCTION_ROOT/second-brain-vault"
readonly APP_ROOT="$PRODUCTION_ROOT/current"
readonly RELEASES_ROOT="$PRODUCTION_ROOT/releases"
readonly RUNTIME_ROOT="$PRODUCTION_ROOT/runtime"
readonly BACKUP_ROOT="$RUNTIME_ROOT/vault-backups"
readonly LOCK_PATH="$RUNTIME_ROOT/vault-sync.lock"
readonly PYTHON="$APP_ROOT/.venv/bin/python"
readonly EXPECTED_BRANCH="main"
readonly EXPECTED_REMOTE="https://github.com/MikeMoore1337/second-brain-vault.git"

for path_spec in \
  "${VAULT_ROOT}:production vault" \
  "${APP_ROOT}:application current" \
  "${RELEASES_ROOT}:application releases root" \
  "${RUNTIME_ROOT}:production runtime" \
  "${BACKUP_ROOT}:backup root" \
  "${LOCK_PATH}:vault operation lock"; do
  path_value="${path_spec%%:*}"
  path_label="${path_spec#*:}"
  assert_path_within_root "$path_value" "$path_label"
done

for command in git id readlink stat uname; do
  require_command "$command"
done

assert_production_layout

exec "$PYTHON" \
  -m second_brain.adapters.vault.sync \
  --vault-root "$VAULT_ROOT" \
  --backup-root "$BACKUP_ROOT" \
  --lock-path "$LOCK_PATH" \
  --app-root "$APP_ROOT" \
  --target-sha "$TARGET_SHA" \
  --expected-remote "$EXPECTED_REMOTE" \
  --expected-branch "$EXPECTED_BRANCH" \
  --apply \
  --format json
