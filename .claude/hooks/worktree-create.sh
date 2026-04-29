#!/usr/bin/env bash
#
# WorktreeCreate hook — replaces Claude Code's default worktree creation logic.
#
# Behaviour:
#   * Creates the new worktree at  ~/Work/worktrees/<name>/  (sibling to the
#     repo, NOT inside it), so repo-wide tools (rg, find, bazel, git status)
#     don't traverse into worktree contents.
#   * Bases the new branch on origin/HEAD (matches Claude Code's default).
#   * Reads .worktreeinclude from the source repo and copies any file that is
#     both matched by a pattern AND gitignored. (Configured WorktreeCreate
#     hooks bypass Claude Code's built-in .worktreeinclude processing, so we
#     re-implement it here.)
#
# Stdin (JSON):  fields documented in https://code.claude.com/docs/en/hooks
#                — at minimum we need "cwd" (the source repo). Some
#                additional field carries the user-supplied name; we probe
#                common locations and fall back to a timestamped name.
# Stdout:        absolute path of the created worktree (Claude Code reads it).
# Exit:          0 on success; non-zero aborts worktree creation.

set -euo pipefail

INPUT=$(cat)

# Source repo = cwd of the session that triggered the hook.
SOURCE_REPO=$(python3 -c '
import sys, json
print(json.loads(sys.stdin.read()).get("cwd", ""))
' <<<"$INPUT")

if [ -z "$SOURCE_REPO" ] || [ ! -d "$SOURCE_REPO" ]; then
    echo "worktree-create: could not determine source repo from hook input" >&2
    exit 1
fi

# Probe for a user-supplied name. The docs don't pin down the field name, so
# we look in several plausible locations and fall back to a timestamp if none
# match. Update the key list once empirical observation tells us where it is.
NAME=$(python3 -c '
import sys, json
d = json.loads(sys.stdin.read())
candidate_keys = ("worktree_name", "worktreeName", "name")
def find(obj):
    if not isinstance(obj, dict):
        return None
    for k in candidate_keys:
        v = obj.get(k)
        if isinstance(v, str) and v:
            return v
    for sub in ("hookSpecificInput", "hook_specific_input",
                "tool_input", "toolInput"):
        v = find(obj.get(sub))
        if v:
            return v
    return None
print(find(d) or "")
' <<<"$INPUT")

if [ -z "$NAME" ]; then
    NAME="auto-$(date +%Y%m%d-%H%M%S)-$$"
fi

WORKTREE_BASE="${HOME}/Work/worktrees"
WORKTREE_PATH="${WORKTREE_BASE}/${NAME}"
BRANCH="worktree-${NAME}"

mkdir -p "$WORKTREE_BASE"

# Refuse if the target already exists — surfaces collisions instead of
# silently reusing a stale directory.
if [ -e "$WORKTREE_PATH" ]; then
    echo "worktree-create: $WORKTREE_PATH already exists" >&2
    exit 1
fi

# Stderr only — stdout is reserved for the path we hand back to Claude Code.
git -C "$SOURCE_REPO" worktree add -b "$BRANCH" "$WORKTREE_PATH" origin/HEAD >&2

# Copy gitignored files matching .worktreeinclude patterns.
WTI="${SOURCE_REPO}/.worktreeinclude"
if [ -f "$WTI" ]; then
    # `ls-files --others -i --exclude-from=<file>` lists untracked files
    # matching the file's patterns. We then verify each is also gitignored
    # by the standard rules (so non-ignored files matched only by
    # .worktreeinclude don't accidentally get copied).
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        if git -C "$SOURCE_REPO" check-ignore -q "$f"; then
            dest="${WORKTREE_PATH}/${f}"
            mkdir -p "$(dirname "$dest")"
            cp -p "${SOURCE_REPO}/${f}" "$dest"
        fi
    done < <(git -C "$SOURCE_REPO" ls-files --others -i --exclude-from="$WTI")
fi

# Hand the worktree path back to Claude Code.
echo "$WORKTREE_PATH"
