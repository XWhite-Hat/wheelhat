# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository, rather than opening a public issue.

## What WheelHat is, security-wise

WheelHat is a local desktop tool. Understanding its threat model matters more
than any individual bug:

- It binds to `127.0.0.1` by default and has **no authentication**. Anyone who
  can reach the port can create wheels, spin them, and run every action those
  wheels are configured with.
- Binding to `0.0.0.0` exposes all of that to your whole network. Only do it on
  a network you control, and understand you are trusting everyone on it.
- The **Run a program** action can launch arbitrary executables. It is disabled
  by default and must be turned on in Settings.
- Credentials are stored unencrypted in the local SQLite database: OBS and
  Streamer.bot WebSocket passwords, the VTube Studio plugin token, and Twitch
  OAuth tokens. They are protected by your operating system's file permissions
  and nothing more. Settings shows the exact path.
- Twitch sign-in uses the device code flow against **your own** Twitch
  application. No password is ever entered into WheelHat, and no token is sent
  anywhere except Twitch.

Reports that are in scope include: a way to reach the API from outside the
bound interface, a path that executes actions without the shell setting enabled,
credential leakage through the API or logs, or a way for a remote page to drive
the local API through a browser.
