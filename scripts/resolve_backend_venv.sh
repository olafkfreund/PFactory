# shellcheck shell=sh
# Resolve the backend venv for .husky/pre-commit (#796).
#
# Sourced, not executed: sets BACKEND_VENV (absolute, or empty) and MAIN_TREE,
# and returns non-zero when no venv is findable. It must never `exit` — that
# would abort the hook that sourced it.
#
# A git worktree has no apps/backend/.venv of its own: the venv is gitignored
# and lives only in the checkout that created it. Before this, the hook's
# pytest section fell through to a bare `python` with no pytest and blocked the
# commit with "Python tests failed", naming the wrong cause.

# dirname of --git-common-dir is the main working tree, with no git-version
# floor: git prints ".git" (relative) in the main checkout, whose dirname is
# "." (the repo root, which is the hook's cwd), and an absolute path in a
# worktree.
MAIN_TREE="$(cd "$(dirname "$(git rev-parse --git-common-dir 2>/dev/null || echo .git)")" 2>/dev/null && pwd)"
: "${MAIN_TREE:=$(pwd)}"

BACKEND_VENV=""
for _cand in "$(pwd)/apps/backend/.venv" "$MAIN_TREE/apps/backend/.venv"; do
  if [ -d "$_cand" ]; then
    BACKEND_VENV="$_cand"
    break
  fi
done
unset _cand

if [ -n "$BACKEND_VENV" ] && [ "$BACKEND_VENV" != "$(pwd)/apps/backend/.venv" ]; then
  echo "  Using the main checkout's venv: $BACKEND_VENV"
  echo "  (this worktree has none; dependencies come from there, not from this branch)"
fi

[ -n "$BACKEND_VENV" ]
