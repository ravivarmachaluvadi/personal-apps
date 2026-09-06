# Personal Apps

Three single-file web apps whose data lives as JSON files in **your own Google Drive**, not in any app's
database. Open them from a laptop or a phone, sign in with Google once per device, and every device sees the
same data.

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

## One-time setup

### 1. Host the `docs/` folder (GitHub Pages)

Google sign-in needs an `https://` origin (or `http://localhost`). The simplest free host is GitHub Pages.

```powershell
cd "C:\Users\Ravi Varma Chaluvadi\Documents\Projects\PersonalPasswordManager"
gh repo create personal-apps --public --source=. --remote=origin --push
gh api -X POST repos/ravivarmachaluvadi/personal-apps/pages -f "source[branch]=main" -f "source[path]=/docs"
```

After a minute the site is at `https://ravivarmachaluvadi.github.io/personal-apps/`. (A private repo needs GitHub
Pro for Pages; the repo holds no secrets, so public is fine. `.gitignore` keeps the CSV and PDF out.)

For local testing on the laptop: `python -m http.server 8000 --directory docs` and open
`http://localhost:8000/`.

### 2. Create the Google OAuth client (about ten minutes)

1. Open https://console.cloud.google.com/ and create a project, for example **Personal Apps**.
2. **APIs & Services → Library** → search **Google Drive API** → Enable.
3. **APIs & Services → OAuth consent screen** → External → fill in the app name and your email → Scopes: add
   `.../auth/drive.file`, `openid`, `email` → Test users: add your own Gmail address → Save.
   When it works, press **Publish app** so the consent does not expire every seven days. The `drive.file` scope
   does not need Google's verification.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → Application type **Web application**
   → Authorised JavaScript origins: add `https://ravivarmachaluvadi.github.io` and `http://localhost:8000`.
   No redirect URI is needed. Create, then copy the **Client ID** (ends in `.apps.googleusercontent.com`).
5. Paste it into `docs/config.js`:

   ```js
   googleClientId: '1234567890-abc.apps.googleusercontent.com',
   ```

   Commit and push. Reload the site: every page now shows **Continue with Google**.

### 3. First run

- **Keycap Atlas**: press *Load the starter set* to add the 250 shortcuts and the `caffeinate` commands, or import
  `seed/shortcuts.json`.
- **Strongroom**: choose a master password, then Settings → Import a file → `vault-import.csv` (delete the CSV
  and the source PDF afterwards).
- **Dumbbell Dojo**: Guide tab → *Import a backup* if you exported data from the old page; otherwise just start.

On a phone: open the site, sign in, and use *Add to Home Screen*.

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

- `docs/` — the site. `tools/build-site.py` regenerates the three pages from the sources below.
- `keycap-atlas.html`, `strongroom.html` — page sources (artifact-style fragments; the build adds the storage layer
  and the HTML skeleton).
- `site/dumbbell-dojo.html`, `site/tally-board.html`, `site/spine-bell.html` — page sources as exported from their
  artifacts; the build swaps their storage code for the Drive layer.
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
