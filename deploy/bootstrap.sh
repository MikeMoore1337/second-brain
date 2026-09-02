#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Использование:
  deploy/bootstrap.sh --root PATH [options]

Проверяет Linux/VPS layout, непривилегированного оператора, два чистых
синхронизированных sibling worktree, Python 3.14, uv и git; затем выполняет
locked sync и CLI smoke.

Опции:
  --root PATH             Корень layout с second-brain/, second-brain-vault/ и runtime/.
  --env-file PATH         Явный runtime env-файл; по умолчанию ROOT/runtime/.env.
  --install-python        Явно разрешить user-level `uv python install 3.14`.
  --check-gh              Проверить существующую авторизацию `gh` без вывода credentials.
  --proposal-preflight    Запустить proposal dry-run; note и Git refs не изменяются.
  -h, --help              Показать эту справку.
USAGE
}

die() {
  printf 'Ошибка: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
}

resolve_existing_path() {
  readlink -f -- "$1" 2>/dev/null
}

private_mode() {
  local path="$1"
  local mode

  mode="$(stat -c '%a' -- "$path" 2>/dev/null)" || die "не удалось проверить permissions: $path"
  [[ "$mode" =~ ^[0-7]+$ ]] || die "не удалось разобрать permissions: $path"
  printf '%s' "$mode"
}

assert_private_directory() {
  local path="$1"
  local label="$2"
  local mode

  mode="$(private_mode "$path")"
  (( (8#$mode & 077) == 0 )) || die "$label должен быть доступен только оператору: $path"
}

assert_current_owner() {
  local path="$1"
  local label="$2"
  local owner current

  owner="$(stat -c '%u' -- "$path" 2>/dev/null)" || die "не удалось проверить owner: $path"
  current="$(id -u)"
  [[ "$owner" == "$current" ]] || die "$label должен принадлежать текущему оператору: $path"
}

assert_owner_writable_directory() {
  local path="$1"
  local label="$2"
  local mode

  mode="$(private_mode "$path")"
  (( (8#$mode & 022) == 0 )) || die "$label не должен быть writable для group/other: $path"
  [[ -w "$path" ]] || die "$label недоступен для записи текущему оператору: $path"
}

is_inside() {
  local parent="$1"
  local child="$2"
  case "$child" in
    "$parent"/*) return 0 ;;
    *) return 1 ;;
  esac
}

validate_env_file() {
  local path="$1"
  local line key seen=0

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*= ]]; then
      key="${BASH_REMATCH[1]}"
      [[ "$key" == "SECOND_BRAIN_VAULT_PATH" ]] \
        || die "env file содержит недокументированную настройку: $key"
      (( seen == 0 )) || die "SECOND_BRAIN_VAULT_PATH указан более одного раза"
      seen=1
      continue
    fi
    die "env file содержит строку вне поддерживаемого формата"
  done < "$path"

  (( seen == 1 )) || die "env file должен содержать SECOND_BRAIN_VAULT_PATH"
}

check_git_worktree() {
  local path="$1"
  local label="$2"
  local top branch status marker marker_path local_sha origin_sha

  top="$(git -C "$path" rev-parse --show-toplevel 2>/dev/null)" \
    || die "$label не является Git worktree"
  top="$(resolve_existing_path "$top")" || die "не удалось разрешить root $label"
  [[ "$top" == "$path" ]] || die "$label должен оставаться отдельным repository root"
  [[ "$(git -C "$path" rev-parse --is-inside-work-tree 2>/dev/null)" == "true" ]] \
    || die "$label не является обычным worktree"
  [[ "$(git -C "$path" rev-parse --is-bare-repository 2>/dev/null)" == "false" ]] \
    || die "$label не должен быть bare repository"
  [[ -z "$(git -C "$path" rev-parse --show-superproject-working-tree 2>/dev/null)" ]] \
    || die "$label не должен быть Git submodule"
  git -C "$path" remote get-url origin >/dev/null 2>&1 \
    || die "$label не имеет remote origin"

  branch="$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null)" \
    || die "$label находится в detached HEAD"
  [[ "$branch" == "main" ]] || die "$label должен находиться на branch main; переключение не выполняется"

  status="$(git -C "$path" status --porcelain=v1 --untracked-files=all 2>/dev/null)" \
    || die "не удалось проверить clean state $label"
  [[ -z "$status" ]] || die "$label dirty; автоматическая очистка запрещена"

  for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD REBASE_HEAD rebase-merge rebase-apply BISECT_LOG; do
    marker_path="$(git -C "$path" rev-parse --git-path "$marker" 2>/dev/null)" \
      || die "не удалось проверить Git operation в $label"
    if [[ "$marker_path" != /* ]]; then
      marker_path="$path/$marker_path"
    fi
    [[ ! -e "$marker_path" ]] || die "$label имеет незавершённую Git operation: $marker"
  done

  local_sha="$(git -C "$path" rev-parse --verify 'refs/heads/main^{commit}' 2>/dev/null)" \
    || die "$label: local main недоступен"
  origin_sha="$(git -C "$path" rev-parse --verify 'refs/remotes/origin/main^{commit}' 2>/dev/null)" \
    || die "$label: origin/main недоступен; выполните явный fetch и повторите проверку"
  [[ "$local_sha" == "$origin_sha" ]] \
    || die "$label: local main не синхронизирован с origin/main; auto-fix запрещён"
}

find_python_314() {
  local python_path version

  if ! python_path="$(uv python find 3.14 2>/dev/null)"; then
    if (( install_python == 0 )); then
      die "Python 3.14.x не найден; установите его отдельно или повторите с явным --install-python"
    fi
    uv python install 3.14 >/dev/null 2>&1 \
      || die "не удалось установить Python 3.14 через user-level uv"
    python_path="$(uv python find 3.14 2>/dev/null)" \
      || die "после установки Python 3.14 не найден"
  fi

  [[ -n "$python_path" && -x "$python_path" ]] \
    || die "uv не вернул исполняемый Python 3.14"
  version="$("$python_path" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null)" \
    || die "не удалось проверить версию Python"
  [[ "$version" == 3.14.* ]] || die "найден неподдерживаемый Python: $version"
}

run_cli() {
  local description="$1"
  shift
  if ! (cd "$brain_root" && uv run --python 3.14 second-brain --env-file "$env_file" "$@"); then
    die "CLI smoke '$description' завершился ошибкой"
  fi
}

root_arg="${SECOND_BRAIN_ROOT:-}"
env_arg="${SECOND_BRAIN_ENV_FILE:-}"
install_python=0
check_gh=0
proposal_preflight=0

while (( $# > 0 )); do
  case "$1" in
    --root)
      (( $# >= 2 )) || die "для --root нужен PATH"
      root_arg="$2"
      shift 2
      ;;
    --env-file)
      (( $# >= 2 )) || die "для --env-file нужен PATH"
      env_arg="$2"
      shift 2
      ;;
    --install-python)
      install_python=1
      shift
      ;;
    --check-gh)
      check_gh=1
      shift
      ;;
    --proposal-preflight)
      proposal_preflight=1
      shift
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

[[ "$(uname -s)" == "Linux" ]] || die "bootstrap поддерживает только Linux"
[[ "$(id -u)" != "0" ]] || die "запускайте bootstrap от непривилегированного пользователя"
[[ -n "$root_arg" ]] || die "укажите --root PATH или SECOND_BRAIN_ROOT"

require_command git
require_command readlink
require_command stat
require_command uv

root="$(resolve_existing_path "$root_arg")" || die "root не существует: $root_arg"
[[ -d "$root" ]] || die "root не является каталогом: $root"

brain_root="$(resolve_existing_path "$root/second-brain")" \
  || die "не найден sibling repository second-brain"
vault_root="$(resolve_existing_path "$root/second-brain-vault")" \
  || die "не найден sibling repository second-brain-vault"
[[ ! -L "$root/second-brain" ]] || die "second-brain должен быть обычным sibling directory"
[[ ! -L "$root/second-brain-vault" ]] || die "second-brain-vault должен быть обычным sibling directory"
[[ -d "$brain_root" ]] || die "second-brain не является каталогом"
[[ -d "$vault_root" ]] || die "second-brain-vault не является каталогом"

runtime_arg="$root/runtime"
if [[ -e "$runtime_arg" && ! -d "$runtime_arg" ]]; then
  die "runtime занят не-каталогом: $runtime_arg"
fi
[[ ! -L "$runtime_arg" ]] || die "runtime должен быть обычным каталогом"
assert_current_owner "$brain_root" "second-brain worktree"
assert_owner_writable_directory "$brain_root" "second-brain worktree"
assert_current_owner "$vault_root" "second-brain-vault worktree"
assert_owner_writable_directory "$vault_root" "second-brain-vault worktree"
assert_private_directory "$vault_root" "second-brain-vault"
check_git_worktree "$brain_root" "second-brain"
check_git_worktree "$vault_root" "second-brain-vault"

if [[ ! -e "$runtime_arg" ]]; then
  (umask 077 && mkdir -- "$runtime_arg") || die "не удалось создать runtime directory"
fi
runtime_root="$(resolve_existing_path "$runtime_arg")" || die "не удалось разрешить runtime directory"
assert_current_owner "$runtime_root" "runtime directory"
assert_private_directory "$runtime_root" "runtime directory"

if [[ -z "$env_arg" ]]; then
  env_arg="$runtime_root/.env"
fi
env_file="$(resolve_existing_path "$env_arg")" || die "env file не существует: $env_arg"
[[ -f "$env_file" ]] || die "env file не является обычным файлом: $env_file"
assert_current_owner "$(resolve_existing_path "$(dirname -- "$env_file")")" "каталог env file"
assert_private_directory "$(resolve_existing_path "$(dirname -- "$env_file")")" "каталог env file"
assert_current_owner "$env_file" "env file"
env_mode="$(private_mode "$env_file")"
(( (8#$env_mode & 077) == 0 )) || die "env file должен иметь private permissions: $env_file"
is_inside "$brain_root" "$env_file" && die "реальный env file нельзя хранить внутри second-brain"
is_inside "$vault_root" "$env_file" && die "реальный env file нельзя хранить внутри second-brain-vault"
validate_env_file "$env_file"

if (( check_gh )); then
  require_command gh
  gh auth status >/dev/null 2>&1 || die "gh auth status не прошёл; настройте существующую auth-сессию без передачи токена скрипту"
fi

find_python_314

if ! (cd "$brain_root" && uv sync --locked --python 3.14); then
  die "uv sync --locked --python 3.14 завершился ошибкой"
fi

configured_vault="$(cd "$brain_root" && uv run --python 3.14 python -c '
import sys
from pathlib import Path

from second_brain.config import load_config

print(load_config(env_file=Path(sys.argv[1])).vault_path)
' "$env_file" 2>/dev/null)" || die "не удалось разрешить SECOND_BRAIN_VAULT_PATH через приложение"
configured_vault="$(resolve_existing_path "$configured_vault")" \
  || die "разрешённый SECOND_BRAIN_VAULT_PATH недоступен"
[[ "$configured_vault" == "$vault_root" ]] \
  || die "SECOND_BRAIN_VAULT_PATH должен указывать на sibling second-brain-vault"

run_cli "doctor" doctor
run_cli "vault validate" vault validate

if (( proposal_preflight )); then
  run_cli "proposal dry-run" proposal note create --type zettel --title "Deployment preflight" --format json
fi

printf 'Готово: runtime bootstrap и CLI smoke завершены успешно.\n'
