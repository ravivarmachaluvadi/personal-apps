# Personal Apps

Five single-file web apps whose data lives as JSON files in **your own Google Drive**, not in any app's
database. Open them from a laptop or a phone, sign in with Google once per device, and every device sees the
same data. The **Continue with Google** button sits in the header of every page; until you tap it on a device,
that device keeps its data only in its own browser (the header then says *changes waiting*).

| App | Page | Drive file (in `My Drive/Personal Apps`) |
|---|---|---|
| Keycap Atlas | `docs/keycap-atlas.html` | `keycap-atlas.json` — shortcuts and commands, by app and OS |
| Strongroom | `docs/strongroom.html` | `strongroom-vault.json` — password vault, ciphertext only |
| Dumbbell Dojo | `docs/dumbbell-dojo.html` | `dumbbell-dojo.json` — ticks, weight logs, plan choice |
| Tally Board | `docs/tally-board.html` | `tally-board.json` — activities, plans, milestones, notes |
| Spine Bell | `docs/spine-bell.html` | `spine-bell.json` — per-device counters and the daily diary |

`docs/index.html` is a launcher for all five. `docs/drive-sync.js` is the shared storage layer; `docs/config.js`
holds the one setting you must fill in (the Google OAuth client ID).

## How the storage works

- Each page keeps one JSON document: `{ v, items: { id: { ..., updatedAt } } }`.
- The browser caches the document in `localStorage` and remembers whether it has unsaved changes, so pages open
  instantly and work offline. Nothing is lost if you are signed out; it is uploaded on the next sign-in.
- A change is uploaded about 1.5 s after you make it. Before uploading, the page checks whether the Drive file
  changed since it last looked; if so it merges first (newest `updatedAt` wins per item, deletions are
  tombstones) and then writes. Other devices pick changes up within about 90 s, or immediately when the page is
  reopened or brought back to the foreground.
- Access uses the `drive.file` scope, which only lets the pages see files they created themselves.
- The vault file contains only AES-256-GCM ciphertext plus the salt used to stretch the master password.
  The master password never leaves the browser. There is no reset.

## Setup status (done on 2026-09-06)

- Hosted at https://ravivarmachaluvadi.github.io/personal-apps/ from the GitHub repo `ravivarmachaluvadi/personal-apps`
  (Pages serves `docs/` on `main`). Deploy = commit and `git push origin main`.
- Google Cloud project `personal-apps-507811` ("Personal Apps"): Drive API enabled, OAuth consent screen External,
  test user = your Gmail, Web client "Personal Apps site" with origins `https://ravivarmachaluvadi.github.io` and
  `http://localhost:8000`. The client ID is in `docs/config.js`; it is public by design and only works from those
  origins. Manage it at https://console.cloud.google.com/auth/overview?project=personal-apps-507811.
- Local testing: `python -m http.server 8000 --directory docs`, then http://localhost:8000/.

If sign-in ever stops working: check the Audience page of that console project (publishing status and test users)
and that the origin you are using is listed on the client.

### 3. First run

- **Keycap Atlas**: press *Load the starter set* to add the 250 shortcuts and the `caffeinate` commands, or import
  `seed/shortcuts.json`.
- **Strongroom**: choose a master password, then Settings → Import a file → `vault-import.csv` (delete the CSV
  and the source PDF afterwards).
- **Dumbbell Dojo**: Guide tab → *Import a backup* if you exported data from the old page; otherwise just start.

On a phone: open the site, sign in, and use *Add to Home Screen*.

## Timers and reminders on a phone

- A phone browser pauses a page that is not on screen (another app in front, or the screen locked), so no web
  page can ring a bell from the background. Spine Bell therefore keeps the screen awake while a countdown runs
  (toggle in Rhythm); leave it open and face up. When you come back to a page whose time ran out, it rings then.
- **Android**: while a countdown runs, Spine Bell shows *Hand this countdown to the phone's Clock app*. That
  opens the phone's own Clock with the same timer, which rings from anywhere. The Rhythm toggle *Also set the
  phone's Clock timer* does this automatically on every start. Both use the standard `SET_TIMER` intent; they
  need a Clock app that supports it (Google Clock and Samsung Clock do). Untested on real hardware so far.
- **Notifications**: `docs/sw.js` is a tiny service worker that exists only so Android Chrome can show the
  Spine Bell and Tally Board banners (Android refuses page-level notifications). It caches nothing.
- **Just a timer**: the fourth Rhythm preset in Spine Bell is a plain countdown, either N minutes or "ring at"
  a clock time, one bell and no break cycle. The clock time is one-off; the next start uses the minutes.

## Sign-in details worth knowing

- The Google token lasts one hour. After that the pages show *Continue with Google* again; one tap, no consent
  screen. Data entered while signed out is kept and uploaded after the tap.
- Sign out from the status pill at the top of any page.
- If a browser blocks the Google pop-up, allow pop-ups for the site once.

## Where things live

`docs/` is the only place the apps live now. `docs/river-rapids/` and the other game folders were copied in from
the old `Documents/FromClaude/FromClaude/Personal` folder and are linked from the launcher. The 3 September copies
of Dumbbell Dojo, Spine Bell and Tally Board from that folder (artifact versions without Drive sync) are kept in
`archive/from-claude-2026-09-03/` for reference only.

## Repository layout

- `docs/` — the site. `tools/build-site.py` regenerates the five pages from the sources below; run it after
  editing any source. It leaves `docs/data/keycap-atlas.json` alone unless the gitignored `seed/export/` folder
  is present. `docs/sw.js` is the notification service worker (not built, edit in place).
- `keycap-atlas.html`, `strongroom.html` — page sources (artifact-style fragments; the build adds the storage layer
  and the HTML skeleton).
- `site/dumbbell-dojo.html`, `site/tally-board.html`, `site/spine-bell.html` — page sources (originally exported
  from their artifacts, now maintained here); the build swaps their storage code for the Drive layer and moves
  the sign-in control into the header. Dumbbell Dojo's muscle maps (which muscles each exercise and each day
  works, front and back) live in the source as `EXM` and `MM_SHAPES`.
- `seed/shortcuts.txt`, `seed/build-seed.mjs` — the starter shortcut set and its builder.
- `seed/ocr-notebook-to-csv.py` — turns OCR text of a scanned password notebook into the vault's import CSV.

## Getting existing passwords in

Never paste real passwords into a Claude conversation. Use Strongroom's own importers: **Settings → Paste a
list** (one account per line, or `Label: value` blocks) or **Settings → Import a file** (CSV from Chrome, Edge,
Bitwarden, 1Password, Excel, or this vault). Bank, Card, Wi-Fi and Identity types are inferred from labels; extra
fields hold account numbers, PINs, expiry dates and the like. For a scanned notebook, run local OCR and
`seed/ocr-notebook-to-csv.py`; only masked summaries are printed.

## Keys notation in Keycap Atlas

Join simultaneous keys with `+`, separate steps with a space: `Ctrl+Shift+P`, `Ctrl+K Ctrl+S`, `g i`, `Alt+H O I`.
`Win`, `Cmd`, `Opt`, `Super` are understood; on macOS entries they render as ⌘ ⌥ ⇧ ⌃. Switch the Add dialog to
*Command line* for things like `caffeinate -t 3600`; those render as a copyable command chip.

## The earlier Claude artifacts

The first versions of these pages were Claude artifacts using the artifact database. They still exist but are
superseded by this site; nothing new should be entered there. Their data was exported to `seed/export/`.
