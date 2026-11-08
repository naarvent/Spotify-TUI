# Security Policy

## Supported versions

Security fixes are made for the latest stable release.

| Version | Supported |
| --- | --- |
| 1.x | ✅ |
| < 1.0 | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately with GitHub's [private vulnerability reporting](https://github.com/naarvent/Spotify-TUI/security/advisories/new) (Security tab > *Report a vulnerability*).

Please include:

- what is affected (version, provider, operating system);
- steps to reproduce, or a proof of concept;
- the impact you expect (for example credential exposure or code execution);
- any suggested fix.

You can expect an acknowledgement within a few days. Once the problem is confirmed we will work on a fix, publish a new release and credit you in the advisory if you wish. Please give us reasonable time to fix it before disclosing it publicly.

## What is in scope

- Leaking or mishandling of secrets: Spotify Client Secret and OAuth tokens, Subsonic passwords, the Spicy Lyrics key, the `librespot` credentials.
- Unsafe handling of data from a server or provider (for example a crafted Subsonic response, a TTML lyrics file, or a playlist URL) that could lead to code execution, arbitrary file writes or reads.
- Problems in the installer or the release workflow that could ship a tampered build.

Out of scope: issues that need an already compromised machine or account, denial of service by a server you configured yourself, and weaknesses of third-party services (Spotify, YouTube, your Subsonic server).

## Good practice for users

- Keep the data directory private. It holds `spt_config.json`, token caches and logs (see [Security](README.md#security) in the README).
- Passwords and keys belong in the system keyring or in environment variables, never in `spt_config.json`.
- If you ever shared a token, key or log by mistake, **regenerate it** (Spotify dashboard, Subsonic user, Spicy Lyrics dashboard).
