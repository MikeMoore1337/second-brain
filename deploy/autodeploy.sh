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
  local phase="$1"

  run_target_script deploy/candidate-recovery-check.sh \
    --control-repository "$APP_ROOT" \
    --releases-root "$RELEASES_ROOT" \
    --current-link "$CURRENT_LINK" \
    --target-sha "$TARGET_SHA" \
    --expected-main-sha "$ORIGIN_APP_SHA" \
    --phase "$phase"
}

run_systemd_contract_check() {
  run_target_script deploy/systemd-contract-check.sh \
    --repository "$APP_ROOT" \
    --sha "$TARGET_SHA" \
    --installed-unit "$SYSTEMD_UNIT_PATH" \
    --drop-in-directory "$SYSTEMD_DROPIN_DIRECTORY"
}

run_runtime_entrypoint_check() {
  local candidate_path="$1"
  local release_sha="$2"

  run_target_script deploy/runtime-entrypoint-check.sh \
    --releases-root "$RELEASES_ROOT" \
    --release-sha "$release_sha" \
    --candidate "$candidate_path"
}

GIT_STATUS_DIAGNOSTIC_MAX_BYTES=512

sanitize_git_status_diagnostic() {
  local diagnostic="$1"

  # Git status output is metadata only, but normalize control characters before
  # writing it to a CI log. The byte limit is applied to the captured value
  # before this formatting so untrusted repository names cannot expand output.
  diagnostic="${diagnostic//$'\r'/ }"
  diagnostic="${diagnostic//$'\n'/ | }"
  diagnostic="${diagnostic//$'\t'/ }"
  diagnostic="${diagnostic//$'\e'/ }"
  printf '%s' "${diagnostic:0:GIT_STATUS_DIAGNOSTIC_MAX_BYTES}"
}

read_bounded_git_status_file() {
  local output_file="$1"
  local output

  output="$(head -c "$GIT_STATUS_DIAGNOSTIC_MAX_BYTES" "$output_file" 2>/dev/null)" \
    || return 1
  sanitize_git_status_diagnostic "$output"
}

read_bounded_git_trace_file() {
  local trace_file="$1"
  local trace

  # Trace2 writes terminal error/exit events last, so keep the bounded tail
  # rather than the initial argv-only events.
  trace="$(tail -c "$GIT_STATUS_DIAGNOSTIC_MAX_BYTES" "$trace_file" 2>/dev/null)" \
    || return 1
  sanitize_git_status_diagnostic "$trace"
}

run_git_status_probe() {
  local probe_name="$1"
  shift
  local output_file stderr_file output stderr probe_exit

  output_file="$(mktemp)" || return 1
  stderr_file="$(mktemp)" \
    || {
      rm -f -- "$output_file" || true
      return 1
    }
  if "$@" >"$output_file" 2>"$stderr_file"; then
    probe_exit=0
  else
    probe_exit=$?
  fi
  output="$(read_bounded_git_status_file "$output_file")" \
    || output="<capture-failed>"
  stderr="$(read_bounded_git_status_file "$stderr_file")" \
    || stderr="<capture-failed>"
  rm -f -- "$output_file" "$stderr_file" \
    || return 1
  [[ -n "$output" ]] || output="<empty>"
  [[ -n "$stderr" ]] || stderr="<empty>"
  printf 'probe_%s_exit=%s;probe_%s_stderr=%s;probe_%s_stdout=%s' \
    "$probe_name" "$probe_exit" "$probe_name" "$stderr" \
    "$probe_name" "$output"
}

assert_clean_main() {
  local path="$1"
  local label="$2"
  local branch status status_stderr status_trace status_exit
  local fallback_status fallback_stderr fallback_trace fallback_exit
  local probe_optional_locks probe_fsmonitor probe_untracked_cache
  local status_file status_stderr_file status_trace_file
  local fallback_status_file fallback_stderr_file fallback_trace_file
  local marker marker_path

  branch="$(git -C "$path" symbolic-ref --quiet --short HEAD 2>/dev/null)" \
    || die "$label находится в detached HEAD"
  [[ "$branch" == "main" ]] || die "$label должен находиться на branch main"

  # Keep Git output outside shell variables until it has been byte-bounded. The
  # diagnostic files are ephemeral and never point into either production
  # checkout; they are removed on both the success and failure paths below.
  status_file="$(mktemp)" \
    || die "не удалось подготовить bounded git status output $label"
  status_stderr_file="$(mktemp)" \
    || die "не удалось подготовить bounded git status diagnostic $label"
  status_trace_file="$(mktemp)" \
    || die "не удалось подготовить bounded git status trace $label"
  if GIT_OPTIONAL_LOCKS=0 GIT_TRACE2_CONFIG_PARAMS= GIT_TRACE2_ENV_VARS= \
    GIT_TRACE2_EVENT="$status_trace_file" \
    git -C "$path" status --porcelain=v1 --untracked-files=all \
      >"$status_file" 2>"$status_stderr_file"; then
    status_exit=0
  else
    status_exit=$?
    status="$(read_bounded_git_status_file "$status_file")" \
      || {
        rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
          || true
        die "$label git status failed; exit_code=$status_exit; stderr capture failed"
      }
    status_stderr="$(read_bounded_git_status_file "$status_stderr_file")" \
      || {
        rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
          || true
        die "$label git status failed; exit_code=$status_exit; stderr capture failed"
      }
    status_trace="$(read_bounded_git_trace_file "$status_trace_file")" \
      || {
        rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
          || true
        die "$label git status failed; exit_code=$status_exit; trace capture failed"
      }

    probe_optional_locks="$(GIT_OPTIONAL_LOCKS=0 run_git_status_probe \
      optional_locks git -C "$path" status --porcelain=v1 --untracked-files=all)" \
      || probe_optional_locks="<probe-capture-failed>"
    probe_fsmonitor="$(run_git_status_probe fsmonitor git -C "$path" \
      -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)" \
      || probe_fsmonitor="<probe-capture-failed>"
    probe_untracked_cache="$(run_git_status_probe untracked_cache git -C "$path" \
      -c core.untrackedCache=false status --porcelain=v1 --untracked-files=all)" \
      || probe_untracked_cache="<probe-capture-failed>"

    # This second invocation is diagnostic-only. It disables Git's optional
    # index locks and fsmonitor/untracked-cache integrations to distinguish a
    # local Git integration failure from an unreadable repository state. It
    # never turns a failed primary check into a clean result.
    fallback_status_file="$(mktemp)" \
      || {
        rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
          || true
        die "$label git status fallback output setup failed"
      }
    fallback_stderr_file="$(mktemp)" \
      || {
        rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
          "$fallback_status_file" || true
        die "$label git status fallback stderr setup failed"
      }
    fallback_trace_file="$(mktemp)" \
      || {
        rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
          "$fallback_status_file" "$fallback_stderr_file" || true
        die "$label git status fallback trace setup failed"
      }
    if GIT_OPTIONAL_LOCKS=0 GIT_TRACE2_CONFIG_PARAMS= GIT_TRACE2_ENV_VARS= \
      GIT_TRACE2_EVENT="$fallback_trace_file" \
      git -C "$path" -c core.fsmonitor=false -c core.untrackedCache=false \
        status --porcelain=v1 --untracked-files=all \
        >"$fallback_status_file" 2>"$fallback_stderr_file"; then
      fallback_exit=0
    else
      fallback_exit=$?
    fi
    fallback_status="$(read_bounded_git_status_file "$fallback_status_file")" \
      || fallback_status="<capture-failed>"
    fallback_stderr="$(read_bounded_git_status_file "$fallback_stderr_file")" \
      || fallback_stderr="<capture-failed>"
    fallback_trace="$(read_bounded_git_trace_file "$fallback_trace_file")" \
      || fallback_trace="<capture-failed>"

    rm -f -- "$status_stderr_file" \
      "$status_file" "$status_trace_file" "$fallback_status_file" \
      "$fallback_stderr_file" "$fallback_trace_file" \
      || die "не удалось удалить temporary git status diagnostics $label"
    [[ -n "$status_stderr" ]] || status_stderr="<empty>"
    [[ -n "$status" ]] || status="<empty>"
    [[ -n "$status_trace" ]] || status_trace="<empty>"
    [[ -n "$fallback_stderr" ]] || fallback_stderr="<empty>"
    [[ -n "$fallback_status" ]] || fallback_status="<empty>"
    [[ -n "$fallback_trace" ]] || fallback_trace="<empty>"
    die "$label git status failed; exit_code=$status_exit; bounded_stderr=$status_stderr; bounded_stdout=$status; trace2=$status_trace; $probe_optional_locks; $probe_fsmonitor; $probe_untracked_cache; fallback_exit=$fallback_exit; fallback_stderr=$fallback_stderr; fallback_stdout=$fallback_status; fallback_trace2=$fallback_trace"
  fi

  status="$(read_bounded_git_status_file "$status_file")" \
    || {
      rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
        || true
      die "$label git status output capture failed; exit_code=$status_exit"
    }
  status_stderr="$(read_bounded_git_status_file "$status_stderr_file")" \
    || {
      rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
      || true
      die "$label git status diagnostic capture failed; exit_code=$status_exit"
    }
  rm -f -- "$status_file" "$status_stderr_file" "$status_trace_file" \
    || die "не удалось удалить temporary git status diagnostics $label"
  [[ -z "$status_stderr" ]] \
    || die "$label git status returned stderr; exit_code=$status_exit; bounded_stderr=$status_stderr"

  if [[ -n "$status" ]]; then
    die "$label dirty; автоматическая очистка запрещена; bounded_status=$status"
  fi

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

reset_candidate_python_environment() {
  local venv_root="$CANDIDATE_RELEASE/.venv"
  local candidate_real releases_real venv_real

  [[ "$CANDIDATE_RELEASE" == "$RELEASES_ROOT/$TARGET_SHA" ]] \
    || die "candidate Python environment path is not exact releases/<SHA>/.venv"
  [[ -d "$CANDIDATE_RELEASE" && ! -L "$CANDIDATE_RELEASE" ]] \
    || die "candidate Python environment parent is unavailable or is a symlink"
  [[ ! -L "$venv_root" ]] \
    || die "candidate Python environment root is a symlink"
  [[ ! -e "$venv_root" || -d "$venv_root" ]] \
    || die "candidate Python environment root is not a directory"

  candidate_real="$(readlink -f -- "$CANDIDATE_RELEASE")" \
    || die "candidate path cannot be resolved before Python environment reset"
  releases_real="$(readlink -f -- "$RELEASES_ROOT")" \
    || die "releases path cannot be resolved before Python environment reset"
  [[ "$candidate_real" == "$releases_real/$TARGET_SHA" ]] \
    || die "candidate path escaped the exact releases/<SHA> directory"
  if [[ -e "$venv_root" ]]; then
    venv_real="$(readlink -f -- "$venv_root")" \
      || die "candidate Python environment path cannot be resolved"
    [[ "$venv_real" == "$candidate_real/.venv" ]] \
      || die "candidate Python environment path escaped the candidate"
  fi

  uv venv --no-project --clear --python 3.14 "$venv_root" \
    || die "deterministic Python environment reset failed"
  [[ -d "$venv_root" && ! -L "$venv_root" ]] \
    || die "deterministic Python environment reset produced an unsafe root"
  [[ "$(readlink -f -- "$venv_root")" == "$candidate_real/.venv" ]] \
    || die "deterministic Python environment reset escaped the candidate"
}

assert_candidate_frontend_roots() {
  local web_root="$CANDIDATE_RELEASE/web"
  local generated_root

  [[ -d "$web_root" && ! -L "$web_root" ]] \
    || die "candidate web root is unavailable or is a symlink"
  for generated_root in node_modules dist; do
    if [[ -L "$web_root/$generated_root" ]]; then
      die "candidate frontend generated root is a symlink"
    fi
    if [[ -e "$web_root/$generated_root" && ! -d "$web_root/$generated_root" ]]; then
      die "candidate frontend generated root is not a directory"
    fi
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

for command in bash git uv npm curl readlink flock seq cmp find stat mktemp head tail rm; do
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
SYSTEMD_UNIT_PATH=/etc/systemd/system/second-brain-web.service
SYSTEMD_DROPIN_DIRECTORY=/etc/systemd/system/second-brain-web.service.d

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
if ! git -C "$APP_ROOT" diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- deploy/caddy deploy/root; then
  # deploy/systemd deploy/caddy deploy/root остаются root-managed contract paths;
  # только systemd имеет отдельный доказуемый installed-state gate.
  die "root-managed deployment contract (deploy/caddy or deploy/root) изменился; требуется owner-managed integration до autodeploy"
fi
if ! git -C "$APP_ROOT" diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- deploy/systemd; then
  run_systemd_contract_check \
    || die "SYSTEMD_CONTRACT_NOT_INTEGRATED / HUMAN_REQUIRED: exact target systemd unit не установлен owner/root"
fi

run_runtime_entrypoint_check "$RELEASES_ROOT/$PREVIOUS_SHA" "$PREVIOUS_SHA" \
  || die "active known-good release runtime entrypoint gate failed; candidate creation запрещена"

/usr/bin/systemctl is-active --quiet second-brain-web.service \
  || die "текущий production service не active; auto-recovery через новый release запрещён"
wait_local_health || die "baseline local health текущего release не проходит"
wait_public_health || die "baseline public health текущего release не проходит"

CANDIDATE_RELEASE="$RELEASES_ROOT/$TARGET_SHA"
if [[ -e "$CANDIDATE_RELEASE" || -L "$CANDIDATE_RELEASE" ]]; then
  run_candidate_recovery_check \
    recovery-pre-build \
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
  reset_candidate_python_environment
  uv sync --locked --python 3.14
  run_runtime_entrypoint_check "$CANDIDATE_RELEASE" "$TARGET_SHA" \
    || die "candidate runtime entrypoint gate failed; activation запрещена"
  uv run --python 3.14 --no-sync python -m compileall \
    -q -f --invalidation-mode checked-hash src
  CANDIDATE_ENTRYPOINT="$CANDIDATE_RELEASE/.venv/bin/second-brain"
  "$CANDIDATE_ENTRYPOINT" --env-file "$RUNTIME_ENV" doctor
  "$CANDIDATE_ENTRYPOINT" --env-file "$RUNTIME_ENV" vault validate
)

(
  assert_candidate_frontend_roots
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
  final-post-build \
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
