"""Pre-Qt bootstrap for the frozen build.

The shipped executable deliberately does not contain PySide6. It is fetched
into the user's data folder on first run, which keeps the download small and
keeps WheelHat's LGPL obligation simple: the Qt libraries are separate,
user-replaceable files rather than something baked into the binary.

Nothing in this package may import PySide6, or anything that imports it.

Adapted from the bootstrap used in MusicHat by the same author, also MIT.
"""

from .check import run

__all__ = ["run"]
