# Athena console: design contract

Local prototype console, synthetic data in every screenshot. This file is the single description of how the console looks.
Its source is the athena-forge landing page (`athena-forge/frontend/src/pages/landing.css`, "the spectrum plate", and
`athena-forge/PRODUCT.md`, which retires the older dark-blue glow/glass/gradient look). Where the console needs something the
landing page does not have, it is listed under "Added for a tool" below and marked as a decision, not as a Forge rule.

## The rules

1. **A near-black plate, flat.** No gradient, glow, blur, glass or shadow. Square corners everywhere (the only round things are
   the checklist step markers and the save dot).
2. **One action colour.** Sodium yellow `#f5c400` is the main action on a screen, the focus ring, and "look here". A screen has
   one yellow button at a time wherever possible. Everything else is an outline.
3. **Colour carries meaning only.** Red-orange is a problem or something that deletes, green is done/ready, and the four kinds of
   data each own one hue (pictures `#f5c400`, thermal `#ff4b2b`, audio `#21d4c4`, tables `#9b7bff`). A status is always also a word
   or a mark (solid, dashed, hollow square), never a colour alone.
4. **Type.** Anybody (display: headings, buttons, 700-800) / Hanken Grotesk (text) / Martian Mono (labels, ids, numbers,
   table heads). Self-hosted, OFL-1.1. Text fields are 16 px at every width (iOS zoom).
5. **Rules, not boxes.** A 2 px strong rule under the page header and table heads, 1 px hairlines between things, panels have a
   1 px edge.
6. **One focus ring.** 3 px yellow, 3 px gap, on every control, on every surface.
7. **No hex colour outside `:root`** in `console.css`. The only exceptions are the box outlines, halos and number tags drawn over
   a photograph in `checkscreen.css` (fixed for legibility on any picture) and the inline `rgb()` of the dialog backdrop.

## Tokens (defined once, at the top of `console.css`)

| Token | Value | Use |
|---|---|---|
| `--bg` / `--panel` / `--panel-2` | `#09090a` / `#121214` / `#1a1a1d` | page and rail / a panel / a row, hover or card inside a panel |
| `--field` / `--stage` | `#0c0c0e` / `#050506` | inside a text field / behind a picture being marked |
| `--fg` / `--fg-2` / `--fg-3` | `#f2f0ea` / `#cbc9c1` / `#94938b` | text and strong rules / reading text / help text and labels |
| `--rule` / `--rule-2` / `--field-line` | 18% / 42% of `--fg`, `#8f8e86` | hairline / panel edge / edge of a field or quiet button |
| `--y` / `--y-ink` / `--y-tint` | `#f5c400` / `#09090a` / 12% yellow | action, focus / text on yellow and on `--fg` / "next" and "current" highlight |
| `--bad` / `--bad-line` / `--bad-tint` | `#ff8a6e` / `#ff4b2b` / 14% | problem text / problem edge and mark / problem background |
| `--ok` / `--ok-tint` | `#6fd19a` / 12% | done, ready |
| `--m-image` `--m-thermal` `--m-audio` `--m-tabular` | `#f5c400` `#ff4b2b` `#21d4c4` `#9b7bff` | the four kinds of data |

## Components

- **Button**, one system at the end of `console.css`: yellow fill = the main action; outline (2 px `--fg`) = anything else; red
  outline = removes or deletes; the small variants (30 px, 1 px) live in rows and cards; a switched-off main action is an outline
  with muted text, not dimmed yellow. Toggle/pressed = inverse (`--fg` fill, dark text).
- **Panel** `.panel`; **notice** (yellow tint, yellow edge); **problem** (red tint, red edge); **done** (green edge).
- **Table**: mono heads, 2 px rule under the heads, hairlines between rows, current row yellow tint.
- **Dialog**: panel edge, head with a 2 px rule, one yellow action.
- **Brand**: the Athena mark (`brand/athena-mark-96.png`, copied from athena-forge) and the word ATHENA in Anybody 900.

## Added for a tool (my decisions, not athena-forge rules)

- The console stays **dark only**: the landing page is dark and has no light variant. A light theme would be a second token set.
- Green (`--ok`) and the problem reds exist only because a tool needs "done" and "wrong"; the landing page has no such states.
- The Athena mark is silver and blue and is the one blue thing on screen. It is athena-forge's brand commitment, so it is kept; swap
  `.brand-mark` in `console.css` to change it everywhere.
- Error pages (403, 404, 500) and the "you may not open this" page share `templates/projects/message.html`.

## Checked (2026-10-17, Chromium unless stated)

axe-core (wcag2a, wcag2aa, wcag21aa, best-practice): 0 violations on sign-in, project page, the picture dialog, check-screen
builder, operator screen and Access, at 1440 px and 390 px. Browser smoke test: 142 checks, 0 failures. Not checked: contrast of
text drawn on a photograph, a screen reader, Safari.
