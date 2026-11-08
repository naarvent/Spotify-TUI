# Contributing to Spotify-TUI

Thanks for your interest! Issues and pull requests are welcome. This guide keeps it quick.

## Ways to help

- **Report a bug** or **suggest a feature** with the [issue templates](https://github.com/naarvent/Spotify-TUI/issues/new/choose).
- **Try a provider** you use (Spotify, YouTube Music, Subsonic / Navidrome, Spotify via MPRIS) and tell us what breaks.
- **Send a pull request** (see below). For anything bigger than a small fix, open an issue first so we can agree on the approach.

> Security problems: please do **not** open a public issue. See [SECURITY.md](SECURITY.md).

## Development setup

You need Python 3.9 or newer (the project is developed on 3.14).

```bash
git clone https://github.com/naarvent/Spotify-TUI.git
cd Spotify-TUI
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[ytmusic,subsonic]"
python -m spt_tui                  # or: spt
```

Optional tools, depending on what you work on: `mpv` (playback for YouTube Music and Subsonic), `yt-dlp`, `cava` (visualizer), `playerctl` (MPRIS provider).

## Running the tests

Every suite is a standalone script that runs offline (no network, no real account):

```bash
python tests/run_all.py            # all suites, in parallel
python tests/test_view_focus.py    # a single suite
python tests/run_all.py --help     # workers, name filter, timeout, verbose
```

The runner isolates each suite with its own temporary data directory, so it never touches your real config, tokens or caches. All suites must pass before you open a pull request.

Static checks:

```bash
python -m compileall -q spt_tui
python -m pyflakes spt_tui
python -m vulture spt_tui --min-confidence 80
git diff --check
```

## Pull request checklist

1. Keep the change **focused** (one idea per pull request).
2. **Preserve existing keyboard and navigation behaviour** unless the change explicitly modifies it.
3. **Add regression tests** for bug fixes and new behaviour. A test prints a final line like `12/12 checks passed`, which the runner reads.
4. Run the **whole suite** and the **static checks** above.
5. **Update the docs** when something user-visible changes: `README.md` and `CHANGELOG.md`.
6. Use clear commit messages. The project uses [Conventional Commits](https://www.conventionalcommits.org/) prefixes (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `ci:`).

## Project layout

| Path | What lives there |
| --- | --- |
| `spt_tui/providers/` | One class per provider (`spotify`, `ytmusic`, `subsonic`, `mpris`) behind the common interface in `base.py`, including its declared capabilities |
| `spt_tui/app/` | The Textual UI: navigation, tables, search, playback, queue, lyrics, visualizer, settings |
| `spt_tui/players/` | The shared `mpv` player |
| `tests/` | Offline test suites and `run_all.py` |

When you add or change a provider, declare what it supports through its capabilities: the UI hides or disables whatever the active provider cannot do. Never assume a Spotify-only feature in shared code.

## Secrets and personal data

- **Never commit** the Spotify Client Secret, OAuth tokens, a Subsonic password, a Spicy Lyrics key or any token cache. Passwords and keys go to the system keyring or to environment variables, never to `spt_config.json`.
- Tests must not use real credentials or touch real user data; use fake keys and the temporary data directory (`SPT_TUI_CACHE_DIR`).
- Remove private information (account names, playlists, paths, tokens) from logs, screenshots and GIFs before sharing them.

## Licence

By contributing you agree that your contribution is released under the project's [MIT licence](LICENSE).
