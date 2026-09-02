"""Stamp a release version into the source, just before building.

The version lives in two places that have to agree: `wheelhat/__init__.py`,
which is what the app reports and what the About dialog and the User-Agent
show, and `pyproject.toml`, which names the wheel and the sdist. Setting one
and not the other ships a 1.2.0 executable inside a wheelhat-0.1.0.whl.

In the repository both are the placeholder, so a build from source is honestly
labelled as one rather than claiming to be whichever release came last.

    python scripts/set_version.py v1.2.3     # or 1.2.3
    python scripts/set_version.py            # reads GITHUB_REF_NAME
    python scripts/set_version.py --clear    # back to the placeholder
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "wheelhat" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"

#: What an untagged build reports. A valid PEP 440 local version, so the wheel
#: still builds, and obviously not a release.
PLACEHOLDER = "0.0.0+source"

INIT_PATTERN = re.compile(r'^__version__ = "[^"]*"$', re.M)
PYPROJECT_PATTERN = re.compile(r'^version = "[^"]*"$', re.M)

#: Tags look like v1.2.3, and may carry a pre-release suffix.
TAG = re.compile(r"^v?(\d+\.\d+(?:\.\d+)?(?:[.-]?(?:a|b|rc|alpha|beta|dev)\.?\d*)?)$", re.I)


def normalise(raw: str) -> str:
    """`v1.2.3` to `1.2.3`, rejecting anything that is not a version."""
    match = TAG.match(raw.strip())
    if not match:
        raise SystemExit(
            f"{raw!r} is not a version tag. Expected something like v1.2.3."
        )
    return match.group(1)


def apply(version: str) -> None:
    init = INIT.read_text(encoding="utf-8")
    if not INIT_PATTERN.search(init):
        raise SystemExit(f"No __version__ assignment in {INIT.name}. Was it renamed?")
    INIT.write_text(INIT_PATTERN.sub(f'__version__ = "{version}"', init, count=1), encoding="utf-8")

    project = PYPROJECT.read_text(encoding="utf-8")
    if not PYPROJECT_PATTERN.search(project):
        raise SystemExit("No version line in pyproject.toml. Was it moved?")
    PYPROJECT.write_text(
        PYPROJECT_PATTERN.sub(f'version = "{version}"', project, count=1), encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    if "--clear" in argv:
        apply(PLACEHOLDER)
        print(f"Version reset to {PLACEHOLDER}.")
        return 0

    positional = [a for a in argv if not a.startswith("-")]
    explicit = bool(positional)
    raw = positional[0] if explicit else os.environ.get("GITHUB_REF_NAME", "")
    if not raw:
        # Not a failure: a build with no tag is simply a build from source.
        print(f"No tag given; leaving the version as {PLACEHOLDER}.")
        return 0

    # A version passed by hand has to be a version - a typo there would ship
    # mislabelled. A ref read from the environment is often a branch, because
    # the workflow can be run manually for a dry run, and that is not an error.
    if not explicit and not TAG.match(raw.strip()):
        print(f"{raw!r} is not a version tag; leaving the version as {PLACEHOLDER}.")
        return 0

    version = normalise(raw)
    apply(version)
    print(f"Stamped version {version} into {INIT.name} and pyproject.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
