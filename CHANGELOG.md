# Changelog

## 0.9.3 — beta (2026-10-18)

- New: **Library report** (Library health tab, or `qobuz-dl-gui --report`). Measures the library against the
  rules using Navidrome as the source of truth — % of albums recognised as owned, duplicates, CD-or-lower with
  Hi-Res available, clean copies with an explicit version, folders out of place, split albums — and writes CSV
  lists plus a random spot-check sample. Read-only: nothing in the library is changed.

## 0.9.2 — beta (2026-10-15)

- Fixed: albums whose names carry a tag suffix such as `[2023, WEB]` weren't recognised as already in the library (✔ marks, warnings, Library doctor, Complete my artists)

## 0.9.1 — beta (2026-10-15)

- Fixed: the app didn't start (the update-check code was attached to the wrong tab)

## 0.9.0 — beta (2026-10-15)

First public build, for testing before 1.0.

- Search with release type, official-release check (MusicBrainz), edition, explicit, quality and "already have it" columns; filters and hidden releases/labels
- Downloads with per-download options, queue remembered between launches, tray icon and notifications
- Library tab: send albums to `Letter/Artist/Album`, replace lower-quality copies (hidden bin), verified copies
- Library health: Library doctor and Complete my artists
- Navidrome connection (library data, rescan after sending)
- Update checks for qobuz-dl and this app
