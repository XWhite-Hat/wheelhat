"""Write the Twitch application id into the package, just before packaging.

Used by the release workflow, and by anyone building an executable locally who
wants it to behave like a released one. Keeping it here rather than inline in
the workflow means the same code runs in both places, and can be tested.

A Twitch client id is public - it ships readable inside every binary and travels
in the URL of every OAuth request. It is kept out of the repository so forks do
not inherit this project's identity and rate limits, not because it is secret.
Never put a client *secret* here; WheelHat authenticates as a public client and
has no use for one.

    python scripts/bake_client_id.py                 # reads TWITCH_CLIENT_ID
    python scripts/bake_client_id.py <client-id>     # or takes it directly
    python scripts/bake_client_id.py --clear         # put it back to empty
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

TARGET = pathlib.Path(__file__).resolve().parent.parent / "wheelhat" / "twitch" / "client_id.py"
PLACEHOLDER = 'BUNDLED_CLIENT_ID = ""'
ASSIGNMENT = re.compile(r'^BUNDLED_CLIENT_ID = ".*"$', re.M)
VALID = re.compile(r"^[A-Za-z0-9]+$")


def bake(client_id: str) -> str:
    """Replace the assignment in the source file. Returns what it now holds."""
    text = TARGET.read_text(encoding="utf-8")
    if not ASSIGNMENT.search(text):
        raise SystemExit(f"No BUNDLED_CLIENT_ID assignment found in {TARGET}. Was it renamed?")
    updated = ASSIGNMENT.sub(f'BUNDLED_CLIENT_ID = "{client_id}"', text, count=1)
    TARGET.write_text(updated, encoding="utf-8")
    return client_id


def main(argv: list[str]) -> int:
    if "--clear" in argv:
        bake("")
        print(f"Cleared the client id in {TARGET.name}.")
        return 0

    positional = [a for a in argv if not a.startswith("-")]
    client_id = (positional[0] if positional else os.environ.get("TWITCH_CLIENT_ID", "")).strip()

    if not client_id:
        # Not a failure. A fork without the secret still builds; it just asks
        # the user for a client id, exactly as a source run does.
        print("No client id given; the build will ask users for their own.")
        return 0

    if not VALID.match(client_id):
        raise SystemExit("That does not look like a Twitch client id (letters and digits only).")

    bake(client_id)
    print(f"Baked a {len(client_id)}-character client id into {TARGET.name}.")
    print("Remember: this edits a tracked file. Run with --clear before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
