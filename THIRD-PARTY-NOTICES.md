# Third-party notices

WheelHat itself is MIT licensed (see [LICENSE](LICENSE)). It depends on the
following third-party packages, all under permissive licences compatible with
MIT redistribution.

## Runtime dependencies

| Package | Licence |
| --- | --- |
| fastapi | MIT |
| starlette | BSD-3-Clause |
| pydantic, pydantic-core | MIT |
| uvicorn | BSD-3-Clause |
| httpx | BSD-3-Clause |
| httpcore | BSD-3-Clause |
| h11 | MIT |
| httptools | MIT |
| websockets | BSD-3-Clause |
| psutil | BSD-3-Clause |
| anyio | MIT |
| click | BSD-3-Clause |
| colorama | BSD-3-Clause |
| idna | BSD-3-Clause |
| python-dotenv | BSD-3-Clause |
| pyyaml | MIT |
| watchfiles | MIT |
| typing-extensions | PSF-2.0 |
| certifi | MPL-2.0 |

## Desktop application

The desktop interface is built on **PySide6 (Qt for Python)**, under the
**LGPL v3**.

PySide6 is **not bundled into the executable**. On first run WheelHat downloads
it from PyPI into the user's own data folder, verifying the official SHA-256 of
every wheel before extracting it. That keeps the download small, and it makes
the LGPL position simple: Qt is a separate set of files the user can inspect,
replace or upgrade themselves, with no relinking required of anyone.

Qt source is available from <https://download.qt.io/>, and PySide6 source from
<https://pypi.org/project/PySide6/>.

The first-run wizard is drawn with **customtkinter** (MIT) and **darkdetect**
(BSD-3-Clause), which are bundled, because they have to work before Qt exists.

PyQt was deliberately not used: it is GPL-or-commercial, which would have forced
the distributed application to GPL terms.

**certifi** is Mozilla Public License 2.0. MPL-2.0 is file-level copyleft: it
applies to modifications of certifi's own files and imposes nothing on WheelHat's
source. If you redistribute a bundled build (for example a PyInstaller
executable) that includes certifi, include its licence text and make its source
available, which the MPL requires.

## Development-only dependencies

Not redistributed with the application: pytest (MIT), pytest-asyncio
(Apache-2.0), ruff (MIT), build (MIT).

## Fonts

The control panel requests the **Inter** typeface from Google Fonts at runtime
(SIL Open Font License 1.1). No font files are bundled, and the interface falls
back to system fonts when offline.

## Trademarks

WheelHat is an independent project. It is not affiliated with, endorsed by, or
sponsored by any of the applications it connects to. OBS Studio, VTube Studio,
Streamer.bot, Speaker.bot, Mix It Up, SAMMI, VNyan, Twitch, Voicemod, Lumia
Stream, Streamlabs, Touch Portal and Warudo are trademarks of their respective
owners, referred to here only to describe interoperability.

WheelHat talks to those applications through their own public, documented local
APIs. It contains no code copied from them.
