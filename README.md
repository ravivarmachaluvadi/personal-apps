# Personal Apps

Seven single-file web apps whose data lives as JSON files in **your own Google Drive**, not in any app's
database. Open them from a laptop or a phone, sign in with Google once per device, and every device sees the
same data. The **Continue with Google** button sits in the header of every page; until you tap it on a device,
that device keeps its data only in its own browser (the header then says *changes waiting*).

| App | Page | Drive file (in `My Drive/Personal Apps`) |
|---|---|---|
| Keycap Atlas | `docs/keycap-atlas.html` | `keycap-atlas.json` — shortcuts and commands, by app and OS |
| Strongroom | `docs/strongroom.html` | `strongroom-vault.json` — password vault, ciphertext only |
| Dumbbell Dojo | `docs/dumbbell-dojo.html` | `dumbbell-dojo.json` — ticks (timestamps), weight logs, plan choice, day edits |
| Tally Board | `docs/tally-board.html` | `tally-board.json` — activities, session log, plans, milestones, notes |
| Spine Bell | `docs/spine-bell.html` | `spine-bell.json` — per-device counters and the daily diary |
| Event Log | `docs/event-log.html` | `event-log.json` — event types, one-off and repeating events, settings |
| Hack Shelf | `docs/hack-shelf.html` | `hack-shelf.json` — entries (write-up, code, fields, tags), topics, usage, settings |

`docs/index.html` is a launcher for all seven. `docs/drive-sync.js` is the shared storage layer; `docs/config.js`
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

## Light and dark

Every page starts on **Auto** and follows the device. Where to switch:

| Page | Switch |
|---|---|
| Launcher, Keycap Atlas, Dumbbell Dojo, Tally Board, Spine Bell | Sun/moon button in the header |
| Strongroom | Sun/moon button in the header and on the lock screen, plus Settings → Appearance |
| Event Log, Hack Shelf | Settings → Appearance (Auto / Light / Dark), saved with the page's data |

The header buttons remember the choice **per page, per device** (`<page>.theme` in `localStorage`), and a small
script in `<head>` applies it before the first paint. Switching back to the theme the device already uses returns
the page to Auto, so it follows the device again.

Faint secondary text (`--faint`) is set per page to reach 4.5:1 contrast on every background in both themes, and
the two-letter badges use `--mg-ink` (white in light mode, near-black in dark mode, where the badge colours are
pastels). A page that can be forced light on a dark device must say `color-scheme:light` in its light block, or
the browser keeps drawing dark-scheme controls (Dumbbell Dojo's day names were white on white that way).

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

## Tally Board: the tick and the session log

A tick records **that** a day happened; the **Log** tab records **what** happened, so a gap of a few days can
still be filled in accurately later.

- **Log a session** takes a day (any day up to today), an activity, what you did, how long in minutes, how hard
  (Easy / Steady / Hard) and a free note. Picking the same day and activity again reloads what is stored, so the
  one form adds, edits and back-fills. Saving with every detail blank is still a plain tick.
- **+ Something else** names an activity that is not on the board yet and creates it with no target ("just
  tracking"), so an unplanned swim or gym class can be logged without reshaping the plan. A name that already
  exists is reused rather than duplicated.
- **Days with nothing logged** lists the last 14 days that carry no tick at all; tapping one loads it into the
  form. The Today tab shows the same nudge for the last 7 days, but deliberately keeps it out of the tab badge
  and the daily notification, which stay reserved for cadence targets and reminders.
- **Plans and sessions are separate records.** A calendar plan ("Badminton, 6pm") keeps its own done/cancelled
  state; the session records what was actually done. When they differ, both survive — the history shows
  *Planned: Badminton (did not happen)* above *Cricket — Sunday match*. The Today tab's past-plan row therefore
  offers a third button, **Did something else**, next to Done and Cancelled.
- **Where the details show up**: the activity card's *Last session* line and its four-week strip (a square with a
  dot has details on it; tapping any square opens that day in the Log), the calendar's day panel, and the month
  grid's tooltips.

### Data shape

Each activity carries the existing `dates: ["YYYY-MM-DD", …]` plus an optional
`log: { "YYYY-MM-DD": { what, mins, effort, note, at } }`. Every streak, percentage and total still reads
`dates` alone, so an activity saved before this change needs no migration and keeps working untouched.
Unticking a day also drops that day's `log` entry, so details never outlive the tick they belong to.

## Event Log: plan, tick, archive, look up

Tally Board answers *did a thing happen on a day*. Event Log answers *what is coming, what did I miss, and when
did that happen* — discrete dated events with a lifecycle. The two do not share data.

### Types

Every event belongs to a type you define yourself: name, 1–3 character code, colour, and how long its resolved
events stay in the active views. A type marked **log only** is for things you record after the fact (a test
result); its events are created already done and it cannot carry a repeat.

### The three states, and the one that is not stored

`planned`, `done` and `cancelled` are stored. **`missed` is not** — it is derived as
`state === 'planned' && date < today`. Nothing writes it, because the only writer a static page could have is
*whichever device happened to be open at midnight*, which would record which browser you opened rather than a
fact, and would churn the merge on every device for no information.

### Archiving replaces purging

Resolved and missed events drop out of Today, Calendar and Timeline once they are older than the archive
window (default 30 days, overridable per type and per event, `Never` allowed), and live on in **History**,
which is searchable by text, type, state and any from/to date range. **Nothing is ever deleted automatically.** Deleting is
always a button you press.

History lists **every** event by default — planned, done, missed and skipped — newest first (or oldest first),
25, 50 or 100 to a page, with the filters narrowing that list. Any filter change goes back to page 1. Search also
matches month names, so `september rent` works. The exports cover every page of the filtered list; the bulk
*Delete these N* only appears once a filter is on, because unfiltered it would mean every event in the file
(that stays behind *type DELETE* in Settings).

Archiving is derived the same way `missed` is: `isArchived()` is a date comparison evaluated inside each view's
filter, so changing the window is instant, free and reversible, and the backlog is never rewritten. Events you
still want in front of you carry a **Keep** pin, which overrides the window and shows them under *Kept here*.

### Repeats

`every N days`, `weekly on chosen weekdays`, `monthly on a day-of-month` (or the last day), and `yearly`. Each
occurrence is its own record with its own done-or-missed state, generated 180 days ahead by default. Where a
day does not exist — the 31st in November, 29 February in a common year — you choose **use the last day** or
**skip the month**; the rule dialog previews the next six dates from the same function the generator uses.

The correctness rule that makes repeats safe to edit:

```
occurrence id = 'o-' + ruleId + '-' + rule.rev + '-' + date
```

The id is deterministic, so two devices generating the same occurrence converge on one record instead of
duplicating it. Editing *when* a rule fires bumps `rev`, which tombstones only the **future, planned,
untouched, unpinned** occurrences of the old revision and generates the new revision under fresh ids. Anything
you already ticked, skipped or edited by hand is never touched — history is immutable. A rule you delete
leaves its history behind; a second button deletes that too.

### Reminders

Both switched **off** by default, in Settings.

- **Browser notifications** reuse `docs/sw.js`. One banner per day per device. They only fire while the page is
  open — a static page cannot wake itself in the background.
- **Calendar export** writes an `.ics` (one `VEVENT` per occurrence, never an `RRULE`, stable `UID` so
  re-importing updates rather than duplicates). Import it and your phone's own calendar owns the alarm, which
  is the only thing that rings with the phone in your pocket.

### Testing it without waiting real days

Settings → Developer → **Pretend today is**. Stored in `localStorage` only, never in Drive, with a banner while
it is on. Every view, the archive derivation and the generator all read `today()`, so one date change exercises
all three.

## Dumbbell Dojo notes (after the 2026-09-14 review)

- **Plans**: keys `ppl6`, `ul4`, `full3`, `bro5` are stored in Drive, so never rename them. `ppl6` (Push / Pull / Legs, each muscle
  twice a week) is the default; `ul4` is the 4-day Upper / Lower plan for sport or flare-up weeks; `full3` is a real full-body A / B / C;
  `bro5` is the six-session body-part split. The plan bar above the week strip switches between them from the Today tab.
- **Day edits** are stored per weekday as `{add, remove, swap}`; Swap keeps the replacement in the original's slot. "Sore back today"
  turns the day into the Spine Reset (remove everything, add the reset moves); "Reset Wednesday to default" (whichever
  day it is) undoes it, and keeps that day's ticks and weight logs.
- **Ticks** store a timestamp (not `true`) so the session summary can say how long the session took. Completion is recomputed
  whenever the list changes (`recomputeComplete`).
- **Backups**: the export is v2 (`items` with timestamps and tombstones). *Merge a backup* keeps whichever is newer per key;
  *Replace everything* makes the file the truth and tombstones what it lacks. Files from the other apps are refused.
- **Tombstones** in `drive-sync.js` are kept for ten years (`TOMBSTONE_DAYS`), so a device left closed for months cannot resurrect
  deletions.
- **Rest timer**: the countdown survives a reload (`sessionStorage`), "GO!" stays until tapped, a notification goes through `sw.js`
  when Alerts are enabled, and on Android a link hands the countdown to the Clock app. The bar follows a running rest onto other tabs.
- **Rendering**: All exercises, Back care and Do & Don't render on first visit. Today's 3D canvases are built at once (`mountAnims(root, true)`);
  everywhere else a card's canvas is built when it scrolls into view, and the animation loop stops when nothing is visible.
- **Do & Don't tab**: `DOS` holds the pairs and `figSVG` draws the side-view figures from a few joint coordinates.

## Hack Shelf: find it again, copy it, keep it fresh

Hack Shelf is for the things you learned or discovered and want to reuse: a command, a fix, a gotcha, a list of
facts. Keycap Atlas stays the place for one keystroke or one command per app and OS (with its drill); Strongroom
stays the place for real secrets.

### An entry

One card with a title and any mix of:

- a **write-up** — paragraphs, `- ` bullets, `1. ` numbered lines, `` `inline code` `` and `https://` links. That
  is the whole formatting set; anything else you type, HTML included, is shown as literal text;
- **code blocks**, each with a language and its own Copy button;
- **fields** — key → value rows, such as *Port → 5432* or *Source → a link*;
- **tags**, and exactly **one topic** (or none).

A collapsed card shows a one-line summary and a **Copy** that copies the first code block, or the first field
when there is no code. Tap the title for everything else: per-block and per-field Copy, Edit, Pin, Duplicate,
Copy as Markdown and Delete.

### Finding it again

| Way | How it works |
|---|---|
| Search | Every word must appear somewhere: title, write-up, code, languages, field keys, tags, topic name |
| Topics and tags | The rail on a laptop, a fold-out row on a phone. Tags combine (AND); a tag on a card filters too |
| Smart order (default) | Pinned first, then `(1 + uses) / (1 + days since last use ÷ 30)`, so frequent *and* recent wins |
| Revisit tab | Up to 5 entries you have not opened, copied or edited for 30 days, oldest first |

A **use** is a copy that actually reached the clipboard, or a click on a link in the entry. Opening a card is not a
use, but it does count as having looked at the entry. **Still useful** in Revisit puts an entry away for twice as
long each time (30 → 60 → 120 → 240 → 365 days); pinned entries never come back, because you see them already.
The batch is fixed when you open the tab, so copying from a card there does not make it vanish.

### Sensitive fields: hidden on screen, not encrypted

Tick **Sensitive** on a field and its value shows as dots until you tap Reveal (and hides again when the page goes
to the background). Copy works without revealing it. Sensitive values are left out of search, so a guess typed
into the search box cannot confirm a secret, and Copy as Markdown writes `(hidden)`. But the value is stored as
**plain text** in `hack-shelf.json`, like everything else in that file.

When a value looks like a real secret — a GitHub, Slack, AWS, Google or `sk-` key, a private key block, or a login
token (JWT) — the editor says so and points to Strongroom. It never stops you saving, and Settings can turn it off.

### Data shape, and why usage lives apart

Everything is a record in `items`, told apart by `kind`:

| kind | id | What it holds |
|---|---|---|
| `entry` | `e…` | title, `topicId`, tags, write-up, `blocks[{id, lang, code}]`, `fields[{id, k, v, secret}]` |
| `meta` | `m-<entry id>` | `pinned`, `uses`, `lastUsed`, `lastSeen`, `reviewedOn`, `interval` |
| `topic` | `t…` | name, colour 0–7 |
| `settings` | `settings` | theme, Revisit batch size and first wait, the secret warning on/off |

Drive sync keeps whichever copy of a record was saved last. Copies, pins and Revisit answers are frequent and
happen on whichever device is in your hand; if they stamped the entry itself, copying a snippet on the phone could
throw away an edit made on the laptop that had not uploaded yet. So they write only `meta`, and the entry changes
only when you edit it. The worst a race can cost is one use count or one pin, never an edit. The editor also warns
before saving over an entry that changed on another device since you opened it.

Topics are referenced by id, so renaming one is a single write. **No topic** is not stored: an entry has no topic
when its `topicId` is empty or points at a deleted topic. Deleting a topic is therefore one tombstone, and its
entries simply read as *No topic* without being rewritten.

### Backups and testing

Settings → **Export JSON** writes `hack-shelf-YYYY-MM-DD.json`. **Import** accepts only that file or the Drive
file itself; another app's file is refused by name. A merge keeps each record's own timestamp, so an old backup
never overwrites a newer edit; *replace* makes the file the truth and deletes what it lacks.

Settings → Developer → **Pretend today is** moves this browser's date (never Drive's), so Revisit can be watched
without waiting a month. Copies and reviews made while pretending are dated with the pretend day.

### The Guide tab

The third tab (or `?`) is the manual: what goes in each editor box, the write-up formatting, topic versus tag,
how Revisit decides and what its three answers do, finding and reusing, and sensitive values. The formatting
examples are drawn by the page's own renderer, so the cheat-sheet cannot drift from what the write-up box really
does. **Templates** — a command hack, a config snippet, a lesson learned, a fact sheet and a login with a
sensitive field — open the editor pre-filled; nothing is saved until you press Save, and a template's topic is
reused if one with that name exists, otherwise created on save. Reading the Guide writes nothing.

## Sign-in details worth knowing

- The Google token lasts one hour. After that the pages show *Continue with Google* again; one tap, no consent
  screen. Data entered while signed out is kept and uploaded after the tap.
- Sign out from the status pill at the top of any page.
- If a browser blocks the Google pop-up, allow pop-ups for the site once.

## Where things live

`docs/` is the only place the apps live now. `docs/river-rapids/` and the other game folders were copied in from
the old `Documents/FromClaude/FromClaude/Personal` folder and are linked from the launcher.

`archive/` is gitignored — it exists on disk only, never in the repo, and holds copies kept purely for reference:

- `archive/from-claude-2026-09-03/` — the 3 September Dumbbell Dojo, Spine Bell and Tally Board (artifact
  versions, no Drive sync).
- `archive/superseded/` — `dumbbell-dojo-artifact-2026-09-06.html` (the last artifact-database version, which
  used to sit in a tracked `artifacts/` folder) and `vroom-valley.v1.bak` (which used to be served publicly from
  `docs/vroom-valley/`). Nothing reads either; both remain recoverable from git history as well.

## Repository layout

- `docs/` — the site. `tools/build-site.py` regenerates the seven pages from the sources below; run it after
  editing any source. It leaves `docs/data/keycap-atlas.json` alone unless the gitignored `seed/export/` folder
  is present. `docs/sw.js` is the notification service worker (not built, edit in place).
- `keycap-atlas.html`, `strongroom.html` — page sources (artifact-style fragments; the build adds the storage layer
  and the HTML skeleton).
- `site/dumbbell-dojo.html`, `site/tally-board.html`, `site/spine-bell.html` — page sources (originally exported
  from their artifacts, now maintained here); the build swaps their storage code for the Drive layer and moves
  the sign-in control into the header. Dumbbell Dojo's muscle maps (which muscles each exercise and each day
  works, front and back) live in the source as `EXM` and `MM_SHAPES`.
- `site/event-log.html` — page source, hand-written and Drive-native: it calls `DriveStore` directly and falls
  back to browser-only storage when `drive-sync.js` is absent, so it opens straight from the filesystem. The
  build only injects the two script tags, then asserts the store is wired, that the page uses no `innerHTML`,
  and that no function is declared twice (a duplicate is hoisted over the first and wins silently).
- `site/hack-shelf.html` — page source, built exactly like Event Log. Because it renders text and code you typed,
  the build refuses every markup-string API (`innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`),
  where Event Log's build refuses only `innerHTML`.
- `seed/shortcuts.txt`, `seed/build-seed.mjs` — the starter shortcut set and its builder.
- `seed/ocr-notebook-to-csv.py` — turns OCR text of a scanned password notebook into the vault's import CSV.
- `seed/text-notebook-to-csv.py` — the same for a notebook already typed as plain text (blank line between
  entries). It reuses the OCR script's block parser, so the two stay in step.
- `tools/cdp.py` — drives an already-running Chrome (started with `--remote-debugging-port=9222`) through
  Playwright, for screenshotting a page while working on it. A dev convenience; the site does not need it.
- `seed/export/` — gitignored. The per-shortcut JSON exported from the old Keycap Atlas artifact; present it and
  `build-site.py` regenerates `docs/data/keycap-atlas.json`, absent it leaves that file alone.

## Getting existing passwords in

Never paste real passwords into a Claude conversation. Use Strongroom's own importers: **Settings → Paste a
list** (one account per line, or `Label: value` blocks) or **Settings → Import a file** (CSV from Chrome, Edge,
Bitwarden, 1Password, Excel, or this vault). Bank, Card, Wi-Fi and Identity types are inferred from labels; extra
fields hold account numbers, PINs, expiry dates and the like. For a scanned notebook, run local OCR and
`seed/ocr-notebook-to-csv.py`; only masked summaries are printed.

### Entry types

Login, Bank, Card, Wi-Fi, Secure note, Identity and Other are built in. Beyond those, the Type dropdown in an
entry ends with **+ Add a new type…**: pick it, name the type (up to 24 characters), and save. The name is
stored on the entry itself, so it is encrypted and syncs like the rest of the vault — there is no separate
list of types to keep in step. Every type in use then shows up in the left rail with its count, and in the Type
dropdown of every other entry.

Typing a name that already exists reuses it whatever the capitalisation, and typing a built-in name (“Card”)
maps back to the built-in. A custom type disappears on its own once no entry uses it. An import keeps an
unrecognised `type` or folder column as a custom type instead of dropping it to Login.

### A login used on more than one site

Some accounts sign in on two addresses with the same username and password — EPFO's Unified Portal and its
Passbook site, for example. Keep them as **one** entry (two entries would also flag the password as reused):
the main address goes in *Website*, and the other in an extra field. **+ Website** in the editor adds that
field for you, labelled *Other website*; rename it (“Passbook”). Any extra field whose value is a web address
— `https://…`, `www.…`, or a bare domain like `example.gov.in` — gets **Open** and **Copy** buttons in the
entry, exactly like *Website*, and search finds the entry by either address. A field marked secret never
turns into a link. It is an ordinary extra field, so nothing about the stored entry, the CSV export or the
import changed. Links only ever open as `http(s)://`; anything else gets `https://` in front.

### Light and dark

The sun/moon button in the header (and in the corner of the lock screen) switches between light and dark.
**Settings → Appearance** has *Auto / Light / Dark*; Auto follows the device's own setting and is the
default. The choice is kept in this browser (`sr.theme` in `localStorage`), not in the vault, because the lock
screen needs it before anything is decrypted — so each device keeps its own. A small script in `<head>`
applies it before the first paint, so the page never flashes the other theme.

### Keyboard

`/` jumps to search. **Enter** in the search box opens the top match, **↓** opens the next one and moves into
the list, and **↑ / ↓** anywhere else walk through the results. **Esc** closes the open entry first, then
clears the search. Rows also have one-click copy buttons for the username and the password (shown on hover
on a laptop, always on a phone); a copy button turns into a tick for a moment when the copy worked.

The rail remembers the last view between visits, with one exception: unlocking always leaves a health view
(*Weak*, *Reused*, *Older than a year*) for *Everything*, because a short health list right after unlocking looks
like missing entries. A type view such as *Login* is a deliberate choice and stays.

## Adding an app in Keycap Atlas

**+ New app** beside the *Apps* heading opens the Add dialog with the app set to *+ New app…* and a name box. The
same choice sits at the bottom of the App list when adding or editing any shortcut, so a shortcut can also be moved
into a new app. An app is only the name on its shortcuts: it appears with the first shortcut you save under it and
leaves the list when its last one is deleted or moved. A new name that differs only in case or spacing from an
existing app (*claude code* for *Claude Code*) files under the existing one instead of making a twin.

## Keys notation in Keycap Atlas

Join simultaneous keys with `+`, separate steps with a space: `Ctrl+Shift+P`, `Ctrl+K Ctrl+S`, `g i`, `Alt+H O I`.
`Win`, `Cmd`, `Opt`, `Super` are understood; on macOS entries they render as ⌘ ⌥ ⇧ ⌃. Switch the Add dialog to
*Command line* for things like `caffeinate -t 3600`; those render as a copyable command chip.

## The earlier Claude artifacts

The first versions of these pages were Claude artifacts using the artifact database. They still exist but are
superseded by this site; nothing new should be entered there. Their data was exported to `seed/export/`.
