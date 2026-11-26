# Qobuz-DL GUI

A desktop app (Linux, Qt 6 / PySide6) for downloading Hi-Res music from Qobuz and keeping a
Navidrome library in shape. It is a front-end for **[qobuz-dl Ultimate Edition](https://github.com/Sei969/qobuz-dl)**
and uses its own `~/.config/qobuz-dl/config.ini`, so the app and the `qobuz-dl` command line share one setup.

> An active Qobuz subscription is required. Not affiliated with Qobuz.

## Install

```
./install.sh            # qobuz-dl from PyPI
./install.sh --git      # qobuz-dl from GitHub
./install.sh --uninstall
```

`sudo pacman -S pyside6 ffmpeg` (Arch / CachyOS) first gives the native KDE look and segmented downloads.

## What it does

- **Search** — albums, tracks, artists, playlists and your Qobuz favourites, with covers and an album details panel.
  Columns for release type, **Official** (checked on MusicBrainz), edition, explicit, quality and whether you already
  have it (✔ library, ↓ download history). Filters: release types, *Prefer explicit*, *Official only*, hidden
  releases/labels. Artist pages show how many official studio albums you own and offer *Download discography*.
- **Download** — links or a `.txt` list (resumable), quality, folder and per-download options. Warns before
  re-downloading an album you already have. The queue can be paused and reordered, and a failed
  download retried.
- **Library** — sends finished downloads to the library as `Letter/Artist/Album`, reusing existing artist folders and
  verifying every copy. Same album already there in another quality? Replace it, keep both or skip; replaced copies go
  to a hidden bin until you empty it. Navidrome rescans afterwards.
- **Library health**
  - *Library doctor*: scans your library through Navidrome and your folders — one copy of each album,
    Letter/Artist/Album location, one album per release in Navidrome ([RULES.md](RULES.md)), missing covers. Each
    problem has a fix (previewed, verified, undoable) or can be marked as an exception. **The scan does not contact
    Qobuz.**
  - *Complete my artists*: your albums against **Wikipedia's** studio-album lists, per artist, with the missing ones
    one click away (the Qobuz link is looked up when you click Download). *Export missing…* saves the list as a
    CSV or text file. *Check on Qobuz* adds the checks that need
    Qobuz's catalogue — Hi-Res or explicit version available, missing tracks, covers — for the artists you pick,
    within a small daily limit you set.
- **Studio albums from Qobuz or Wikipedia** — choose per artist (or for everyone) which list counts as the artist's
  studio albums (on album searches and artist pages), with a side-by-side comparison. Wikipedia pages are read
  through its public API (English, Portuguese, Spanish, French or German). When a list is wrong you can
  correct it yourself: add or remove albums, or point the artist at another Wikipedia page.
- **Discover** (hidden while Safe mode is on) — artists you don't have yet, similar to the ones in your library
  according to your **Navidrome** server (which asks Last.fm), optionally weighted by what you play. Qobuz is only
  asked when you press *Search on Qobuz* or *Download latest album*.
- **Automation** (Settings → Automation, off by default) — a background library check every few hours that can move
  wrong-letter folders (undoable), and finished downloads that are new to the library can be sent there by
  themselves. Automation never contacts Qobuz and never downloads anything.
- **Keyboard first** — Ctrl+F, Ctrl+D, Delete, Ctrl+Return, F5, Esc, Ctrl+1…8; paste (Ctrl+V) or drop Qobuz links
  anywhere to queue them. The full list is in Library tools.
- **Playlist sync**, **Library tools** (lyrics, download history), tray icon with notifications, remembered options and
  queue, and update checks for both qobuz-dl and this app.

### When the app contacts Qobuz

Only when you act: searching, opening an artist or album, downloading (qobuz-dl does the downloading), Download on a
missing album, *Download latest album* and *Load Qobuz data* in Compare, *Check on Qobuz* (within your daily limit,
Settings → Qobuz contact). Scans, the Library report, Complete my artists, Discover and the background check never
do. Every Qobuz request goes through one gate that spaces requests out and, if Qobuz says the account is blocked or
rate-limited, **stops all Qobuz requests** until you press *I've been unblocked*. The app uses Qobuz's web-player
credentials through qobuz-dl, which Qobuz may not permit; use it at your own risk.

**Safe mode** (Settings → Qobuz contact, on by default) keeps Qobuz traffic to the minimum: no Discover tab, no
*Check on Qobuz* or *Load Qobuz data*, requests 3 seconds apart, album details only when you press *Load details*,
at most 5 Qobuz link look-ups per click, and downloads one at a time with a 2-second delay between tracks. Leave it
on, especially after a Qobuz block.

### Limitations

- **MusicBrainz allows 1 request per second.** The first look at a large artist takes a few seconds, and the first
  full Library doctor scan of a big library can take a while. Results are cached, so later checks are fast.
  Releases MusicBrainz doesn't know are shown as *unverified*, not hidden.
- Wikipedia pages are laid out differently from artist to artist; if a studio-album list reads wrongly, the Compare
  view shows what was found.
- Discover needs a Last.fm API key on your Navidrome server.
- The Library doctor and exact "already have it" checks need a Navidrome connection (Settings → Navidrome).
- The MusicButler `radar` command of qobuz-dl isn't available in the app.

## Tests

```
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

`tests/` holds unit tests for the logic and `tests/ui/` drives the real window headless, with Qobuz, Navidrome and
Wikipedia replaced by fakes, so no test touches the network or an account. Two of them fail if anything the app
does by itself (opening, scanning, the background check, the report) contacts Qobuz. They run on every push and pull
request (GitHub Actions). `tests/test_real_library.py` is a separate script for a recorded real library.

## Credits

- **[qobuz-dl Ultimate Edition](https://github.com/Sei969/qobuz-dl)** by Sei969 — does all the downloading and tagging.
- **[qobuz-dl](https://github.com/vitiko98/qobuz-dl)** by vitiko98 — the original project, and everyone credited there.
- **[MusicBrainz](https://musicbrainz.org)** — release types and official-release data (CC0).
- **[Navidrome](https://www.navidrome.org)** — library data through the Subsonic / OpenSubsonic API.

## License

GPL-3.0, the same as qobuz-dl, whose code this app uses directly. See [LICENSE](LICENSE).
