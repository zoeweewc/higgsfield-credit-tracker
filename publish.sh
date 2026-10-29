#!/bin/bash
# Push this repo to the team's shared Drive folder, safely.
#
# Overwriting a file inside a Google Drive folder can silently never upload: the local
# file looks right, Drive reports nothing, and the team keeps running the old code with
# no error anywhere. So every file is removed before it is written, and this script
# refuses to report success until Drive has given each one an item id - which is Drive's
# own proof that it took the file.
#
# Usage:  ./publish.sh            push engine/ and Install/, then verify
#         ./publish.sh --check    verify only, change nothing
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$REPO/plugins/higgsfield-credits"
CHECK_ONLY=false
[ "${1:-}" = "--check" ] && CHECK_ONLY=true

# --------------------------------------------------------------- find the shared folder
SHARED=""
shopt -s nullglob
for hit in "$HOME"/Library/CloudStorage/GoogleDrive-*/My\ Drive/Higgsfield\ Credits \
           "$HOME"/Library/CloudStorage/GoogleDrive-*/My\ Drive/*/Higgsfield\ Credits \
           "$HOME"/Library/CloudStorage/GoogleDrive-*/My\ Drive/*/*/Higgsfield\ Credits; do
  if [ -d "$hit" ]; then SHARED="$hit"; break; fi   # shallowest match wins
done
shopt -u nullglob
if [ -z "$SHARED" ]; then
  echo "Can't find the shared 'Higgsfield Credits' folder in Google Drive." >&2
  exit 1
fi
echo "Shared folder: $SHARED"

# ------------------------------------------------------------------------------- publish
# put() removes the destination first, because overwriting is what silently fails.
put() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  rm -f "$dest"
  cp "$src" "$dest"
}

if ! $CHECK_ONLY; then
  echo "Publishing…"
  # the live engine: this is the code every teammate actually runs
  put "$PLUGIN/scripts/_engine/hf_credits.py" "$SHARED/engine/hf_credits.py"
  put "$PLUGIN/scripts/_engine/xlsx.py"       "$SHARED/engine/xlsx.py"
  # the launcher teammates install from
  put "$REPO/.claude-plugin/marketplace.json"             "$SHARED/Install/.claude-plugin/marketplace.json"
  put "$PLUGIN/.claude-plugin/plugin.json"                "$SHARED/Install/plugins/higgsfield-credits/.claude-plugin/plugin.json"
  put "$PLUGIN/hooks/hooks.json"                          "$SHARED/Install/plugins/higgsfield-credits/hooks/hooks.json"
  put "$PLUGIN/scripts/hf_credits.py"                     "$SHARED/Install/plugins/higgsfield-credits/scripts/hf_credits.py"
  put "$PLUGIN/scripts/_engine/hf_credits.py"             "$SHARED/Install/plugins/higgsfield-credits/scripts/_engine/hf_credits.py"
  put "$PLUGIN/scripts/_engine/xlsx.py"                   "$SHARED/Install/plugins/higgsfield-credits/scripts/_engine/xlsx.py"
  put "$PLUGIN/skills/higgsfield-credits/SKILL.md"        "$SHARED/Install/plugins/higgsfield-credits/skills/higgsfield-credits/SKILL.md"
  put "$REPO/README.md"                                   "$SHARED/Install/README.md"
fi

# -------------------------------------------------------------------------------- verify
# Content first: a file that uploaded the wrong bytes is worse than one that didn't upload.
echo "Checking contents…"
bad=0
compare() {
  if ! diff -q "$1" "$2" >/dev/null 2>&1; then
    echo "  DIFFERS  ${2#$SHARED/}"; bad=1
  fi
}
compare "$PLUGIN/scripts/_engine/hf_credits.py" "$SHARED/engine/hf_credits.py"
compare "$PLUGIN/scripts/_engine/xlsx.py"       "$SHARED/engine/xlsx.py"
compare "$REPO/README.md"                       "$SHARED/Install/README.md"
if ! diff -rq "$REPO/plugins" "$SHARED/Install/plugins" >/dev/null 2>&1; then
  echo "  DIFFERS  Install/plugins"; diff -rq "$REPO/plugins" "$SHARED/Install/plugins" | sed 's/^/    /'; bad=1
fi

# Then upload: Drive stamps an item id onto every file it has actually taken.
echo "Checking uploads…"
pending=1
for attempt in $(seq 1 45); do
  pending=0
  while IFS= read -r -d '' f; do
    [ -n "$(xattr -p com.google.drivefs.item-id#S "$f" 2>/dev/null)" ] || pending=1
  done < <(find "$SHARED/engine" "$SHARED/Install" -type f -print0)
  [ "$pending" -eq 0 ] && break
  sleep 2
done
if [ "$pending" -ne 0 ]; then
  echo "NOT UPLOADED - Drive has not taken these yet:"
  while IFS= read -r -d '' f; do
    [ -n "$(xattr -p com.google.drivefs.item-id#S "$f" 2>/dev/null)" ] || echo "  ${f#$SHARED/}"
  done < <(find "$SHARED/engine" "$SHARED/Install" -type f -print0)
  echo "Leave Google Drive running and re-run: ./publish.sh --check" >&2
  exit 1
fi

# The launcher must be installable, or teammates can't get it at all.
if command -v claude >/dev/null 2>&1; then
  echo "Validating marketplace…"
  claude plugin validate "$SHARED/Install" >/dev/null 2>&1 \
    || { echo "  marketplace.json did not validate"; bad=1; }
fi

[ "$bad" -ne 0 ] && { echo "FAILED - fix the above before telling anyone to update." >&2; exit 1; }
echo "OK - every file matches and Drive has taken all of them."
