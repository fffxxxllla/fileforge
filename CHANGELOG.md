# Changelog

## 2.0.1 — 2026-09-09

- Increased the light-theme contrast of the overwrite switch in its off state.
- Accepted arbitrary decimal capacity values, rounded to the nearest whole byte.
- Accepted leading-decimal input such as `.25 MB`.

## 2.0.0 — 2026-09-09

- Added a standalone, single-file Windows application.
- Rebuilt the interface with a modern card layout, system/light/dark themes, high-DPI support, size presets, fixed action bar, and completion actions.
- Changed KB/MB/GB/TB defaults to 1024-based Windows-compatible sizing.
- Added explicit decimal units (`KB1000`, `MB1000`, `GB1000`, `TB1000`).
- Added exact-byte and Windows display previews before generation.
- Progress is now visible only during active generation.
- Updated the application icon, version metadata, source build scripts, tests, and documentation.

## 1.0.0 — 2026-09-09

- Initial CLI and Tk interface.
- Added 47 format families, exact-size generation, sparse payloads, atomic replacement, and validation.
