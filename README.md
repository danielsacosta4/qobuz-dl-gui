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
  re-downloading an album you already have.
- **Library** — sends finished downloads to the library as `Letter/Artist/Album`, reusing existing artist folders and
  verifying every copy. Same album already there in another quality? Replace it, keep both or skip; replaced copies go
  to a hidden bin until you empty it. Navidrome rescans afterwards.
- **Library health**
  - *Library doctor*: CD-quality-or-lower albums available in Hi-Res, clean albums with an explicit version, albums
    missing tracks, missing covers, albums Navidrome shows split in several, unofficial releases — each with a fix.
  - *Complete my artists*: official studio albums owned vs. available, per artist, with the missing ones one click away.
- **Playlist sync**, **Library tools** (lyrics, download history), tray icon with notifications, remembered options and
  queue, and update checks for both qobuz-dl and this app.

### Limitations

- **MusicBrainz allows 1 request per second.** The first look at a large artist takes a few seconds, and the first
  full Library doctor scan of a big library can take a while. Results are cached, so later checks are fast.
  Releases MusicBrainz doesn't know are shown as *unverified*, not hidden.
- Library scans pause between Qobuz requests to keep your account safe.
- The Library doctor and exact "already have it" checks need a Navidrome connection (Settings → Navidrome).
- The MusicButler `radar` command of qobuz-dl isn't available in the app.

## Credits

- **[qobuz-dl Ultimate Edition](https://github.com/Sei969/qobuz-dl)** by Sei969 — does all the downloading and tagging.
- **[qobuz-dl](https://github.com/vitiko98/qobuz-dl)** by vitiko98 — the original project, and everyone credited there.
- **[MusicBrainz](https://musicbrainz.org)** — release types and official-release data (CC0).
- **[Navidrome](https://www.navidrome.org)** — library data through the Subsonic / OpenSubsonic API.

## License

GPL-3.0, the same as qobuz-dl, whose code this app uses directly. See [LICENSE](LICENSE).
