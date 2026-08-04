#!/usr/bin/env bash
# Create a source-only archive from tracked git files (not the working tree).
#
# Outputs:
#   dist/kinetic-trading-<git-describe>.tar.gz
#   dist/kinetic-trading-<git-describe>.tar.gz.sha256
#
# Requires a clean work tree unless ALLOW_DIRTY_TREE=1.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/check_dirty_tree.sh"

mkdir -p dist

describe="$(git describe --always --dirty 2>/dev/null || git rev-parse --short HEAD)"
# Strip characters unsafe for filenames; git describe is usually already safe.
safe_describe="$(printf '%s' "$describe" | tr '/:' '--')"
prefix="kinetic-trading-${safe_describe}"
archive="dist/${prefix}.tar.gz"
checksum="${archive}.sha256"

# Archive tracked files at HEAD. Untracked/ignored paths are excluded by design.
git archive --format=tar.gz --prefix="${prefix}/" -o "$archive" HEAD

if command -v shasum >/dev/null 2>&1; then
  (cd dist && shasum -a 256 "$(basename "$archive")" >"$(basename "$checksum")")
elif command -v sha256sum >/dev/null 2>&1; then
  (cd dist && sha256sum "$(basename "$archive")" >"$(basename "$checksum")")
else
  python3 - "$archive" "$checksum" <<'PY'
import hashlib
import pathlib
import sys

archive = pathlib.Path(sys.argv[1])
checksum = pathlib.Path(sys.argv[2])
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
PY
fi

echo "source-archive: wrote $archive"
echo "source-archive: wrote $checksum"
cat "$checksum"
