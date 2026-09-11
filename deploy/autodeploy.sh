#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  cat <<'USAGE'
Использование:
  deploy/autodeploy.sh --sha APPLICATION_SHA

Выпускает exact application SHA в существующий production layout
/srv/second-brain. Скрипт предназначен для запуска непривилегированным
пользователем second-brain после успешного CI и one-time production bootstrap.
USAGE
}

die() {
  printf 'Ошибка автодеплоя: %s\n' "$*" >&2
  exit 1
}

handle_interruption() {
  local signal="$1"

  if [[ "$ACTIVATION_STARTED" == "0" ]]; then
    printf 'STOP / HUMAN_REQUIRED: autodeploy прерван (%s) до activation; current и service не изменялись; candidate сохранён для recovery.\n' \
      "$signal" >&2
    exit 143
  fi

  printf 'STOP / HUMAN_REQUIRED: autodeploy прерван (%s) после начала activation; состояние current/service нужно проверить вручную; cleanup не выполнялся.\n' \
    "$signal" >&2
  exit 143
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
}

run_target_script() {
  local script_path="$1"
  shift
  local -a pipeline_status

  set +e
  git -C "$APP_ROOT" show "$TARGET_SHA:$script_path" 2>/dev/null | bash -s -- "$@"
  pipeline_status=("${PIPESTATUS[@]}")
  set -e
  (( pipeline_status[0] == 0 && pipeline_status[1] == 0 ))
}

run_env_preflight() {
  run_target_script deploy/production-env-preflight.sh \
    --repository "$APP_ROOT" \
    --sha "$TARGET_SHA" \
    --runtime-root "$RUNTIME_ROOT" \
    --env-file "$RUNTIME_ENV"
}

run_candidate_recovery_check() {
  run_target_script deploy/candidate-recovery-check.sh \
    --control-repository "$APP_ROOT" \
    --releases-root "$RELEASES_ROOT" \
    --current-link "$CURRENT_LINK" \
    --target-sha "$TARGET_SHA" \
    --expected-main-sha "$ORIGIN_APP_SHA"
}

assert_clean_main() {
  local path="$1"
  local label="$2"
  local branch status marker marker_path

  branch="$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null)" \
    || die "$label находится в detached HEAD"
  [[ "$branch" == "main" ]] || die "$label должен находиться на branch main"

  status="$(git -C "$path" status --porcelain=v1 --untracked-files=all 2>/dev/null)" \
    || die "не удалось проверить clean state $label"
  [[ -z "$status" ]] || die "$label dirty; автоматическая очистка запрещена"

  for marker in \
    MERGE_HEAD \
    CHERRY_PICK_HEAD \
    REVERT_HEAD \
    REBASE_HEAD \
    rebase-merge \
    rebase-apply \
    BISECT_LOG \
    sequencer \
    index.lock; do
    marker_path="$(git -C "$path" rev-parse --git-path "$marker" 2>/dev/null)" \
      || die "не удалось проверить Git operation в $label"
    if [[ "$marker_path" != /* ]]; then
      marker_path="$path/$marker_path"
    fi
    [[ ! -e "$marker_path" && ! -L "$marker_path" ]] \
      || die "$label имеет незавершённую Git operation: $marker"
  done
}

read_current_sha() {
  local target sha

  [[ -L "$CURRENT_LINK" ]] \
    || die "current должен быть symlink; первый production deploy выполняется owner-managed"
  target="$(readlink "$CURRENT_LINK")" || die "не удалось прочитать current symlink"
  [[ "$target" == releases/* ]] || die "current должен указывать на releases/<SHA>"
  sha="${target#releases/}"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "current содержит некорректный release SHA"
  [[ -d "$RELEASES_ROOT/$sha" && ! -L "$RELEASES_ROOT/$sha" ]] \
    || die "current указывает на отсутствующий или symlink release"
  [[ "$(git -C "$RELEASES_ROOT/$sha" rev-parse --verify HEAD 2>/dev/null)" == "$sha" ]] \
    || die "current release не соответствует своему SHA"
  printf '%s' "$sha"
}

wait_local_health() {
  local attempt
  for attempt in $(seq 1 20); do
    if curl --fail --silent --show-error --max-time 5 \
      "http://127.0.0.1:8123/healthz" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_public_health() {
  local attempt
  for attempt in $(seq 1 10); do
    if curl --fail --silent --show-error --max-time 10 \
      "https://brain.mikemoore.top/healthz" >/dev/null; then
      return 0
    fi
    sleep 3
  done
  return 1
}

rollback_and_fail() {
  local reason="$1"

  printf 'Новый release не прошёл post-activation smoke: %s\n' "$reason" >&2
  printf 'Возвращаю current на предыдущий known-good SHA %s.\n' "$PREVIOUS_SHA" >&2

  if ! /usr/bin/sudo -n "$RELEASE_CONTROL" rollback "$PREVIOUS_SHA"; then
    printf 'Rollback release-control завершился ошибкой; дальнейшие mutation остановлены.\n' >&2
    exit 1
  fi
  if [[ "$(readlink "$CURRENT_LINK" 2>/dev/null)" != "releases/$PREVIOUS_SHA" ]]; then
    printf 'Rollback завершился, но current не указывает на предыдущий release.\n' >&2
    exit 1
  fi
  if ! /usr/bin/systemctl is-active --quiet second-brain-web.service; then
    printf 'Rollback вернул current, но service не active.\n' >&2
    exit 1
  fi
  if ! wait_local_health; then
    printf 'Rollback вернул current, но local health предыдущего release не восстановился.\n' >&2
    exit 1
  fi
  if ! wait_public_health; then
    printf 'Rollback вернул current, но public health предыдущего release не восстановился.\n' >&2
    exit 1
  fi

  printf 'Rollback успешен; failed candidate сохранён для diagnostics: %s\n' \
    "$RELEASES_ROOT/$TARGET_SHA" >&2
  exit 1
}

TARGET_SHA=""
ACTIVATION_STARTED=0
PREVIOUS_SHA=""
CURRENT_LINK=""
CANDIDATE_RELEASE=""

trap 'handle_interruption SIGTERM' TERM
trap 'handle_interruption SIGHUP' HUP
trap 'handle_interruption SIGINT' INT

while (( $# > 0 )); do
  case "$1" in
    --sha)
      (( $# >= 2 )) || die "для --sha нужен APPLICATION_SHA"
      TARGET_SHA="$2"
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

[[ "$(uname -s)" == "Linux" ]] || die "autodeploy поддерживает только Linux"
[[ "$(id -u)" != "0" ]] || die "autodeploy нельзя запускать от root"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || die "--sha должен быть exact 40-character lowercase Git SHA"

for command in bash git uv npm curl readlink flock seq; do
  require_command "$command"
done
[[ -x /usr/bin/sudo ]] || die "ожидается /usr/bin/sudo для narrowly-scoped release control"
[[ -x /usr/bin/systemctl ]] || die "ожидается /usr/bin/systemctl"

SECOND_BRAIN_ROOT=/srv/second-brain
APP_ROOT="$SECOND_BRAIN_ROOT/second-brain"
VAULT_ROOT="$SECOND_BRAIN_ROOT/second-brain-vault"
RELEASES_ROOT="$SECOND_BRAIN_ROOT/releases"
CURRENT_LINK="$SECOND_BRAIN_ROOT/current"
RUNTIME_ROOT="$SECOND_BRAIN_ROOT/runtime"
RUNTIME_ENV="$RUNTIME_ROOT/web.env"
LOCK_FILE="$RUNTIME_ROOT/autodeploy.lock"
RELEASE_CONTROL=/usr/local/sbin/second-brain-release-control

[[ -d "$APP_ROOT" ]] || die "не найден control checkout second-brain"
[[ -d "$VAULT_ROOT" ]] || die "не найден sibling second-brain-vault"
[[ -d "$RELEASES_ROOT" ]] || die "не найден releases directory"
[[ -d "$RUNTIME_ROOT" ]] || die "не найден runtime directory"
[[ ! -L "$RELEASES_ROOT" ]] || die "releases directory не должен быть symlink"
[[ ! -L "$RUNTIME_ROOT" ]] || die "runtime directory не должен быть symlink"
[[ -r "$RUNTIME_ENV" && -f "$RUNTIME_ENV" ]] || die "production web.env недоступен"
[[ -w "$RUNTIME_ROOT" ]] || die "runtime directory недоступен для deploy lock"
[[ -w "$RELEASES_ROOT" ]] || die "releases directory недоступен для candidate"
[[ -x "$RELEASE_CONTROL" ]] || die "root-owned release-control helper не установлен"
[[ "$($RELEASE_CONTROL version)" == "1" ]] || die "неподдерживаемая версия release-control helper"

exec 9>"$LOCK_FILE"
flock -n 9 || die "другой production deploy уже выполняется"

assert_clean_main "$APP_ROOT" "second-brain"
assert_clean_main "$VAULT_ROOT" "second-brain-vault"

git -C "$APP_ROOT" fetch --no-tags origin main

ORIGIN_APP_SHA="$(git -C "$APP_ROOT" rev-parse --verify 'refs/remotes/origin/main^{commit}')" \
  || die "origin/main second-brain недоступен"
if [[ "$ORIGIN_APP_SHA" != "$TARGET_SHA" ]]; then
  if git -C "$APP_ROOT" merge-base --is-ancestor "$TARGET_SHA" "$ORIGIN_APP_SHA" 2>/dev/null; then
    printf 'Пропуск autodeploy: CI SHA %s уже заменён более новым main %s.\n' \
      "$TARGET_SHA" "$ORIGIN_APP_SHA"
    exit 0
  fi
  die "CI SHA не совпадает с текущим origin/main и не является его предком"
fi

LOCAL_APP_SHA="$(git -C "$APP_ROOT" rev-parse --verify 'refs/heads/main^{commit}')" \
  || die "local main second-brain недоступен"
git -C "$APP_ROOT" merge-base --is-ancestor "$LOCAL_APP_SHA" "$TARGET_SHA" \
  || die "local main second-brain diverged или содержит local-only commits"

# Validate the exact target contract before changing the control checkout or
# creating/reusing a candidate release.
run_env_preflight \
  || die "exact target production env preflight failed; candidate creation запрещена"

git -C "$VAULT_ROOT" fetch --no-tags origin main
git -C "$APP_ROOT" merge --ff-only origin/main
[[ "$(git -C "$APP_ROOT" rev-parse --verify HEAD)" == "$TARGET_SHA" ]] \
  || die "control checkout не обновился до exact CI SHA"
assert_clean_main "$APP_ROOT" "second-brain"

LOCAL_VAULT_SHA="$(git -C "$VAULT_ROOT" rev-parse --verify 'refs/heads/main^{commit}')" \
  || die "local main second-brain-vault недоступен"
ORIGIN_VAULT_SHA="$(git -C "$VAULT_ROOT" rev-parse --verify 'refs/remotes/origin/main^{commit}')" \
  || die "origin/main second-brain-vault недоступен"
[[ "$LOCAL_VAULT_SHA" == "$ORIGIN_VAULT_SHA" ]] \
  || die "second-brain-vault не синхронизирован с origin/main; autodeploy его не обновляет"
assert_clean_main "$VAULT_ROOT" "second-brain-vault"

# Repeat immediately before any candidate filesystem mutation so an operator
# env change during fetch/merge also fails closed.
run_env_preflight \
  || die "production env preflight failed before candidate creation"

PREVIOUS_SHA="$(read_current_sha)"
if [[ "$PREVIOUS_SHA" == "$TARGET_SHA" ]]; then
  /usr/bin/systemctl is-active --quiet second-brain-web.service \
    || die "release уже active, но systemd service не active"
  wait_local_health || die "release уже active, но local health не проходит"
  wait_public_health || die "release уже active, но public health не проходит"
  printf 'Release %s уже активен и healthy.\n' "$TARGET_SHA"
  exit 0
fi

git -C "$APP_ROOT" cat-file -e "$PREVIOUS_SHA^{commit}" \
  || die "previous known-good SHA отсутствует в control checkout"
if ! git -C "$APP_ROOT" diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- \
  deploy/systemd deploy/caddy deploy/root; then
  die "root-managed deployment contract изменился; требуется owner-managed integration до autodeploy"
fi

/usr/bin/systemctl is-active --quiet second-brain-web.service \
  || die "текущий production service не active; auto-recovery через новый release запрещён"
wait_local_health || die "baseline local health текущего release не проходит"
wait_public_health || die "baseline public health текущего release не проходит"

CANDIDATE_RELEASE="$RELEASES_ROOT/$TARGET_SHA"
if [[ -e "$CANDIDATE_RELEASE" || -L "$CANDIDATE_RELEASE" ]]; then
  run_candidate_recovery_check \
    || die "existing candidate нельзя доказать recoverable; automatic deletion/reuse запрещены"
  printf 'Использую доказанно recoverable candidate %s; весь pipeline будет выполнен заново.\n' \
    "$CANDIDATE_RELEASE"
else
  git -C "$APP_ROOT" worktree add --detach "$CANDIDATE_RELEASE" "$TARGET_SHA"
  [[ "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" == "$TARGET_SHA" ]] \
    || die "candidate worktree не соответствует exact CI SHA"
fi

(
  cd "$CANDIDATE_RELEASE"
  uv sync --locked --python 3.14
  uv run --python 3.14 --no-sync second-brain --env-file "$RUNTIME_ENV" doctor
  uv run --python 3.14 --no-sync second-brain --env-file "$RUNTIME_ENV" vault validate
)

(
  cd "$CANDIDATE_RELEASE/web"
  npm ci
  npm run check
  npm run build
  npm run qa:pwa
  test -s dist/index.html
)

[[ "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" == "$TARGET_SHA" ]] \
  || die "candidate SHA изменился во время build"
[[ -z "$(git -C "$CANDIDATE_RELEASE" status --porcelain=v1 --untracked-files=no)" ]] \
  || die "tracked files candidate изменились во время build"

run_candidate_recovery_check \
  || die "final candidate integrity verification failed; activation запрещена"
run_env_preflight \
  || die "production env preflight failed before activation"
[[ "$(read_current_sha)" == "$PREVIOUS_SHA" ]] \
  || die "current изменился во время deploy; activation запрещена"
/usr/bin/systemctl is-active --quiet second-brain-web.service \
  || die "current service стал inactive до activation"
wait_local_health || die "baseline local health текущего release не проходит перед activation"
wait_public_health || die "baseline public health текущего release не проходит перед activation"

ACTIVATION_STARTED=1
if ! /usr/bin/sudo -n "$RELEASE_CONTROL" activate "$TARGET_SHA"; then
  if [[ "$(readlink "$CURRENT_LINK" 2>/dev/null || true)" == "releases/$TARGET_SHA" ]]; then
    rollback_and_fail "release-control переключил current, но activation завершился ошибкой"
  fi
  die "release-control activation завершился ошибкой до переключения current"
fi

[[ "$(readlink "$CURRENT_LINK")" == "releases/$TARGET_SHA" ]] \
  || rollback_and_fail "current не указывает на новый release после activation"
[[ "$(git -C "$CURRENT_LINK" rev-parse --verify HEAD 2>/dev/null)" == "$TARGET_SHA" ]] \
  || rollback_and_fail "active release не соответствует exact CI SHA"
/usr/bin/systemctl is-active --quiet second-brain-web.service \
  || rollback_and_fail "systemd service не active после activation"
wait_local_health || rollback_and_fail "local /healthz не восстановился"
wait_public_health || rollback_and_fail "public /healthz не прошёл через Cloudflare/Caddy"

printf 'Production deploy успешен: %s\n' "$TARGET_SHA"
