"""The Twitch application WheelHat signs in through.

Empty in the repository. Release builds inject the real id from a repository
secret before packaging, so a downloaded WheelHat just works, while a build from
source falls back to asking for one - exactly as it always did.

A Twitch client id is public by design: it travels in the URL of every OAuth
request, and Twitch documents it as "considered public and can be embedded in a
web page's source". There is no client secret here and there must never be one.
WheelHat uses the device code grant flow as a *public* client, which Twitch
recommends for applications "on a more open platform (such as windows)" and
which by definition needs no secret.

Keeping the literal out of the repository is not about secrecy - it ships in
every binary and can be read straight out of it. It is so that forks and copies
do not silently inherit this project's identity and rate limits.
"""

from __future__ import annotations

import os

#: Written by the release workflow. Left empty in source control.
BUNDLED_CLIENT_ID = ""

#: Escape hatch for development, and for anyone running their own application
#: without wanting to save it into the database.
ENV_VAR = "WHEELHAT_TWITCH_CLIENT_ID"


def bundled() -> str:
    """The client id this build ships with, if any.

    The environment wins over the baked-in value so a developer can point a
    normal build at their own application without editing files.
    """
    return (os.environ.get(ENV_VAR) or BUNDLED_CLIENT_ID).strip()
