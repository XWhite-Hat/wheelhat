# Contributing to WheelHat

Thanks for taking a look. Bug reports from real streams are as useful as code.

## Getting set up

```bash
git clone https://github.com/OWNER/wheelhat
cd wheelhat
python -m venv .venv
.venv\Scripts\pip install -e ".[dev,desktop]"
.venv\Scripts\wheelhat                 # the desktop application
.venv\Scripts\wheelhat --server --reload   # headless, auto-reloading
```

`--reload` restarts the process on every edit, which a window cannot survive, so
it implies `--server`. Work on the web UI through the reloading server and check
it in the desktop shell afterwards.

## Before you open a pull request

```bash
.venv\Scripts\python -m ruff check .      # rules live in pyproject.toml
.venv\Scripts\python -m pytest -q         # 204 tests, about 35 seconds
```

CI runs the same two commands on Windows and Linux across Python 3.10 to 3.13,
then builds a wheel and checks the packaged app actually serves.

Code formatting is **not** enforced. Match the surrounding style; do not
reformat files you are not otherwise changing.

## Testing without the apps installed

`tests/fakes.py` holds stand-in servers for every application WheelHat connects
to — OBS, VTube Studio, Streamer.bot, Mix It Up, SAMMI, Speaker.bot and VNyan —
each implementing enough of the real protocol to exercise the real connector.
You do not need any of those installed to work on an integration.

If you add an integration, add a fake for it. A connector without one cannot be
tested by anyone who does not personally run that app.

## The desktop shell

`wheelhat/desktop/` wraps the same server in a PySide6 window. The HTTP server
cannot be removed - OBS browser sources need a URL - so this is a shell around
it rather than a replacement for it.

Things that are easy to break there, and how they are handled:

- **Downloads.** QtWebEngine discards them by default; `webview.py` routes them
  through a native save dialog. Test with Settings -> Export everything.
- **External links.** Anything off-origin is handed to the real browser, so the
  streamer stays signed in to Twitch.
- **No console.** A windowed build has no stdout or stderr, so logs go to
  `wheelhat.log` in the data folder. Help -> Open the log file.

Build it with:

```bash
pyinstaller build/wheelhat.spec --noconfirm --clean --distpath dist --workpath build/work
```

PySide6 is in the spec's `excludes` on purpose. `wheelhat/bootstrap/` downloads
it into the user's data folder on first run, which keeps the executable at about
22 MB and keeps Qt user-replaceable for the LGPL. If you ever find PySide6 in the
bundle, something has gone wrong - the release workflow fails the build if the
executable exceeds 120 MB for exactly that reason.

The bootstrap runs before Qt exists, so nothing under `wheelhat/bootstrap/` may
import PySide6, or anything that imports it. It logs to
`%LOCALAPPDATA%\WheelHat\bootstrap.log`; read that first when setup misbehaves.

## Adding an action

Actions describe their own form fields, and the editor renders them
generically. In `wheelhat/actions/handlers.py`:

```python
@action_type(
    "my_action",
    "Human label",
    "Group name",
    description="What it does, in one sentence.",
    requires="obs",                       # connector kind, if any
    fields=[Field(key="thing", label="Thing", type="select", source="obs.scenes")],
)
async def my_action(config: dict[str, Any], ctx: ExecContext) -> str:
    ...
    return "what happened"           # this string lands in the action log
```

A `source` on a field makes it a live dropdown; add the resolver to
`wheelhat/actions/options.py`. Return a short description of what happened, and
raise `ActionFailed` with a message a streamer can act on.

## Adding an integration

1. Subclass `Connector` (WebSocket) or `HttpConnector` (REST) in
   `wheelhat/integrations/`.
2. Register it in `wheelhat/integrations/registry.py` and add the kind to
   `IntegrationConfig` in `wheelhat/models.py`.
3. Add a `KnownApp` entry in `wheelhat/discovery.py` with a probe that
   identifies the app **positively**. An open port is not an identification —
   several of these live on busy ports.
4. Add a fake and tests.

## House rules for error messages

Errors are read by streamers mid-stream, not by developers. Say what went wrong
and where the setting lives:

> OBS requires a WebSocket password (Tools > WebSocket Server Settings > Show Connect Info)

not `AuthenticationError: 4009`.

## Reporting a bug

Include the WheelHat version (Settings page), your OS, which apps you had
connected, and anything from the Activity page. If it involves an integration,
say which version of that app you are running.
