#!/usr/bin/env bash
#
# WorktreeRemove hook — cleans up a worktree created by worktree-create.sh.
#
# Worktrees live at ~/Work/worktrees/<name>/, so a plain
# `git worktree remove` (which knows about them via the repo's worktree
# admin dir) is sufficient. We also delete the associated branch to match
# Claude Code's default removal behaviour.
#
# Stdin (JSON):  contains "cwd" — the worktree being removed (per the docs'
#                common-input fields). The published example also shows a
#                positional arg, so we accept that as a fallback.
# Output:        ignored. Exit code is logged in debug mode but never blocks.

set -euo pipefail

INPUT=$(cat || true)

WORKTREE_PATH=""
if [ -n "$INPUT" ]; then
    WORKTREE_PATH=$(python3 -c '
import sys, json
try:
    print(json.loads(sys.stdin.read()).get("cwd", ""))
except Exception:
    print("")
' <<<"$INPUT")
fi

if [ -z "$WORKTREE_PATH" ] && [ $# -gt 0 ]; then
    WORKTREE_PATH="$1"
fi

if [ -z "$WORKTREE_PATH" ]; then
    echo "worktree-remove: no worktree path supplied" >&2
    exit 1
fi

# Guardrails — refuse to act on anything that isn't a worktree at the expected
# location. Either of these alone would have prevented the 2026-06-11 incident,
# where the platform passed the project dir as cwd and the unguarded fallback
# `rm -rf "$WORKTREE_PATH"` destroyed the main repo.
WORKTREES_ROOT="${HOME}/Work/worktrees"

RESOLVED_WT=$(cd "$WORKTREE_PATH" 2>/dev/null && pwd -P) || RESOLVED_WT=""
RESOLVED_ROOT=$(cd "$WORKTREES_ROOT" 2>/dev/null && pwd -P) || RESOLVED_ROOT=""

if [ -z "$RESOLVED_WT" ] || [ -z "$RESOLVED_ROOT" ]; then
    echo "worktree-remove: cannot resolve path; refusing to act ($WORKTREE_PATH)" >&2
    exit 1
fi

# (a) Direct child of the worktrees root only — not the root itself, not nested deeper.
if [ "$(dirname "$RESOLVED_WT")" != "$RESOLVED_ROOT" ]; then
    echo "worktree-remove: refusing — '$RESOLVED_WT' is not directly under '$RESOLVED_ROOT'" >&2
    exit 1
fi

# (b) Must have a .git pointer FILE (worktree marker), not a .git directory (full repo).
if [ ! -f "${RESOLVED_WT}/.git" ]; then
    echo "worktree-remove: refusing — no .git pointer file at '$RESOLVED_WT' (not a worktree)" >&2
    exit 1
fi

WORKTREE_PATH="$RESOLVED_WT"

# Resolve the source repo via the worktree's .git pointer file.
# Format:  gitdir: /abs/path/to/source/.git/worktrees/<name>
GITDIR=$(sed -n 's/^gitdir: //p' "${WORKTREE_PATH}/.git")
SOURCE_REPO="${GITDIR%/.git/worktrees/*}"

if [ -z "$SOURCE_REPO" ] || [ ! -d "$SOURCE_REPO" ]; then
    echo "worktree-remove: refusing — could not resolve source repo from '$WORKTREE_PATH/.git'" >&2
    exit 1
fi

# Capture the branch name before removal — `git worktree remove` doesn't
# delete the branch on its own.
BRANCH=$(git -C "$WORKTREE_PATH" symbolic-ref --short HEAD 2>/dev/null || true)

git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE_PATH" >&2 \
    || rm -rf "$WORKTREE_PATH"
if [ -n "$BRANCH" ]; then
    # `branch -D` refuses if the branch is checked out in another
    # worktree, which is exactly the safety we want.
    git -C "$SOURCE_REPO" branch -D "$BRANCH" >&2 2>/dev/null || true
fi
