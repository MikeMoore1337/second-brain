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

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
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

  for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD REBASE_HEAD rebase-merge rebase-apply BISECT_LOG; do
    marker_path="$(git -C "$path" rev-parse --git-path "$marker" 2>/dev/null)" \
      || die "не удалось проверить Git operation в $label"
    if [[ "$marker_path" != /* ]]; then
      marker_path="$path/$marker_path"
    fi
    [[ ! -e "$marker_path" ]] || die "$label имеет незавершённую Git operation: $marker"
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
  [[ -d "$RELEASES_ROOT/$sha" ]] || die "current указывает на отсутствующий release"
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
  local rollback_link="$SECOND_BRAIN_ROOT/.current.$PREVIOUS_SHA.rollback"

  printf 'Новый release не прошёл post-activation smoke: %s\n' "$reason" >&2
  printf 'Возвращаю current на предыдущий known-good SHA %s.\n' "$PREVIOUS_SHA" >&2

  if [[ -e "$rollback_link" || -L "$rollback_link" ]]; then
    printf 'Rollback остановлен: временный symlink уже существует: %s\n' "$rollback_link" >&2
    exit 1
  fi

  if ! ln -s "releases/$PREVIOUS_SHA" "$rollback_link"; then
    printf 'Rollback не смог создать временный symlink.\n' >&2
    exit 1
  fi
  if ! mv -T -- "$rollback_link" "$CURRENT_LINK"; then
    printf 'Rollback не смог вернуть current symlink.\n' >&2
    exit 1
  fi
  if ! /usr/bin/sudo -n /usr/bin/systemctl restart second-brain-web.service; then
    printf 'Rollback вернул current, но restart предыдущего release завершился ошибкой.\n' >&2
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

for command in git uv npm curl readlink flock seq; do
  require_command "$command"
done
[[ -x /usr/bin/sudo ]] || die "ожидается /usr/bin/sudo для narrowly-scoped service restart"
[[ -x /usr/bin/systemctl ]] || die "ожидается /usr/bin/systemctl"

SECOND_BRAIN_ROOT=/srv/second-brain
APP_ROOT="$SECOND_BRAIN_ROOT/second-brain"
VAULT_ROOT="$SECOND_BRAIN_ROOT/second-brain-vault"
RELEASES_ROOT="$SECOND_BRAIN_ROOT/releases"
CURRENT_LINK="$SECOND_BRAIN_ROOT/current"
RUNTIME_ROOT="$SECOND_BRAIN_ROOT/runtime"
RUNTIME_ENV="$RUNTIME_ROOT/web.env"
LOCK_FILE="$RUNTIME_ROOT/autodeploy.lock"

[[ -d "$APP_ROOT" ]] || die "не найден control checkout second-brain"
[[ -d "$VAULT_ROOT" ]] || die "не найден sibling second-brain-vault"
[[ -d "$RELEASES_ROOT" ]] || die "не найден releases directory"
[[ -d "$RUNTIME_ROOT" ]] || die "не найден runtime directory"
[[ -r "$RUNTIME_ENV" && -f "$RUNTIME_ENV" ]] || die "production web.env недоступен"
[[ -w "$RUNTIME_ROOT" ]] || die "runtime directory недоступен для deploy lock"
[[ -w "$RELEASES_ROOT" ]] || die "releases directory недоступен для candidate"
[[ -w "$SECOND_BRAIN_ROOT" ]] || die "production root недоступен для atomic current switch"

exec 9>"$LOCK_FILE"
flock -n 9 || die "другой production deploy уже выполняется"

assert_clean_main "$APP_ROOT" "second-brain"
assert_clean_main "$VAULT_ROOT" "second-brain-vault"

git -C "$APP_ROOT" fetch --no-tags origin main
git -C "$VAULT_ROOT" fetch --no-tags origin main

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
if ! git -C "$APP_ROOT" diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- deploy/systemd deploy/caddy; then
  die "root-managed systemd/Caddy contract изменился; требуется owner-managed integration до autodeploy"
fi

/usr/bin/systemctl is-active --quiet second-brain-web.service \
  || die "текущий production service не active; auto-recovery через новый release запрещён"
wait_local_health || die "baseline local health текущего release не проходит"
wait_public_health || die "baseline public health текущего release не проходит"

CANDIDATE_RELEASE="$RELEASES_ROOT/$TARGET_SHA"
[[ ! -e "$CANDIDATE_RELEASE" && ! -L "$CANDIDATE_RELEASE" ]] \
  || die "candidate release уже существует; автоматическое переиспользование/очистка запрещены"

git -C "$APP_ROOT" worktree add --detach "$CANDIDATE_RELEASE" "$TARGET_SHA"
[[ "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" == "$TARGET_SHA" ]] \
  || die "candidate worktree не соответствует exact CI SHA"

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
  test -s dist/index.html
)

[[ "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" == "$TARGET_SHA" ]] \
  || die "candidate SHA изменился во время build"
[[ -z "$(git -C "$CANDIDATE_RELEASE" status --porcelain=v1 --untracked-files=no)" ]] \
  || die "tracked files candidate изменились во время build"

NEXT_LINK="$SECOND_BRAIN_ROOT/.current.$TARGET_SHA.next"
[[ ! -e "$NEXT_LINK" && ! -L "$NEXT_LINK" ]] \
  || die "временный activation symlink уже существует"
ln -s "releases/$TARGET_SHA" "$NEXT_LINK"
mv -T -- "$NEXT_LINK" "$CURRENT_LINK"

[[ "$(readlink "$CURRENT_LINK")" == "releases/$TARGET_SHA" ]] \
  || rollback_and_fail "current не указывает на новый release после activation"
[[ "$(git -C "$CURRENT_LINK" rev-parse --verify HEAD 2>/dev/null)" == "$TARGET_SHA" ]] \
  || rollback_and_fail "active release не соответствует exact CI SHA"

/usr/bin/sudo -n /usr/bin/systemctl restart second-brain-web.service \
  || rollback_and_fail "systemd restart завершился ошибкой"
/usr/bin/systemctl is-active --quiet second-brain-web.service \
  || rollback_and_fail "systemd service не active после restart"
wait_local_health || rollback_and_fail "local /healthz не восстановился"
wait_public_health || rollback_and_fail "public /healthz не прошёл через Cloudflare/Caddy"

printf 'Production deploy успешен: %s\n' "$TARGET_SHA"
