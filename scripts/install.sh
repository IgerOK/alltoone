#!/usr/bin/env sh
# Install alltoone and optionally set up a project for the handoff workflow.
#
#   ./scripts/install.sh
#   ./scripts/install.sh --target ~/work/my-project
#   ./scripts/install.sh --target . --force      # overwrite existing files
#
# Editable install: `git pull` is enough to update, no reinstall.
set -eu

python_bin="python3"
target=""
force=0

while [ $# -gt 0 ]; do
  case "$1" in
    --target) target="$2"; shift 2 ;;
    --python) python_bin="$2"; shift 2 ;;
    --force) force=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done

repo=$(cd "$(dirname "$0")/.." && pwd)

if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "error: $python_bin not found; install Python 3.10+" >&2
  exit 1
fi
version=$("$python_bin" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if [ "$(printf '%s\n3.10\n' "$version" | sort -V | head -n1)" != "3.10" ]; then
  echo "error: python $version is too old; 3.10+ is required" >&2
  exit 1
fi
echo "python  $version"

"$python_bin" -m pip install --quiet --upgrade -e "$repo"
"$python_bin" -m alltoone --version

if [ -z "$target" ]; then
  echo
  echo "Installed. Next: add the workflow to a project with"
  echo "  ./scripts/install.sh --target <dir>"
  exit 0
fi

target_path=$(cd "$target" && pwd)
if [ "$target_path" = "$repo" ]; then
  echo "error: target is the repository itself" >&2
  exit 1
fi

# `.opencode/handoff/` is never copied: it holds this project's session state.
for rel in ".opencode/plugins/context-budget" \
           ".opencode/commands/handoff.md" \
           ".opencode/commands/resume.md" \
           ".opencode/skills/skill-template.md" \
           "AGENTS.md" \
           "opencode.jsonc"; do
  if [ -e "$target_path/$rel" ] && [ "$force" -eq 0 ]; then
    echo "skip    $rel (exists; use --force to overwrite)"
    continue
  fi
  mkdir -p "$(dirname "$target_path/$rel")"
  cp -R "$repo/$rel" "$target_path/$rel"
  echo "copied  $rel"
done

echo
echo "Done. Open $target_path in OpenCode, work as usual."
echo "At 85% context run /handoff, then open a new chat and run /resume."
