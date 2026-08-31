# WheelHat

[![CI](https://github.com/OWNER/wheelhat/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/wheelhat/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Spinner wheels for Twitch streamers. Build as many wheels as you like, drop each
one into OBS as a browser source, and wire every slice up to actually *do*
something — switch your VTube Studio outfit, change an OBS scene, fire a webhook,
buzz your phone.

The point of difference: WheelHat talks to your running apps directly, so
configuring an action means **picking your real scene from a dropdown**, not
typing a URL and hand-writing a JSON payload.

```
Viewer redeems channel points  →  wheel spins in OBS  →  slice wins  →  actions fire
```

---

## Quick start

WheelHat is a desktop application. Download **`WheelHat.exe`** and run it —
one 22 MB file, nothing to install.

The first time it starts it asks where to keep your wheels, then downloads the
Qt interface library into that folder and opens. After that it starts straight
up. If Qt is ever damaged or deleted, it offers to repair it rather than
failing.

Qt lives beside your data instead of inside the application on purpose: it keeps
the download small, and it means you can replace it yourself, which is what its
licence asks for. Every file is checked against its official SHA-256 before it is
installed.

From source:

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[desktop]"
.venv\Scripts\wheelhat
```

Or double-click **`start.bat`** on Windows, which sets everything up on first run.

Requires Python 3.10 or newer.

### Running it headless

On a dedicated stream box, in a container, or anywhere without a desktop:

```bash
wheelhat --server
```

That serves the same control panel over HTTP with no window, exactly as earlier
versions did. `pip install wheelhat` without the `[desktop]` extra skips Qt
entirely.

### Living in the tray

Closing the window **does not stop WheelHat** — your wheels stay live and
triggers keep firing, which is what you want if you close it by accident
mid-stream. Quit properly from the tray icon or **File → Quit**.

If port 8777 is already taken, WheelHat moves to the next free port and tells you
in the status bar. Your OBS browser source URLs will need updating to match.

---

## Putting a wheel on stream

1. Open a wheel in the control panel and copy its **browser source URL**
   (`http://localhost:8777/overlay/<wheel-id>`).
2. In OBS: **Sources → + → Browser**, paste the URL, set the size to match your
   canvas (1920×1080 is fine — the wheel scales to fit and the background is
   transparent).
3. Leave **"Shutdown source when not visible"** unticked so the overlay stays
   connected between spins.

The overlay is a web page by design — that is what OBS browser sources consume —
so it works the same whether you run the desktop app or the headless server.

The server decides the winner, not the browser, so two sources showing the same
wheel always land on the same slice — and your actions still fire even if no
overlay is open.

---

## Making the wheel yours

Every wheel is customised on its own **Look** and **Images** tabs, with a live
preview beside the editor.

### Images

Upload art once and reuse it anywhere. Drop files onto the picker, or point a
layer at any URL. There are five places art can go:

| Layer | Where it sits |
| --- | --- |
| **Per-slice** | Inside one wedge — game covers, emotes, faces |
| **Overlay / frame** | *On top of* the wheel, and it does not spin: bezels, glass, glow, borders |
| **Background** | Behind the wheel, filling the browser source |
| **Centre / hub** | Clipped to a circle in the middle — your logo |
| **Pointer** | Replaces the drawn triangle |

Each layer has scale, opacity, rotation and nudge controls, all as fractions of
the wheel radius, so a layout survives the source being resized. Slice images add:

- **Size** and **distance from centre** for placement in the wedge
- **Turn with the wheel** — off keeps a logo upright while the wheel spins
- **Keep inside the slice** — clip to the wedge, or let art overflow
- **Hide the label** — for a purely visual wheel

### Shape and type

The **Look** tab covers the rest: a gap between wedges to turn the pie into
separated segments, a centre hole to make it a ring, shading towards the hub,
wedge inlines, and label controls — curved text that follows the wedge,
uppercase, outline, shadow, and how far out the label sits.

Long labels **wrap onto up to three lines and shrink to fit** rather than being
cut short. The fit is measured both ways: each line has to fit between the rim
and the hub, and the block has to fit inside the wedge — so on a busy wheel with
thin wedges a label drops back to one smaller line instead of cramming. Turn
wrapping off for strictly one line per wedge, in which case *Trim labels after N
characters* applies instead.

The **drop shadow** is configurable per wheel — softness, offset, colour and
opacity, or off entirely. Sizes scale with the wheel, so one setting looks right
on any browser source. It is a CSS `drop-shadow`, so a wheel with a centre hole
or gaps between its wedges casts the shadow of that shape rather than a disc.

Wedge borders are drawn as **inlines**, inside each wedge rather than centred on
its edge. Two neighbouring wedges therefore meet inline-to-inline instead of both
painting the shared edge, which would otherwise leave it thicker than the outer
edges and — with a semi-transparent colour — twice as dark.

Three of those colours can also be set **per slice**, on the slice itself: its
label colour, its inline colour, and its label outline colour. Each has an
**Auto** switch: leave it on and the slice follows the wheel (label colour picks
black or white automatically for contrast against the wedge), turn it off to
pick a colour for that one slice.

### Sizing the browser source

Each wheel records the browser source size it is designed for, on the **Look**
tab, with a **recommended** size beside it that fits the wheel, the title, the
winner banner and any frame with nothing cropped. WheelHat always fits whatever
size the source really is - these are there so OBS and the wheel agree.

A frame reaching past the wheel used to be cropped by the edge of the source,
because the canvas only kept a few pixels of slack around the rim. The wheel now
makes room for however far the frame's scale and offset push it.

The winner banner can sit **underneath** the wheel or **on top of** it. Underneath
keeps its row reserved whether or not a winner is showing, so the wheel is one
fixed size and neither resizes nor jumps when a result appears; on top floats it
across the wheel and leaves the full height of the source for the wheel itself.

Assets live in your data folder (the path is on the Settings page) and are served
from `/assets`. PNG, JPG, GIF, WebP and SVG are accepted up to 8 MB, along with
MP3, WAV and OGG for overlay sounds.

---

## Connecting your apps

Go to **Connections → Scan now**. WheelHat checks the machine for the streaming
tools it knows about and reports each one honestly:

| Status | Meaning |
| --- | --- |
| Ready to control | Found it, spoke its protocol, ready to go |
| Port open | Something is listening, but it did not answer as expected |
| Running, server off | The app is open but its WebSocket server is switched off — the row tells you which setting to turn on |
| Not running | Nothing found |

Press **Use it** on anything supported and it becomes a saved connection.

### OBS Studio

Native connector over obs-websocket v5. In OBS: **Tools → WebSocket Server
Settings → Enable WebSocket server**, then paste the password into WheelHat if
you set one.

Once connected, OBS actions offer live dropdowns of your scenes, sources,
filters, text sources, media sources and hotkeys.

### VTube Studio

Native connector over the plugin API. In VTS: **Settings → General → Start API**.
Then press **Authorise** in WheelHat and accept the popup inside VTube Studio —
that happens once, and the token is remembered.

Outfit swaps are hotkeys in VTube Studio, so the "Trigger a hotkey" action with
a dropdown of your real hotkey names is usually what you want.

### Streamer.bot

Native connector over the WebSocket Server API. In Streamer.bot:
**Servers/Clients → WebSocket Server → Enable** (it is on by default at
`127.0.0.1:8080`). A password is optional — WheelHat only needs one if you want
to send chat messages, which is the single privileged request in that API.

This is the one worth wiring up if you already use Streamer.bot, because
everything *it* can reach becomes something a wheel slice can do: YouTube, Kick
and Trovo chat, StreamElements, sound files, OBS via its own connection, C# code.

- **Run an action** — pick from a dropdown of your real actions, grouped exactly
  as they are in Streamer.bot. Selected by id, so renaming an action there will
  not silently break your wheel.
- **Fire a custom trigger** — one trigger, many listening actions.
- **Send a chat message** — Twitch, YouTube, Kick or Trovo.
- **Raw request** — anything else in the API.

The wheel result is passed through as arguments automatically, so inside
Streamer.bot you can use `%winner%`, `%wheel%`, `%user%`, `%reward%` and the rest
without configuring anything. Add your own arguments too; yours win on a clash.

WheelHat identifies Streamer.bot positively — by its `Hello` frame, or a
`GetInfo` reply on builds older than v0.2.5 — rather than assuming anything
answering on 8080 is it, since that port is busy on a typical dev machine.

### Mix It Up

Native connector over its Developer API (`http://localhost:8911/api/v2`). Turn it
on in Mix It Up under **Services → Developer API → Connect**; there is no
password.

Mix It Up is the only other tool surveyed that can list its own configuration, so
the **Run a command** action gives you a real dropdown of your commands, grouped
by type and marked when disabled. Arguments are passed as though a viewer had
typed them, and you can optionally bypass the command's cooldowns and costs.

### Speaker.bot

Native connector over its WebSocket API (`ws://127.0.0.1:7680/`). Enable it under
**Servers/Clients → WebSocket Server**. Speak a templated message in a chosen
voice, or pause/clear the TTS queue.

Its API has no request for listing voice aliases, so the voice is typed rather
than picked. It also has no identify request, which means detection can only go
as far as "something is listening on Speaker.bot's port".

### SAMMI

Native connector over the SAMMI Core API (`http://localhost:9450/api`). Trigger
or release a button, and set SAMMI variables — useful for pushing the winner onto
a SAMMI deck. Set a password under SAMMI's API settings and WheelHat will send it.

Note the port: **9450 is the Core API**, while 9425 is SAMMI Bridge, which is a
different service. SAMMI cannot list its buttons, so button IDs are typed.

### VNyan

Native connector on `ws://127.0.0.1:8000/vnyan`. Sending a trigger name fires the
matching **Websocket Command** trigger in your node graphs — the VNyan equivalent
of a VTube Studio hotkey, and the way to do avatar punishments if VNyan is your
tracker.

VNyan cannot report its own triggers, so the name is typed. Port 8000 is a busy
one, so WheelHat only claims a match if the fixed `/vnyan` path accepts the
connection.

### Detected, but not wired up

The scanner also recognises these and tells you what to do with them, rather than
pretending it can drive them:

| App | Port | Why not |
| --- | --- | --- |
| Lumia Stream | 39231 | Works today with an HTTP request action: `POST /api/send?token=…` |
| Voicemod | 59129 | Its Control API *can* list voices, but needs a client key issued by Voicemod |
| Streamlabs Desktop | 59650 | JSON-RPC over SockJS plus a token from Settings → Remote Control |
| Touch Portal | 12136 | Its API is for plugins to add actions *to* Touch Portal, not to press its buttons |
| Warudo | 19190 | Use its receiver/HTTP asset with an HTTP request action |
| Home Assistant | 8123 | HTTP request action with a long-lived access token |

Anything else with a URL works through the **HTTP request / webhook** action —
Pushover, Pushcut, Discord, n8n and IFTTT all do.

### Going the other way

Every wheel also has a **spin URL** (shown in the editor) that any other app can
fetch to spin it:

```
http://localhost:8777/api/wheels/<wheel-id>/trigger?user=SomeName
```

It is a plain GET so it drops straight into Streamer.bot's *Fetch URL*
sub-action, a stream deck button, Touch Portal, SAMMI, or a browser bookmark.
The optional `user` parameter fills in `{{user}}` for your actions.

---

## Triggers

Each wheel has its own triggers, so different wheels can respond to different
things:

- **Channel point redemption** — pick the reward from a dropdown of your real rewards
- **Chat command** — `!spin`, with a permission gate (everyone / subs / VIPs / mods / you)
- **Bits cheered** — above a threshold
- **Subscription** — new, resub or gifted
- **New follower**
- **Incoming raid** — above a viewer count

All of them support a global cooldown and a per-viewer cooldown.

### Signing in to Twitch

WheelHat signs in with the device code flow, so no password or secret ever
passes through this app. Press **Sign in with Twitch**, then enter the short
code shown at `twitch.tv/activate`. That is the whole process — a released
build carries its own Twitch application, so there is nothing to register.

Twitch [documents the client id as public](https://dev.twitch.tv/docs/authentication/register-app)
("considered public and can be embedded in a web page's source") and recommends
the **public** client type for apps "on a more open platform (such as windows)",
which is exactly this. There is no client *secret* anywhere in WheelHat, and a
public client does not need one.

**Using your own application instead.** Optional, and worth doing if you want
your own name on the consent screen or your own rate limits:

1. Register an app at the [Twitch developer console](https://dev.twitch.tv/console/apps/create).
   Category *Application Integration*, client type **Public**.
2. Paste the Client ID into **Twitch → Use your own Twitch application**.
   Clearing that field goes back to the built-in one.

The console **requires** an OAuth Redirect URL before it will create the app,
but the device code flow never sends one — it posts only the client id, the
scopes and the device code. So the field has to be filled in and is then never
used; `http://localhost` is the usual filler. Only the **client type** matters: it has
to be *Public*, which is the type Twitch recommends for desktop apps anyway.

Builds from source carry no application id, so they ask for one. Save it in
the app, or copy `.env.example` to `.env` and set `WHEELHAT_TWITCH_CLIENT_ID`.

**No Twitch registration at all.** If you already run Streamer.bot, Mix It Up,
Firebot or SAMMI, they hold your Twitch authentication already. Point a channel
point redeem at a wheel's trigger URL
(`http://localhost:8777/api/wheels/<id>/trigger?user=$user`) and you never have
to connect WheelHat to Twitch.

WheelHat subscribes to channel point redemptions as soon as you sign in, so
every reward on your channel is seen whether or not a wheel uses it yet. Other
topics are added as your wheels need them — add a bits trigger and it starts
listening for bits, not before. The Twitch page shows both groups separately.

It also subscribes to `stream.online`, which needs no permissions. Twitch closes
an EventSub connection that has no subscriptions on it, and channel points need
affiliate status, so that one guarantees the connection stays up on any channel.

### Not an affiliate yet?

Channel points and bits only exist on affiliate and partner channels. WheelHat
notices, stops offering them, and says so rather than letting a channel point
trigger sit there never firing — affiliate-only triggers are labelled in the
editor, and the rewards card explains the situation instead of showing a form
Twitch would refuse.

Everything else works. **Chat command** triggers run a wheel from chat on any
channel: set a command like `!spin`, choose who may use it — everyone,
subscribers, VIPs or moderators — and add the usual global and per-viewer
cooldowns. Follows and raids work everywhere too.

### Channel point rewards

The Twitch page can **create rewards on your channel**, so you never have to go
looking for a reward id — create one, then pick it from the dropdown on a
wheel's trigger.

For a reward that already exists — including one made on Twitch itself — press
**Listen** beside the reward dropdown on a channel point trigger and redeem it
on your channel. WheelHat fills the reward in for you.

That listen is deliberately a moment, not a habit. WheelHat sees every
redemption on your channel anyway, but it only remembers one, only while you
have armed it, and the window closes by itself. It keeps the reward's name, id
and cost — never anything about the viewer.

Rewards created in WheelHat can also be **closed automatically**: tick *Close the
redemption once the wheel has spun* on the trigger and the redemption is marked
fulfilled instead of sitting in your queue. If the spin is blocked — a cooldown,
a disabled wheel — the redemption is cancelled instead, refunding the viewer.

This only works for rewards WheelHat created. Twitch restricts closing
redemptions to the application that made the reward, so one you created on
Twitch itself can still *trigger* a wheel, but its redemptions cannot be closed
from here. That restriction is per application, so a reward created by any
WheelHat install can be managed by any other.

There is a **Simulate a redemption** button on the Twitch page so you can test
the whole chain without going live.

---

## Actions

Every slice has its own list of actions, run in order when it wins. A wheel can
also have actions that run before and after every spin.

| Group | Actions |
| --- | --- |
| Web | HTTP request / webhook, Discord message, Pushover notification |
| OBS Studio | Switch scene, show/hide a source, toggle a filter, set text, control a media source, trigger a hotkey, recording & streaming control, raw request |
| VTube Studio | Trigger a hotkey, load a model, set an expression, move the model, raw request |
| Streamer.bot | Run an action, fire a custom trigger, send a chat message, raw request |
| Mix It Up | Run a command |
| Speaker.bot | Speak a message, control the TTS queue |
| SAMMI | Trigger a button, set a variable |
| VNyan | Send a trigger |
| Twitch | Send a chat message, fulfil or refund the redemption |
| Overlay | Show a message, play a sound |
| Flow | Wait |
| System | Run a program *(off by default — enable it in Settings)* |

Each action has a **Test now** button that runs it immediately, so you can get it
right without spinning.

### Template variables

Any text field accepts placeholders:

`{{winner}}` `{{wheel}}` `{{user}}` `{{user_login}}` `{{reward}}` `{{user_input}}`
`{{amount}}` `{{source}}` `{{time}}` `{{date}}`

Filters keep things safe in context: `{{winner|json}}` escapes quotes for a JSON
body, `{{winner|url}}` percent-encodes, and `upper` / `lower` / `trim` / `slug`
do what they say.

```json
{ "text": "The wheel chose {{winner|json}} for {{user|json}}" }
```

---

## Slice options

- **Weight** — a slice with weight 2 is twice as wide, and twice as likely
- **Remove after it wins** — elimination-style wheels
- **Skip for the next N spins** — a cooldown so the same slice cannot repeat
- **Force a win** — spin with a chosen slice rigged to win, to watch its actions run

**Reset eliminations** puts everything back.

---

## How it fits together

```
wheelhat/
├── app.py            FastAPI app, static mounts, lifespan
├── engine.py         Picks the winner, broadcasts the spin, fires actions
├── triggers.py       Twitch events → matching wheels
├── discovery.py      Port fingerprinting + process scan
├── hub.py            WebSocket fan-out to overlays and the control panel
├── db.py             SQLite; wheels are stored as JSON documents
├── actions/
│   ├── schema.py     Field schemas each action type declares about itself
│   ├── handlers.py   The action implementations
│   ├── options.py    Live dropdown data pulled from running apps
│   └── executor.py   Template rendering and chain execution
├── integrations/     Connectors for OBS, VTube Studio, Streamer.bot, Mix It Up,
│                     Speaker.bot, SAMMI and VNyan (supervised, auto-reconnecting)
├── twitch/           Device-code auth, Helix, EventSub over WebSocket
└── web/              Control panel and overlay (vanilla JS, no build step)
```

The editor form for an action is not hand-written. Each action type declares its
fields in `actions/schema.py`, including which ones should be filled from a live
app (`source: "obs.scenes"`), and the frontend renders that generically. Adding
an integration action means describing its fields and writing the handler — the
graphical form comes for free.

Your data lives in `data/wheelhat.db` next to the project (or in
`%APPDATA%\WheelHat` once installed). Settings shows the exact paths, and there
is an export/import button for backups.

---

## Development

```bash
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest          # 248 tests
.venv\Scripts\wheelhat --reload         # auto-reload on edits
```

`tests/fakes.py` contains a stand-in server for every app WheelHat connects to,
each speaking enough of the real protocol to exercise the real connector —
handshakes, authentication, capability lookups, reconnection and failure paths —
with none of those applications installed.

### Command line

```
wheelhat --host 127.0.0.1 --port 8777 --no-browser --reload --log-level debug
```

Set `WHEELHAT_DATA_DIR` to move the database somewhere else.

---

## Contributing

Bug reports, integration requests and pull requests are all welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md). The test suite ships stand-in servers for
every application WheelHat talks to, so you can work on any integration without
installing that app.

---

## Security and privacy

- WheelHat binds to `127.0.0.1` by default and has **no authentication**. Anyone
  who can reach the port can spin your wheels and run their actions. Binding to
  `0.0.0.0` exposes that to your whole network — only do it on a network you
  trust. See [SECURITY.md](SECURITY.md).
- "Run a program" actions are disabled until you turn them on in Settings.
- Passwords and tokens are stored unencrypted in the local SQLite database,
  protected only by your OS file permissions. Settings shows the path.
- Nothing is sent anywhere except the local apps you connect and Twitch itself.

---

## Licence

MIT — see [LICENSE](LICENSE). Dependency licences and trademark notes are in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

WheelHat is an independent project and is not affiliated with or endorsed by
OBS, Twitch, VTube Studio, Streamer.bot, Mix It Up, SAMMI, VNyan or any other
application it connects to.
