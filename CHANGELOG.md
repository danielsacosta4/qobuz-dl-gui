# Changelog

## 0.11.0 — beta (2026-11-26)

- **Correct a studio-album list yourself.** In Compare (Library health → Complete my artists, and Search) you can
  now **Add album…**, **Remove from list**, **Put back…**, **Use another Wikipedia page…** and **Undo my changes**
  for an artist. Your corrections are kept per artist and used everywhere the list is: the counts in Complete my
  artists, the Release column and the "N of M" count in Search, and Discover's latest album. An artist Wikipedia
  has no list for can get one made of albums you add.
- **Export missing…** (Complete my artists) saves the missing albums of the selected artists, or of every artist
  shown, as a CSV or text file.
- **Queue control**: **Retry** a failed or stopped download, move waiting jobs **↑ / ↓**, and **Pause after this
  job** (also on right-click). The order is remembered when you close the app.
- Under the hood: the Search tab's code is split into smaller files, and the in-app updater, list corrections,
  export and queue are covered by automatic tests (106 in all).
- None of this adds Qobuz requests.

## 0.10.1 — beta (2026-11-15)

- **Safe mode, on by default** (Settings → Qobuz contact), to keep Qobuz traffic to the minimum. While it's on:
  the Discover tab is hidden; *Check on Qobuz* and *Load Qobuz data* are off; Qobuz requests are spaced 3 seconds
  apart; the album details panel loads only when you press *Load details*; Qobuz link look-ups are limited to 5
  per click; downloads run one at a time with at least 2 seconds between tracks. The status bar shows "Safe mode".

## 0.10.0 — beta (2026-11-07)

Much less contact with Qobuz, after Qobuz blocked the account:
- **Qobuz is only contacted when you act.** The Library doctor scan, the Library report, Complete my artists,
  Discover and the background check make no Qobuz request.
- **Complete my artists** compares your library with **Wikipedia's** studio-album lists. A missing album's Qobuz
  link is looked up when you click Download. **Check on Qobuz…** runs the Qobuz-based checks (Hi-Res / explicit
  available, missing tracks, covers) for the artists you select, within a daily limit (0 by default = off).
- **Discover** reads similar artists from your **Navidrome** server (Last.fm) instead of Qobuz. The Qobuz
  similar-artist and featured-album calls, and the Qobuz featured lists, are gone.
- **Automation trimmed** to what is local: send finished downloads, the background library check, wrong-letter
  folder moves. New-release downloads, favourites downloads, the weekly pick and automatic covers were removed.
- **Hard stop**: if Qobuz answers "account blocked" (`USER_BLOCKED`), 403 or 429, the app stops every Qobuz
  request, shows "Qobuz paused", asks before queueing a download and remembers it across restarts until you press
  *I've been unblocked*.
- Compare no longer asks Qobuz by itself (*Load Qobuz data* does, when pressed).

## 0.9.9 — beta (2026-10-28)

- The **Studio albums: Qobuz | Wikipedia** box and **Compare…** also appear on ordinary album searches. They are
  about the artist with the most results in the list; at most 5 artists per list are looked up on Wikipedia.

## 0.9.8 — beta (2026-10-28)

- **Studio albums from Qobuz or Wikipedia**, per artist or for everyone (Settings → Studio albums), with a
  side-by-side Compare view. Used by Complete my artists, Search artist pages and Discover. Wikipedia language:
  English, Portuguese, Spanish, French or German.

## 0.9.7 — beta (2026-10-28)

- New **Discover** tab: artists you don't have, similar to the ones you own, with Dismiss. (It first used Qobuz's
  similar-artist data; 0.10.0 moved it to Navidrome.)

## 0.9.6 — beta (2026-10-21)

- **Faster**: a finished download re-reads only the download history, not the whole library; the Library tab scans
  the download folder in the background and skips it when nothing changed; album details and covers are kept on
  disk.
- **Keyboard shortcuts** (list in Library tools), and **paste or drop links** anywhere to queue them.
- **Send to library by itself** (opt-in): albums new to the library go without the preview, with the same
  checksums and History.
- **Background check** (opt-in) every 6, 12 or 24 hours.
- Status colours follow a light or dark theme; the queue shows an overall bar with a time estimate and how long
  each job took; album details show whether you own the album and each track's quality.

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
