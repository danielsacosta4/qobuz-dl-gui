# Changelog

## 0.9.5 — beta (2026-10-18)

The library rules (see RULES.md):
- The Library doctor now checks five written rules — **R1 one copy, R2 best quality, R3 explicit, R4 location,
  R5 one album in Navidrome** — and shows a line with how many albums break each one.
- New checks: **R1 duplicates** (fix keeps the best copy, moves the others to the bin) and **R4 wrong letter**
  (fix moves the artist folder to the right letter; refuses if one is already there). Both can be undone.
- "Ignore" is now **Mark as exception**; choosing *Keep both* when sending records an R1 exception.
- Better matching with Qobuz: joined artists ("Neil Young • Crazy Horse") are looked up one by one, qualifiers
  like "(Radio Edit)" are tried without, and releases by a different artist with the same title are rejected.
  On a real 4,856-album library this raised albums matched on Qobuz from 80% to 93%.
- Editions (deluxe, remaster…) count as one album; language versions, live recordings and volumes don't.
- R5 no longer reports unrelated artists who share a song title (cover versions): 28 → 9, all genuine.
- Sending shows each stage: **Copying** a file, then **Checking** it as it's read back from the share.

## 0.9.4 — beta (2026-10-18)

Safe writes to the library:
- **Library guard**: the first time, you confirm the library folder and the app places a hidden marker there.
  Without the marker (e.g. the share isn't mounted) the app refuses to write.
- **Preview** of every step before sending albums or fixing problems.
- **Checksums**: every file copied to the library is read back from the share and compared (SHA-256).
  A mismatch stops that album and leaves no bad file behind.
- **History with Undo** (Library tab → History): every change is recorded. Undo restores replaced copies,
  overwritten files, tags and downloads; files you edited since are left alone and reported.
- Nothing is deleted outright any more: replaced files go to the hidden bin, sent downloads to the desktop
  Trash, and tracks removed by Playlist sync to the bin/Trash. Emptying the bin is the one permanent step.

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
