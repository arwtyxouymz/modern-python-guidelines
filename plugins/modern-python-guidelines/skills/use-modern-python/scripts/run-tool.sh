#!/usr/bin/env sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "${script_dir}/modern_python.py" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "${script_dir}/modern_python.py" "$@"
fi

echo "modern-python-guidelines: Python 3.10 or newer is required" >&2
exit 2
