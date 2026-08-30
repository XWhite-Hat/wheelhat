#!/usr/bin/env bash
# Replace the OWNER placeholder in repo links with your GitHub username/org.
# Usage:  bash scripts/set-repo-owner.sh your-github-name
set -euo pipefail
owner="${1:?usage: bash scripts/set-repo-owner.sh <github-owner>}"
files=(README.md CONTRIBUTING.md CHANGELOG.md pyproject.toml .github/ISSUE_TEMPLATE/config.yml)
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  python - "$f" "$owner" <<'PY'
import pathlib, sys
path, owner = pathlib.Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
updated = text.replace("OWNER/wheelhat", f"{owner}/wheelhat")
if updated != text:
    path.write_text(updated, encoding="utf-8")
    print(f"updated {path}")
PY
done
