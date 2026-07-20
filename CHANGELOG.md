# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.2.1] - 2026-07-20

### Changed

- Renamed the "Seek Settings" screen to "Settings" everywhere it is visible to
  the user (the `<` key binding label, the settings screen header and the
  keyboard-shortcut documentation in the README). The internal action and view
  route were renamed to match; the persisted `seek_*` configuration keys were
  kept unchanged for backward compatibility.

### Added

- Configurable maximum size for the lyrics cache, editable from the Settings
  screen as a new field.
  - Accepts human-readable sizes with an explicit, unambiguous unit — `MB` or
    `GB` (for example `200 MB`, `500 MB`, `1024 MB`, `1 GB`, `2 GB`, `4 GB`).
    Units are binary (1 MB = 1024 × 1024 bytes) to match the existing byte-based
    cap; parsing is case-insensitive and tolerant of surrounding whitespace.
  - Robust input validation: a bare number (no unit), an unsupported unit, a
    non-positive value or any malformed input is rejected with a clear error
    message, and a value outside the accepted range (1 MB – 8 GB) reports the
    allowed bounds.
  - The value is stored internally as a byte count and integrated with the
    existing local JSON configuration, so it persists across restarts and is
    re-applied to the live cache cap on startup. A configured cap immediately
    bounds the on-disk cache the next time it is trimmed.
  - Defaults to 2 MB when unset, preserving the previous built-in behaviour.
- New test suite `tests/test_lyrics_cache_size.py` covering MB/GB parsing,
  formatting round-trips, minimum/maximum valid values, invalid and
  out-of-range formats, persistence and reload, fallback to the default when no
  value is configured, the configured cap actually bounding the trimmed cache,
  and an end-to-end walk of the Settings wizard.
