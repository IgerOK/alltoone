#!/usr/bin/env sh
# Remove what the installer placed, and the package itself.
#
#   ./scripts/uninstall.sh                          # package only
#   ./scripts/uninstall.sh --target ~/work/my-project
#   ./scripts/uninstall.sh --target . --purge        # also drop .opencode/handoff
#   ./scripts/uninstall.sh --target . --force        # remove even changed files
#
# A file that differs from the shipped copy is kept: it may be the user's own
# agreement or config, and deleting it silently would be data loss.
set -eu

python_bin="python3"
target=""
purge=0
keep_package=0
force=0

while [ $# -gt 0 ]; do
  case "$1" in
    --target) target="$2"; shift 2 ;;
    --python) python_bin="$2"; shift 2 ;;
    --purge) purge=1; shift ;;
    --keep-package) keep_package=1; shift ;;
    --force) force=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done

repo=$(cd "$(dirname "$0")/.." && pwd)

# Kept identical to install.sh; a test keeps the two lists in sync.
for rel in ".opencode/plugins/context-budget" \
           ".opencode/commands/handoff.md" \
           ".opencode/commands/resume.md" \
           ".opencode/skills/skill-template.md" \
           "AGENTS.md" \
           "opencode.jsonc"; do

  if [ -z "$target" ]; then
    continue
  fi

  to="$target/$rel"
  from="$repo/$rel"
  if [ ! -e "$to" ]; then
    echo "absent  $rel"
    continue
  fi
  same=0
  if [ -d "$from" ]; then
    if [ -d "$to" ] && diff -r "$from" "$to" >/dev/null 2>&1; then same=1; fi
  elif [ -f "$to" ] && cmp -s "$from" "$to"; then
    same=1
  fi
  if [ "$force" -eq 0 ] && [ "$same" -eq 0 ]; then
    echo "kept    $rel (changed since install; use --force)"
    continue
  fi
  rm -rf "$to"
  echo "removed $rel"
done

if [ -z "$target" ]; then
  if [ "$keep_package" -eq 0 ]; then
    "$python_bin" -m pip uninstall --quiet -y alltoone
    echo "removed package alltoone"
  fi
  echo
  echo "Nothing else was touched. To clean a project as well:"
  echo "  ./scripts/uninstall.sh --target <dir> [--purge]"
  exit 0
fi

target_path=$(cd "$target" && pwd)
if [ "$target_path" = "$repo" ]; then
  echo "error: target is the repository itself" >&2
  exit 1
fi

# Personal skills stay: they were written for this project, not installed.
if [ -d "$target_path/.opencode/skills" ] && [ -z "$(ls -A "$target_path/.opencode/skills")" ]; then
  rmdir "$target_path/.opencode/skills"
fi

if [ "$purge" -eq 1 ] && [ -d "$target_path/.opencode/handoff" ]; then
  rm -rf "$target_path/.opencode/handoff"
  echo "removed .opencode/handoff (state and pack)"
fi

# Only empty leftovers of ours: a project may keep other .opencode content.
for dir in ".opencode/commands" ".opencode/plugins" ".opencode"; do
  if [ -d "$target_path/$dir" ] && [ -z "$(ls -A "$target_path/$dir")" ]; then
    rmdir "$target_path/$dir"
  fi
done

if [ "$keep_package" -eq 0 ]; then
  "$python_bin" -m pip uninstall --quiet -y alltoone
  echo "removed package alltoone"
fi

echo
echo "Done. Project files were not touched."
if [ "$purge" -eq 0 ]; then
  echo "The handoff state is still in $target_path/.opencode/handoff (add --purge to drop it)."
fi
