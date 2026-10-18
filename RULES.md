# Library rules

These are the rules Qobuz-DL GUI keeps your library to. The Library doctor (Library health tab) checks
R1–R5 and shows how many albums break each one; the Library report measures them and writes the lists.
Anything you decide is fine as it is can be marked as an **exception** — it no longer counts as a break.

| Rule | Name | What it means | Checked by |
|---|---|---|---|
| R0 | Intact | Every file plays from start to end without errors. | `flac -t` (outside the app, see below) |
| R1 | One copy | Each album is in the library exactly once. | Doctor · report `duplicates.csv` |
| R2 | Best quality | Each album is in the best quality Qobuz offers: Hi-Res when it exists, lossless at least. | Doctor · report `upgrades.csv` |
| R3 | Explicit | Each album is the explicit version whenever an explicit version exists. | Doctor · report `explicit.csv` |
| R4 | Location | Every album is filed as `Letter/Artist/Album`. | Doctor · report `location.csv` |
| R5 | One album in Navidrome | Navidrome shows each album once, under one album artist. | Doctor · report `split.csv` |

## What counts as "the same album"

- **Editions are the same album**: deluxe, remastered, expanded, anniversary, bonus tracks, a year in brackets
  ("(2021 Remaster)"), explicit/clean. Keeping two of these breaks R1.
- **Language versions are different albums**: "Variéty" and "Variéty (Version Anglaise)" are both kept.
- **Live recordings are different albums**: "(Live At The Palace / 1999)" is not an edition of the studio record.
- **Volumes and parts are different albums**: "Racional Vol. 1" and "Vol. 2".
- **Remixes are different releases**: "(Kölsch Remix)" is not the original. Cuts like "(Original Mix)",
  "(Radio Edit)" or "(Extended Mix)" are only used to *find* a release on Qobuz, never to merge two of yours.
- Tagging notes in square brackets ("[2023, WEB]") are ignored.

## Matching your albums to Qobuz

- Navidrome joins several album artists as "A • B" (or "A; B"); each artist is looked up on Qobuz.
- A Qobuz release only counts as one of your albums when its **main artist is one of the album's artists**.
  (Qobuz artist pages also list other people's records — e.g. a release by another band that happens to have
  the same title.)

## R4 in detail

- The letter is the first letter of the artist's name with accents dropped (`Ágata` → A).
- "The …" goes under **T** (`The Cure` → T).
- Names starting with a digit or a symbol go under **#**.
- The Doctor's fix moves the whole artist folder to the right letter. It refuses if a folder with that name is
  already there (merge those by hand) and can be undone from History.

## R1 fix

The Doctor keeps the best copy (higher quality first, then more tracks) and moves the others to the hidden bin
(`.replaced-by-qobuz-dl-gui` in the library). Folders are taken from Navidrome's own file paths; if the copies
can't be told apart that way, the fix refuses and asks you to sort them by hand. Choosing **Keep both** when
sending from the Library tab records an R1 exception.

## Every write is safe

All changes the app makes to the library (sending, fixes, sync deletions) are shown in a preview first, refuse
to run when the library marker isn't there (share not mounted), verify every copied file by reading it back from
the share (SHA-256), and are recorded in History with Undo.

## Independent checks (not done by the app)

- **R0**: `find /mnt/navidrome/Music -name '*.flac' -print0 | xargs -0 -n50 flac -t -s -w` — any output is a
  broken file.
- **Owned / matching**: the report's `spot_check.csv` — 30 random albums the app says you own, to confirm by eye.
- **Tags**: open a random sample of 20 albums in MusicBrainz Picard and compare.
