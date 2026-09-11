#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  cat <<'USAGE'
Использование:
  deploy/candidate-recovery-check.sh \
    --control-repository PATH \
    --releases-root PATH \
    --current-link PATH \
    --target-sha APPLICATION_SHA \
    --expected-main-sha APPLICATION_SHA

Классифицирует только уже существующий exact-SHA candidate. Скрипт read-only:
он не удаляет, не чистит и не меняет Git worktree.
USAGE
}

die() {
  printf 'Ошибка candidate recovery: STOP / HUMAN_REQUIRED: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "команда '$1' не найдена в PATH"
}

canonical_path() {
  readlink -f -- "$1" 2>/dev/null
}

assert_no_git_operation_state() {
  local path="$1"
  local marker marker_path

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
      || die "не удалось проверить Git operation state"
    if [[ "$marker_path" != /* ]]; then
      marker_path="$path/$marker_path"
    fi
    [[ ! -e "$marker_path" && ! -L "$marker_path" ]] \
      || die "candidate содержит незавершённое Git operation state"
  done
}

is_sensitive_ignored_path() {
  local path="$1"

  case "$path" in
    .env|.env.*|*.env|*/.env|*/.env.*|*.pem|*.key|*.p12|*.pfx|*.crt|*.cer|*.der)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_allowed_generated_path() {
  local path="$1"

  is_sensitive_ignored_path "$path" && return 1
  case "$path" in
    .venv/*|web/node_modules/*|web/dist/*)
      return 0
      ;;
    src/*/__pycache__/*.cpython-314.pyc)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

assert_no_unexpected_ignored_state() {
  local path="$1"
  local ignored_paths ignored_path

  ignored_paths="$(git -C "$path" ls-files --others --ignored --exclude-standard 2>/dev/null)" \
    || die "candidate ignored state cannot be enumerated"
  while IFS= read -r ignored_path || [[ -n "$ignored_path" ]]; do
    [[ -n "$ignored_path" ]] || continue
    is_allowed_generated_path "$ignored_path" \
      || die "candidate contains ignored state outside the explicit generated-state allowlist"
  done <<< "$ignored_paths"
}

CONTROL_REPOSITORY=""
RELEASES_ROOT=""
CURRENT_LINK=""
TARGET_SHA=""
EXPECTED_MAIN_SHA=""

while (( $# > 0 )); do
  case "$1" in
    --control-repository)
      (( $# >= 2 )) || die "для --control-repository нужен PATH"
      CONTROL_REPOSITORY="$2"
      shift 2
      ;;
    --releases-root)
      (( $# >= 2 )) || die "для --releases-root нужен PATH"
      RELEASES_ROOT="$2"
      shift 2
      ;;
    --current-link)
      (( $# >= 2 )) || die "для --current-link нужен PATH"
      CURRENT_LINK="$2"
      shift 2
      ;;
    --target-sha)
      (( $# >= 2 )) || die "для --target-sha нужен APPLICATION_SHA"
      TARGET_SHA="$2"
      shift 2
      ;;
    --expected-main-sha)
      (( $# >= 2 )) || die "для --expected-main-sha нужен APPLICATION_SHA"
      EXPECTED_MAIN_SHA="$2"
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

for command in git readlink; do
  require_command "$command"
done
[[ "$CONTROL_REPOSITORY" == /* && "$RELEASES_ROOT" == /* && "$CURRENT_LINK" == /* ]] \
  || die "all paths must be absolute"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ && "$EXPECTED_MAIN_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || die "target and expected main SHA must be exact lowercase Git SHAs"
[[ "$TARGET_SHA" == "$EXPECTED_MAIN_SHA" ]] \
  || die "target SHA is not the expected tested/current-main SHA"

[[ -d "$CONTROL_REPOSITORY" && ! -L "$CONTROL_REPOSITORY" ]] \
  || die "control repository is unavailable or is a symlink"
[[ -d "$RELEASES_ROOT" && ! -L "$RELEASES_ROOT" ]] \
  || die "releases root is unavailable or is a symlink"
[[ -L "$CURRENT_LINK" ]] || die "current must remain a symlink"

CONTROL_REAL="$(canonical_path "$CONTROL_REPOSITORY")" \
  || die "control repository path cannot be resolved"
RELEASES_REAL="$(canonical_path "$RELEASES_ROOT")" \
  || die "releases root path cannot be resolved"
CONTROL_TOP="$(git -C "$CONTROL_REPOSITORY" rev-parse --show-toplevel 2>/dev/null)" \
  || die "control repository top-level path cannot be read"
[[ "$(canonical_path "$CONTROL_TOP")" == "$CONTROL_REAL" ]] \
  || die "control repository top-level path does not match expected path"
CANDIDATE="$RELEASES_ROOT/$TARGET_SHA"
CANDIDATE_REAL_EXPECTED="$RELEASES_REAL/$TARGET_SHA"

[[ -d "$CANDIDATE" && ! -L "$CANDIDATE" ]] \
  || die "candidate path is absent, not a directory, or is a symlink"
[[ "$(canonical_path "$CANDIDATE")" == "$CANDIDATE_REAL_EXPECTED" ]] \
  || die "candidate path is not the exact expected releases/<SHA> path"
[[ "$(readlink "$CURRENT_LINK" 2>/dev/null)" != "releases/$TARGET_SHA" ]] \
  || die "candidate is already active current; caller must use active no-op semantics"
CURRENT_TARGET="$(readlink "$CURRENT_LINK" 2>/dev/null)" \
  || die "current target cannot be read"
[[ "$CURRENT_TARGET" =~ ^releases/[0-9a-f]{40}$ ]] \
  || die "current target is not a known release path"

[[ "$(git -C "$CONTROL_REPOSITORY" rev-parse --is-inside-work-tree 2>/dev/null)" == "true" ]] \
  || die "control repository is not a worktree"
[[ "$(git -C "$CONTROL_REPOSITORY" rev-parse --is-bare-repository 2>/dev/null)" == "false" ]] \
  || die "control repository must not be bare"
[[ -z "$(git -C "$CONTROL_REPOSITORY" rev-parse --show-superproject-working-tree 2>/dev/null)" ]] \
  || die "control repository must not be a submodule"
CONTROL_BRANCH="$(git -C "$CONTROL_REPOSITORY" symbolic-ref --quiet --short HEAD 2>/dev/null)" \
  || die "control repository must remain on branch main"
[[ "$CONTROL_BRANCH" == "main" ]] || die "control repository must remain on branch main"
[[ "$(git -C "$CONTROL_REPOSITORY" rev-parse --verify HEAD 2>/dev/null)" == "$EXPECTED_MAIN_SHA" ]] \
  || die "control repository HEAD is not the expected tested/current-main SHA"
CONTROL_ORIGIN="$(git -C "$CONTROL_REPOSITORY" remote get-url origin 2>/dev/null)" \
  || die "control repository origin is unavailable"

CANDIDATE_TOP="$(git -C "$CANDIDATE" rev-parse --show-toplevel 2>/dev/null)" \
  || die "candidate is not a Git worktree"
[[ "$(canonical_path "$CANDIDATE_TOP")" == "$CANDIDATE_REAL_EXPECTED" ]] \
  || die "candidate top-level path does not match expected path"
[[ "$(git -C "$CANDIDATE" rev-parse --is-inside-work-tree 2>/dev/null)" == "true" ]] \
  || die "candidate is not inside a worktree"
[[ "$(git -C "$CANDIDATE" rev-parse --is-bare-repository 2>/dev/null)" == "false" ]] \
  || die "candidate must not be bare"
[[ -z "$(git -C "$CANDIDATE" rev-parse --show-superproject-working-tree 2>/dev/null)" ]] \
  || die "candidate must not be a submodule"
[[ -f "$CANDIDATE/.git" && ! -L "$CANDIDATE/.git" ]] \
  || die "candidate Git worktree metadata is not a regular linked-worktree file"

CANDIDATE_ORIGIN="$(git -C "$CANDIDATE" remote get-url origin 2>/dev/null)" \
  || die "candidate origin is unavailable"
[[ "$CONTROL_ORIGIN" == "$CANDIDATE_ORIGIN" ]] \
  || die "candidate repository origin does not match control repository"
CONTROL_COMMON="$(cd "$CONTROL_REPOSITORY" && readlink -f -- "$(git rev-parse --git-common-dir 2>/dev/null)")" \
  || die "control repository identity cannot be resolved"
CANDIDATE_COMMON="$(cd "$CANDIDATE" && readlink -f -- "$(git rev-parse --git-common-dir 2>/dev/null)")" \
  || die "candidate repository identity cannot be resolved"
[[ "$CONTROL_COMMON" == "$CANDIDATE_COMMON" ]] \
  || die "candidate is not registered in the control repository identity"

[[ "$(git -C "$CANDIDATE" rev-parse --verify HEAD 2>/dev/null)" == "$TARGET_SHA" ]] \
  || die "candidate HEAD does not equal exact target SHA"
[[ "$(git -C "$CANDIDATE" rev-parse --abbrev-ref HEAD 2>/dev/null)" == "HEAD" ]] \
  || die "candidate is not detached"
if git -C "$CANDIDATE" symbolic-ref --quiet HEAD >/dev/null 2>&1; then
  die "candidate has a symbolic branch HEAD"
fi

CANDIDATE_STATUS="$(git -C "$CANDIDATE" status --porcelain=v1 --untracked-files=all 2>/dev/null)" \
  || die "candidate clean state cannot be checked"
[[ -z "$CANDIDATE_STATUS" ]] \
  || die "candidate tracked or non-ignored files are dirty"
assert_no_unexpected_ignored_state "$CANDIDATE"
assert_no_git_operation_state "$CANDIDATE"

WORKTREE_LIST="$(git -C "$CONTROL_REPOSITORY" worktree list --porcelain 2>/dev/null)" \
  || die "control repository worktree registration cannot be read"

CANDIDATE_REGISTERED=0
BLOCK_PATH=""
BLOCK_BAD=0

finalize_worktree_block() {
  local block_real=""
  local block_is_candidate=0

  [[ -n "$BLOCK_PATH" ]] || return 0
  if [[ "$BLOCK_PATH" == "$CANDIDATE" ]]; then
    block_is_candidate=1
  elif [[ -e "$BLOCK_PATH" || -L "$BLOCK_PATH" ]]; then
    block_real="$(canonical_path "$BLOCK_PATH")" || block_real=""
    [[ "$block_real" == "$CANDIDATE_REAL_EXPECTED" ]] && block_is_candidate=1
  fi

  if (( block_is_candidate == 1 )); then
    (( BLOCK_BAD == 0 )) || die "candidate worktree registration has locked/prunable/unknown state"
    (( CANDIDATE_REGISTERED == 0 )) \
      || die "candidate appears more than once in worktree registration"
    CANDIDATE_REGISTERED=1
  fi
  BLOCK_PATH=""
  BLOCK_BAD=0
}

while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    worktree\ *)
      finalize_worktree_block
      BLOCK_PATH="${line#worktree }"
      [[ -n "$BLOCK_PATH" ]] || die "worktree registration contains an empty path"
      ;;
    '')
      finalize_worktree_block
      ;;
    HEAD\ *|detached)
      ;;
    branch\ *|locked\ *|prunable\ *)
      BLOCK_BAD=1
      ;;
    *)
      BLOCK_BAD=1
      ;;
  esac
done <<< "$WORKTREE_LIST"
finalize_worktree_block
(( CANDIDATE_REGISTERED == 1 )) \
  || die "candidate is not registered as a worktree of the current control repository"

printf 'Candidate %s is recoverable; full validation/build must run again.\n' "$TARGET_SHA"
