#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly REQUIREMENTS_PATH="deploy/production-env-requirements.conf"
readonly CONTRACT_VERSION="1"

declare -A CONTRACT_KIND=()
declare -A CONTRACT_FIXED_VALUE=()
declare -A ENV_VALUE=()
declare -A ENV_SEEN=()

DECODED_VALUE=""

usage() {
  cat <<'USAGE'
Использование:
  deploy/production-env-preflight.sh \
    --repository PATH \
    --sha APPLICATION_SHA \
    --runtime-root PATH \
    --env-file PATH

Проверяет exact-SHA production requirements и внешний web.env. Файл окружения
никогда не выполняется как shell-код и его значения не выводятся.
USAGE
}

die() {
  printf 'Ошибка production env preflight: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
}

private_mode() {
  local path="$1"
  local mode

  mode="$(stat -c '%a' -- "$path" 2>/dev/null)" \
    || die "не удалось проверить permissions: $path"
  [[ "$mode" =~ ^[0-7]+$ ]] || die "не удалось разобрать permissions: $path"
  printf '%s' "$mode"
}

assert_current_owner() {
  local path="$1"
  local label="$2"
  local owner

  owner="$(stat -c '%u' -- "$path" 2>/dev/null)" \
    || die "не удалось проверить owner: $label"
  [[ "$owner" == "$(id -u)" ]] \
    || die "$label должен принадлежать текущему оператору"
}

assert_private_directory() {
  local path="$1"
  local label="$2"
  local mode

  [[ -d "$path" && ! -L "$path" ]] \
    || die "$label должен быть обычным directory без symlink"
  assert_current_owner "$path" "$label"
  mode="$(private_mode "$path")"
  (( (8#$mode & 077) == 0 )) \
    || die "$label не должен быть доступен group/other"
}

register_contract_name() {
  local key="$1"
  local kind="$2"

  [[ -z "${CONTRACT_KIND[$key]:-}" ]] \
    || die "requirements contract содержит duplicate variable: $key"
  CONTRACT_KIND["$key"]="$kind"
}

parse_requirements_contract() {
  local content="$1"
  local line line_number=0 format_seen=0 key value

  while IFS= read -r line || [[ -n "$line" ]]; do
    line_number=$((line_number + 1))
    [[ "$line" != *$'\r'* ]] \
      || die "requirements contract содержит CR на line $line_number"

    if [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi

    if [[ "$line" == "format_version=$CONTRACT_VERSION" ]]; then
      (( format_seen == 0 )) \
        || die "requirements contract содержит duplicate format_version"
      format_seen=1
      continue
    fi

    if [[ "$line" =~ ^format_version= ]]; then
      die "requirements contract содержит unsupported format_version на line $line_number"
    fi

    if [[ "$line" =~ ^required=([A-Za-z_][A-Za-z0-9_]*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      register_contract_name "$key" required
      continue
    fi

    if [[ "$line" =~ ^optional=([A-Za-z_][A-Za-z0-9_]*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      register_contract_name "$key" optional
      continue
    fi

    if [[ "$line" =~ ^fixed=([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      [[ -n "$value" && "$value" != *$'\r'* && ! "$value" =~ [[:cntrl:]] ]] \
        || die "requirements contract содержит malformed fixed entry на line $line_number"
      [[ "$key" != *SECRET* && "$key" != *TOKEN* && "$key" != *PASSWORD* \
        && "$key" != *PRIVATE* && "$key" != *API_KEY* ]] \
        || die "requirements contract не может фиксировать secret-like variable: $key"
      register_contract_name "$key" fixed
      CONTRACT_FIXED_VALUE["$key"]="$value"
      continue
    fi

    die "requirements contract содержит malformed entry на line $line_number"
  done <<< "$content"

  (( format_seen == 1 )) || die "requirements contract не содержит format_version=1"
  (( ${#CONTRACT_KIND[@]} > 0 )) \
    || die "requirements contract не содержит ни одной variable requirement"
}

trim_outer_whitespace() {
  local value="$1"

  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

decode_env_value() {
  local raw="$1"
  local quote inner

  raw="$(trim_outer_whitespace "$raw")"
  if [[ "$raw" == \"* || "$raw" == \'* ]]; then
    [[ ${#raw} -ge 2 ]] || die "env file contains malformed quoted value"
    quote="${raw:0:1}"
    [[ "${raw: -1}" == "$quote" ]] \
      || die "env file contains unterminated quoted value"
    inner="${raw:1:${#raw}-2}"
    if [[ "$quote" == '"' ]]; then
      [[ "$inner" != *'"'* ]] || die "env file contains malformed quoted value"
    else
      [[ "$inner" != *"'"* ]] || die "env file contains malformed quoted value"
    fi
    [[ "$inner" != *'\\'* ]] \
      || die "env file contains unsupported escape syntax"
    DECODED_VALUE="$inner"
    return 0
  fi

  [[ "$raw" != *'"'* && "$raw" != *"'"* && "$raw" != *$'\r'* ]] \
    || die "env file contains unsupported value syntax"
  DECODED_VALUE="$raw"
}

parse_env_file() {
  local line line_number=0 key raw

  while IFS= read -r line || [[ -n "$line" ]]; do
    line_number=$((line_number + 1))
    [[ "$line" != *$'\r'* ]] \
      || die "env file contains CR on line $line_number"
    if [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi

    if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      raw="${BASH_REMATCH[2]}"
    else
      die "env file contains malformed entry on line $line_number"
    fi

    [[ -z "${ENV_SEEN[$key]:-}" ]] \
      || die "env file contains duplicate variable: $key"
    [[ -n "${CONTRACT_KIND[$key]:-}" ]] \
      || die "env file contains undeclared variable: $key"
    decode_env_value "$raw"
    ENV_SEEN["$key"]=1
    ENV_VALUE["$key"]="$DECODED_VALUE"
  done < "$ENV_FILE"
}

validate_requirements() {
  local key kind

  for key in "${!CONTRACT_KIND[@]}"; do
    kind="${CONTRACT_KIND[$key]}"
    case "$kind" in
      required)
        [[ -n "${ENV_SEEN[$key]:-}" ]] \
          || die "env file is missing required variable: $key"
        [[ -n "${ENV_VALUE[$key]}" ]] \
          || die "required variable is empty: $key"
        ;;
      fixed)
        [[ -n "${ENV_SEEN[$key]:-}" ]] \
          || die "env file is missing required variable: $key"
        [[ -n "${ENV_VALUE[$key]}" ]] \
          || die "required variable is empty: $key"
        [[ "${ENV_VALUE[$key]}" == "${CONTRACT_FIXED_VALUE[$key]}" ]] \
          || die "fixed variable has an unexpected value: $key"
        ;;
      optional)
        ;;
      *)
        die "requirements contract has unknown internal kind for: $key"
        ;;
    esac
  done
}

REPOSITORY=""
TARGET_SHA=""
RUNTIME_ROOT=""
ENV_FILE=""

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
    --runtime-root)
      (( $# >= 2 )) || die "для --runtime-root нужен PATH"
      RUNTIME_ROOT="$2"
      shift 2
      ;;
    --env-file)
      (( $# >= 2 )) || die "для --env-file нужен PATH"
      ENV_FILE="$2"
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

[[ "$REPOSITORY" == /* && "$RUNTIME_ROOT" == /* && "$ENV_FILE" == /* ]] \
  || die "repository, runtime-root и env-file должны быть absolute paths"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || die "--sha должен быть exact 40-character lowercase Git SHA"

for command in git readlink stat id; do
  require_command "$command"
done

[[ -d "$REPOSITORY" && ! -L "$REPOSITORY" ]] \
  || die "repository недоступен или является symlink"
REPOSITORY_REAL="$(readlink -f -- "$REPOSITORY")" \
  || die "не удалось разрешить repository"
assert_private_directory "$RUNTIME_ROOT" "runtime directory"
RUNTIME_REAL="$(readlink -f -- "$RUNTIME_ROOT")" \
  || die "не удалось разрешить runtime directory"
[[ "$ENV_FILE" == "$RUNTIME_ROOT/web.env" ]] \
  || die "production env должен находиться в runtime/web.env"
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" && -r "$ENV_FILE" ]] \
  || die "production web.env недоступен или является symlink"
[[ "$(readlink -f -- "$ENV_FILE")" == "$RUNTIME_REAL/web.env" ]] \
  || die "production web.env path не совпадает с runtime directory"
assert_current_owner "$ENV_FILE" "production web.env"
[[ "$(private_mode "$ENV_FILE")" == "600" ]] \
  || die "production web.env должен иметь mode 600"
[[ "$ENV_FILE" != "$REPOSITORY_REAL"/* ]] \
  || die "production web.env нельзя хранить внутри repository"

REQUIREMENTS_OBJECT="$TARGET_SHA:$REQUIREMENTS_PATH"
REQUIREMENTS_TYPE="$(git -C "$REPOSITORY" cat-file -t "$REQUIREMENTS_OBJECT" 2>/dev/null)" \
  || die "requirements contract отсутствует в exact target SHA"
[[ "$REQUIREMENTS_TYPE" == "blob" ]] \
  || die "requirements contract должен быть regular Git blob"
REQUIREMENTS_TREE_ENTRY="$(git -C "$REPOSITORY" ls-tree "$TARGET_SHA" -- "$REQUIREMENTS_PATH" 2>/dev/null)" \
  || die "не удалось проверить mode requirements contract"
[[ "$REQUIREMENTS_TREE_ENTRY" =~ ^100644[[:space:]]blob[[:space:]] ]] \
  || die "requirements contract должен быть tracked regular file"
REQUIREMENTS_CONTENT="$(git -C "$REPOSITORY" show "$REQUIREMENTS_OBJECT" 2>/dev/null)" \
  || die "не удалось прочитать requirements contract из exact target SHA"

parse_requirements_contract "$REQUIREMENTS_CONTENT"
parse_env_file
validate_requirements

printf 'Production env preflight passed for release %s.\n' "$TARGET_SHA"
