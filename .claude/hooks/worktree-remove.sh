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

# Resolve the source repo via the worktree's .git pointer file.
# Format:  gitdir: /abs/path/to/source/.git/worktrees/<name>
SOURCE_REPO=""
if [ -f "${WORKTREE_PATH}/.git" ]; then
    GITDIR=$(sed -n 's/^gitdir: //p' "${WORKTREE_PATH}/.git")
    SOURCE_REPO="${GITDIR%/.git/worktrees/*}"
fi

# Capture the branch name before removal — `git worktree remove` doesn't
# delete the branch on its own.
BRANCH=""
if [ -d "$WORKTREE_PATH" ]; then
    BRANCH=$(git -C "$WORKTREE_PATH" symbolic-ref --short HEAD 2>/dev/null || true)
fi

if [ -n "$SOURCE_REPO" ] && [ -d "$SOURCE_REPO" ]; then
    git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE_PATH" >&2 \
        || rm -rf "$WORKTREE_PATH"
    if [ -n "$BRANCH" ]; then
        # `branch -D` refuses if the branch is checked out in another
        # worktree, which is exactly the safety we want.
        git -C "$SOURCE_REPO" branch -D "$BRANCH" >&2 2>/dev/null || true
    fi
else
    rm -rf "$WORKTREE_PATH"
fi
