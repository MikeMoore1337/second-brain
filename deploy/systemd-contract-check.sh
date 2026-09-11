#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly UNIT_RELATIVE_PATH="deploy/systemd/second-brain-web.service"

REPOSITORY=""
TARGET_SHA=""
INSTALLED_UNIT=""
DROPIN_DIRECTORY=""

usage() {
  cat <<'USAGE'
Использование:
  deploy/systemd-contract-check.sh \
    --repository PATH \
    --sha APPLICATION_SHA \
    --installed-unit PATH \
    --drop-in-directory PATH

Проверяет, что tracked systemd unit exact target SHA уже установлен owner/root
как безопасный regular file без неизвестного service drop-in state.
USAGE
}

die() {
  printf 'SYSTEMD_CONTRACT_NOT_INTEGRATED / HUMAN_REQUIRED: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
}

assert_absolute_path() {
  [[ "$2" == /* ]] || die "$1 должен быть absolute path"
}

assert_safe_root_owned_path() {
  local path="$1"
  local label="$2"
  local owner mode

  owner="$(stat -c '%u' -- "$path" 2>/dev/null)" \
    || die "не удалось проверить owner: $label"
  [[ "$owner" == "0" ]] || die "$label должен принадлежать root"

  mode="$(stat -c '%a' -- "$path" 2>/dev/null)" \
    || die "не удалось проверить permissions: $label"
  [[ "$mode" =~ ^[0-7]+$ ]] || die "не удалось разобрать permissions: $label"
  (( (8#$mode & 022) == 0 )) \
    || die "$label имеет небезопасные group/other write permissions"
}

assert_installed_unit() {
  local target_entry installed_mode

  [[ -d "$REPOSITORY" && ! -L "$REPOSITORY" ]] \
    || die "repository недоступен или является symlink"
  [[ -f "$INSTALLED_UNIT" && ! -L "$INSTALLED_UNIT" ]] \
    || die "installed systemd unit должен быть regular file без symlink"
  [[ -r "$INSTALLED_UNIT" ]] \
    || die "installed systemd unit не читается deploy user"
  assert_safe_root_owned_path "$INSTALLED_UNIT" "installed systemd unit"

  installed_mode="$(stat -c '%a' -- "$INSTALLED_UNIT" 2>/dev/null)" \
    || die "не удалось проверить permissions installed systemd unit"
  [[ "$installed_mode" =~ ^[0-7]+$ ]] \
    || die "не удалось разобрать permissions installed systemd unit"
  (( (8#$installed_mode & 0444) != 0 )) \
    || die "installed systemd unit должен быть readable"

  target_entry="$(git -C "$REPOSITORY" ls-tree "$TARGET_SHA" -- "$UNIT_RELATIVE_PATH" 2>/dev/null)" \
    || die "не удалось проверить tracked systemd unit в exact target SHA"
  [[ "$target_entry" =~ ^100644[[:space:]]blob[[:space:]] ]] \
    || die "target systemd unit должен быть tracked regular file mode 100644"

  if ! git -C "$REPOSITORY" show "$TARGET_SHA:$UNIT_RELATIVE_PATH" 2>/dev/null \
    | cmp -s - "$INSTALLED_UNIT"; then
    die "installed systemd unit bytes не совпадают с exact target SHA"
  fi
}

assert_dropin_state() {
  local dropin_entry

  if [[ -L "$DROPIN_DIRECTORY" ]]; then
    die "systemd service drop-in directory является symlink"
  fi
  if [[ -e "$DROPIN_DIRECTORY" ]]; then
    [[ -d "$DROPIN_DIRECTORY" ]] \
      || die "systemd service drop-in path не является directory"
    assert_safe_root_owned_path "$DROPIN_DIRECTORY" "systemd service drop-in directory"
    dropin_entry="$(find "$DROPIN_DIRECTORY" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" \
      || die "не удалось проверить systemd service drop-in state"
    [[ -z "$dropin_entry" ]] \
      || die "обнаружен неизвестный service drop-in state; exact obsolete drop-in должен быть удалён owner/root"
  fi
}

while (( $# > 0 )); do
  case "$1" in
    --repository)
      (( $# >= 2 )) || die "для --repository нужен PATH"
      REPOSITORY="$2"
      shift 2
      ;;
    --sha)
      (( $# >= 2 )) || die "для --sha нужен APPLICATION_SHA"
      TARGET_SHA="$2"
      shift 2
      ;;
    --installed-unit)
      (( $# >= 2 )) || die "для --installed-unit нужен PATH"
      INSTALLED_UNIT="$2"
      shift 2
      ;;
    --drop-in-directory)
      (( $# >= 2 )) || die "для --drop-in-directory нужен PATH"
      DROPIN_DIRECTORY="$2"
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

[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || die "--sha должен быть exact 40-character lowercase Git SHA"
for argument in \
  "repository:$REPOSITORY" \
  "installed-unit:$INSTALLED_UNIT" \
  "drop-in-directory:$DROPIN_DIRECTORY"; do
  name="${argument%%:*}"
  value="${argument#*:}"
  assert_absolute_path "$name" "$value"
done

for command in git cmp find stat; do
  require_command "$command"
done

assert_installed_unit
assert_dropin_state

printf 'Systemd contract integrated for release %s.\n' "$TARGET_SHA"
