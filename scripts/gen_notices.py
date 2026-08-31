"""Regenerate the dependency table in THIRD-PARTY-NOTICES.md.

The table used to be maintained by hand and had drifted: five bundled packages
were missing, one of them Apache-2.0, which carries retention obligations that
MIT does not. Deriving it from installed metadata means it cannot drift again,
and the accompanying test fails the build if it has.

    python scripts/gen_notices.py            # merge this machine's set in
    python scripts/gen_notices.py --check    # fail if anything is unattributed

Dependencies differ by platform and Python version: uvloop everywhere except
Windows, exceptiongroup below 3.11, colorama on Windows with some versions of
click. No single machine resolves the whole set, so regenerating merges rather
than replaces, and --check fails only on a missing package. Over-attribution is
harmless; a gap is the licence violation.
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

#: Dependencies that exist only on some platforms or Python versions, and so
#: are invisible to a generator run on any single machine. Attribution has to
#: cover every build published, not just the one that produced this file.
#:
#:   colorama        click, on Windows, in some versions
#:   uvloop          uvicorn[standard], on everything except Windows
#:   exceptiongroup  anyio, on Python older than 3.11
#:
#: Naming a package that turns out not to ship costs nothing. Leaving out one
#: that does ship is the failure that matters.
CONDITIONAL = {
    "colorama": "BSD-3-Clause",
    "uvloop": "MIT OR Apache-2.0",
    "exceptiongroup": "MIT",
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

    seen.update(CONDITIONAL)

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
    # Both maps are hand-verified, so they win over whatever a package
    # happens to declare - colorama's classifier says only "BSD License".
    override = LICENCE_OVERRIDES.get(key) or CONDITIONAL.get(key)
    if override:
        return override
    try:
        md = metadata(name)
    except PackageNotFoundError:
        return CONDITIONAL.get(key, "unknown")
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


def listed_in_notices(text: str) -> dict[str, str]:
    """Packages the file already names.

    Read back so that regenerating on one machine cannot drop an entry added on
    another - uvloop is only ever seen off Windows, exceptiongroup only below
    Python 3.11.
    """
    body = text.partition(START)[2].partition(END)[0]
    found: dict[str, str] = {}
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] not in {"Package", "---"} and cells[1] != "---":
            found[cells[0]] = cells[1]
    return found


def build_table(existing: dict[str, str]) -> str:
    """The union of what this machine resolves and what the file already says."""
    merged = dict(existing)
    for name in bundled_distributions():
        merged[name] = licence_of(name)
    lines = ["| Package | Licence |", "| --- | --- |"]
    for name in sorted(merged):
        lines.append(f"| {name} | {merged[name]} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    text = NOTICES.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"Missing {START} / {END} markers in {NOTICES.name}.")

    existing = listed_in_notices(text)

    if "--check" in argv:
        # Only a gap is a failure. Demanding an exact match would mean the file
        # could never satisfy Linux and Windows at the same time, because the
        # dependency set genuinely differs between them.
        missing = sorted(name for name in bundled_distributions() if name not in existing)
        if missing:
            print("Not attributed in THIRD-PARTY-NOTICES.md: " + ", ".join(missing))
            print("Run: python scripts/gen_notices.py")
            return 1
        print(f"All {len(bundled_distributions())} bundled packages are attributed.")
        return 0

    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    updated = f"{head}{START}\n{build_table(existing)}\n{END}{tail}"

    NOTICES.write_text(updated, encoding="utf-8")
    print(f"Wrote {len(listed_in_notices(updated))} packages into {NOTICES.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
