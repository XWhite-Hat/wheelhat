"""Regenerate the dependency table in THIRD-PARTY-NOTICES.md.

The table used to be maintained by hand and had drifted: five bundled packages
were missing, one of them Apache-2.0, which carries retention obligations that
MIT does not. Deriving it from installed metadata means it cannot drift again,
and the accompanying test fails the build if it has.

    python scripts/gen_notices.py            # rewrite the table
    python scripts/gen_notices.py --check    # fail if it is out of date
"""

from __future__ import annotations

import pathlib
import sys
from importlib.metadata import PackageNotFoundError, distribution, metadata

NOTICES = pathlib.Path(__file__).resolve().parent.parent / "THIRD-PARTY-NOTICES.md"
START = "<!-- dependencies:start -->"
END = "<!-- dependencies:end -->"

#: Never shipped: build tooling, and the project itself.
IGNORE = {"wheelhat", "pip", "setuptools", "wheel"}

#: Extras the released executable is built with. Anything only needed for
#: development or testing is not bundled and does not need attribution.
BUNDLED_EXTRAS = ("bootstrap",)

#: Where a package's own metadata is wrong. customtkinter 6.0.0 declares
#: "Creative Commons Zero v1.0" in its License field, but the LICENSE file it
#: ships is MIT (c) Tom Schimansky - a packaging mistake, not a relicensing.
LICENCE_OVERRIDES = {
    "customtkinter": "MIT",
}

#: Attributed whether or not this environment happens to have them.
#: click requires colorama on Windows in some versions and not others, so
#: whether it is bundled depends on which click resolves at build time.
#: Naming a package that turns out not to ship costs nothing; leaving one
#: out that does ship is the failure that matters.
ALWAYS_ATTRIBUTE = {
    "colorama": "BSD-3-Clause",
}


def _requirements(name: str, extra: str = "") -> list[tuple[str, tuple[str, ...]]]:
    """Direct requirements of `name`, as (package, extras) pairs."""
    try:
        requires = distribution(name).requires or []
    except PackageNotFoundError:
        return []
    names: list[tuple[str, tuple[str, ...]]] = []
    for raw in requires:
        # "httpx>=0.27; extra == 'foo'" - keep only what applies to us.
        requirement, _, marker = raw.partition(";")
        marker = marker.strip()
        if marker:
            if "extra ==" not in marker:
                # A platform marker. Keep it: over-attribution is harmless,
                # under-attribution is the failure that matters.
                pass
            elif f"'{extra}'" not in marker and f'"{extra}"' not in marker:
                continue
        # "uvicorn[standard]>=0.29" - the extra matters, it is what pulls in
        # httptools, PyYAML, python-dotenv and watchfiles, all of them bundled.
        requirement = requirement.strip()
        extras: tuple[str, ...] = ()
        if "[" in requirement and "]" in requirement:
            head, _, rest = requirement.partition("[")
            inner, _, remainder = rest.partition("]")
            extras = tuple(e.strip() for e in inner.split(",") if e.strip())
            requirement = head + remainder
        for separator in ("<", ">", "=", "!", "~", " ", "("):
            requirement = requirement.split(separator)[0]
        if requirement.strip():
            names.append((requirement.strip(), extras))
    return names


def bundled_distributions() -> list[str]:
    """Every distribution the released executable carries, resolved from metadata."""
    seen: set[str] = set()
    queue = list(_requirements("wheelhat"))
    for extra in BUNDLED_EXTRAS:
        queue.extend(_requirements("wheelhat", extra))

    seen.update(ALWAYS_ATTRIBUTE)

    while queue:
        name, extras = queue.pop()
        key = name.lower().replace("_", "-")
        if key in IGNORE:
            continue
        try:
            distribution(name)
        except PackageNotFoundError:
            continue
        first_visit = key not in seen
        seen.add(key)
        if first_visit:
            queue.extend(_requirements(name))
        # Extras are followed even on a repeat visit: the package may have been
        # reached without them the first time.
        for extra in extras:
            queue.extend(_requirements(name, extra))
    return sorted(seen)


def licence_of(name: str) -> str:
    """The licence a distribution declares, preferring the SPDX expression."""
    key = name.lower().replace("_", "-")
    override = LICENCE_OVERRIDES.get(key)
    if override:
        return override
    try:
        md = metadata(name)
    except PackageNotFoundError:
        return ALWAYS_ATTRIBUTE.get(key, "unknown")
    expression = (md.get("License-Expression") or "").strip()
    if expression:
        return expression
    declared = (md.get("License") or "").strip()
    if declared and len(declared) <= 40 and "\n" not in declared:
        return declared
    for classifier in md.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            return classifier.split("::")[-1].strip()
    return "see package"


def build_table() -> str:
    lines = ["| Package | Licence |", "| --- | --- |"]
    for name in bundled_distributions():
        lines.append(f"| {name} | {licence_of(name)} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    text = NOTICES.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"Missing {START} / {END} markers in {NOTICES.name}.")

    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    updated = f"{head}{START}\n{build_table()}\n{END}{tail}"

    if "--check" in argv:
        if updated != text:
            print("THIRD-PARTY-NOTICES.md is out of date. Run: python scripts/gen_notices.py")
            return 1
        print("Notices are up to date.")
        return 0

    NOTICES.write_text(updated, encoding="utf-8")
    print(f"Wrote {len(bundled_distributions())} packages into {NOTICES.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
