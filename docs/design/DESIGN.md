# Athena console: design contract

Local prototype console, synthetic data in every screenshot. This file is the single description of how the console looks and why.
It replaces the earlier dark "spectrum plate" contract (2026-10-17). The redesign plan (*Beginner-Friendly Product Redesign and
Development Task List*, 2026-10-30) asked for a calmer, lighter, easier-to-scan console for non-technical people who use it every
day. The Athena mark and name are kept; the palette, type and shape are new.

Reference for workflow and progressive disclosure: Roboflow's project-based flow (images, labels, annotation, dataset versions,
models, deployment). Athena keeps its own words, colours and review rules. Nothing here implies Athena has Roboflow's feature set.

## The rules

1. **A light, quiet page.** Cool off-white page (`--bg`), white panels (`--panel`), 1 px hairlines, 6-8 px corners, no gradient,
   glass or glow. The only shadows are on things that float over the page (dialogs, the message at the bottom).
2. **One action colour.** The blue of the Athena mark, darkened until white text on it passes 7:1 (`--accent`). It marks the main
   action, the focus ring, and "this is where you are" (current tab, current row, the next step). A part of the page has one
   blue button wherever possible; everything else is white with a grey edge.
3. **Colour carries meaning only.** Green = done or ready, red = a problem or something that deletes, amber = read this first (a
   limit, a warning, a suggestion that still needs a person). A status is always also a word or a mark (a ring, a dashed edge, a
   tick), never a colour alone.
4. **One type family.** Hanken Grotesk for everything: headings (650-700), labels, buttons (600), body (400). Sentence case
   everywhere: no upper-case labels, no letter-spacing, no display face. Martian Mono stays only for code, file names and log
   text. Text fields are 16 px at every width (iOS zoom).
5. **Hairlines inside a panel.** A panel has a 1 px edge and 20 px padding; inside it, hairlines and spacing separate things, and
   a card inside a panel is used only for something the person acts on (a step, a model, a snapshot).
6. **One focus ring.** 3 px blue, 3 px gap, on every control, on every surface.
7. **No hex colour outside `:root`** in `console.css`, `access.css` and `checkscreen.css`. The exceptions are the box outlines,
   halos and number tags drawn over a photograph in `checkscreen.css` (fixed for legibility on any picture), the scrollbar thumb,
   and the `rgb()` of the dialog backdrop and shadows.

## Tokens (defined once, at the top of `console.css`)

| Token | Value | Use |
|---|---|---|
| `--bg` / `--panel` / `--panel-2` | `#f5f6f8` / `#ffffff` / `#f1f3f6` | the page / a panel, a card, the rail / a row, a hover, a card inside a panel |
| `--field` / `--stage` | `#ffffff` / `#1b1f27` | inside a text field / behind a picture being marked |
| `--fg` / `--fg-2` / `--fg-3` | `#171c26` / `#3b4352` / `#5b6475` | text / reading text / help text and labels |
| `--rule` / `--rule-2` / `--field-line` | `#e1e5ec` / `#cdd3dd` / `#7c8596` | hairline / panel edge / edge of a field or quiet button |
| `--accent` / `--accent-hover` / `--on-accent` / `--accent-tint` | `#2447b8` / `#1c3a99` / `#ffffff` / `#e9eefc` | action, focus, current / hover / text on accent / the next step and the current row |
| `--ok` / `--ok-tint` | `#17693f` / `#e6f4ec` | done, ready |
| `--bad` / `--bad-line` / `--bad-tint` | `#b3261e` / `#d4382c` / `#fdecea` | problem text / problem edge and mark / problem background |
| `--warn` / `--warn-line` / `--warn-tint` | `#7a4b00` / `#d9a520` / `#fff6dc` | read-this-first text / edge and mark / background |
| `--m-image` `--m-thermal` `--m-audio` `--m-tabular` | `#2447b8` `#d4382c` `#0f766e` `#6d4fd1` | the four kinds of data |
| `--r-field` / `--r-card` / `--r-pill` | 6 px / 8 px / 999 px | buttons, fields, badges / panels, cards, rows (dialogs 12 px) / chips and step markers |

Contrast ratios (computed from the hex values, WCAG 2 formula): `--fg` 17.1:1 and `--fg-3` 6.0:1 on a panel; white on `--accent`
7.9:1; `--ok` 6.7:1 and `--bad` 6.5:1 on white; `--warn` 6.9:1 on `--warn-tint`; `--field-line` 3.1:1 on a panel.

## Components

- **Button**, one system near the end of `console.css`: blue fill = the main action; white with a grey edge = anything else; red
  text and edge = removes or deletes; the small variants (32 px) live in rows and cards; a switched-off main action is an outline
  with muted text, not a dimmed blue. A selected filter or tab is a pale blue fill with blue text.
- **Panel** `.panel`; **note** (amber tint and edge: `.gate-note`, `.dataset-note`, `.train-ack`, `.result-limits`); **problem**
  (red tint and edge: `.form-error`, `.dataset-problems`); **done** (green tint and edge).
- **Overview** (new, `#overview`): at the top of an image project, one `.next-step` card with the first step that is still to do
  (read from the same list as the checklist underneath, never computed separately) and one blue button, then a four-count strip
  (`.project-counts`: pictures, pictures to review, snapshots saved, approved models). A person without the needed role sees the
  same sentence the server would refuse with, and the button is switched off. The counts are fields of the project list API
  (`image_count`, `pending_pictures` (added with this redesign: pictures with at least one box still waiting), `dataset_count`,
  `approved_model_count`); nothing is estimated in the browser.
- **Table**: grey head row in the body face, hairlines between rows, current row pale blue.
- **Dialog**: white, 12 px corners, a head with a hairline, one blue action; the backdrop is translucent ink.
- **Project tabs** (`.jump`): quiet text tabs, the current one pale blue. Names are the existing plain-language ones (Overview,
  Labels, Pictures, Make a training set, Teach the computer, Check screens).
- **Brand**: the Athena mark (`brand/athena-mark-96.png`) and the name "Athena" in the body face.
- Error pages (403, 404, 500) and the "you may not open this" page share `templates/projects/message.html`.

## Decisions (mine, to be confirmed by the owner)

- **Light only for now.** The plan left light versus dark open. A dark theme would be a second token set under
  `:root[data-theme="dark"]`; it is not built because nothing in this round needs it and an unused theme rots. Revisit if
  operators work in dim rooms.
- **Names kept, not renamed to the plan's nouns.** The plan proposes Images / Dataset versions / Models. The console's own words
  (Pictures, snapshot, Teach the computer) were tried with a simulated non-technical user (`docs/dogfood/`) and many tests pin
  them. The mapping to the plan's areas is in `docs/design/journey-map.md`. Renaming is a copy decision, not a styling one.
- **The project list and the open project still share one page.** Splitting them into two routes is the larger structural step
  in the plan (Epic C); it is not done here.
- **Anybody (the old display face) is no longer loaded.** Its font files and licence note stay in `fonts/` until a licence pass
  removes them.
- **Check-screen box colours do not follow the page.** Yellow (found by the computer), teal (added by a person) and red-orange
  (the one being looked at) stay fixed so they read on any photograph.

## Checked

See `docs/dogfood/2026-10-30-light-theme-redesign.md` for what was measured (Chromium, synthetic data) and what was not.
