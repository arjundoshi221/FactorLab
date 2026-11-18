---
name: FactorLab Hub
description: A dark, dense trading terminal for owner-operators. Every number is traceable, and every gap is labelled.
colors:
  terminal-amber: "#ffa028"
  amber-ink: "#160d00"
  bg: "#06080b"
  panel-soft: "#090c0f"
  panel: "#0c0f13"
  raised: "#11151a"
  hover: "#151a21"
  press: "#1b2129"
  line: "#1a2027"
  line-2: "#262d36"
  line-3: "#38414b"
  field-line: "#58616c"
  ink: "#e6e9ec"
  ink-2: "#c4cad0"
  ink-3: "#9ba4ac"
  muted: "#85909a"
  faint: "#77818b"
  up: "#22b573"
  down: "#e5484d"
  flat: "#6c7680"
  good: "#22b573"
  warn: "#f2b33d"
  bad: "#e5484d"
  info: "#4c9bf0"
  series-1: "#3987e5"
  series-2: "#d95926"
  series-3: "#199e70"
  series-4: "#c98500"
  series-5: "#d55181"
  series-6: "#008300"
  series-7: "#9085e9"
  series-8: "#e66767"
  grid: "#1c232b"
  axis: "#76818b"
typography:
  display:
    fontFamily: "JetBrains Mono, IBM Plex Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "20px"
    fontWeight: 600
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    letterSpacing: "-0.005em"
  title:
    fontFamily: "JetBrains Mono, IBM Plex Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "10px"
    fontWeight: 600
    letterSpacing: "0.08em"
  body:
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.45
  data:
    fontFamily: "JetBrains Mono, IBM Plex Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "11px"
    fontWeight: 500
    fontFeature: "tnum"
  label:
    fontFamily: "JetBrains Mono, IBM Plex Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "9px"
    fontWeight: 600
    letterSpacing: "0.08em"
rounded:
  none: "0px"
  dot: "50%"
spacing:
  gap: "8px"
  gutter: "16px"
  gutter-phone: "12px"
  row: "26px"
  control: "28px"
  status: "22px"
  tape: "26px"
  subbar: "30px"
  bar: "36px"
components:
  button:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0 11px"
    height: "{spacing.control}"
  button-hover:
    backgroundColor: "{colors.hover}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.none}"
  segmented:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.muted}"
    rounded: "{rounded.none}"
    height: "{spacing.control}"
    padding: "2px"
  segmented-active:
    backgroundColor: "{colors.terminal-amber}"
    textColor: "{colors.amber-ink}"
  input:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    height: "{spacing.control}"
    padding: "0 8px"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink-3}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    height: "18px"
    padding: "0 6px"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.none}"
  panel-head:
    backgroundColor: "{colors.panel-soft}"
    textColor: "{colors.ink-2}"
    typography: "{typography.title}"
    height: "28px"
    padding: "0 4px 0 10px"
  code-badge:
    backgroundColor: "{colors.terminal-amber}"
    textColor: "{colors.amber-ink}"
    padding: "3px 6px"
    rounded: "{rounded.none}"
  section-tab-active:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.terminal-amber}"
    height: "{spacing.bar}"
    padding: "0 16px"
---

# Design System: FactorLab Hub

## Overview

**Creative North Star: "The Night Desk"**

The hub is an operator's desk at the start of a trading session. The room is dark, the screens are dense, and one warm light marks where to look. Surfaces are stacked near-blacks separated by hairlines. Type is small, numbers are monospaced and tabular, and the amber accent marks only the active thing: the current section, the command prompt, a page code, the selected segment. Everything else is grey until data gives it a colour.

Density is the point. Rows are 26px, controls 28px, and labels are 9–10px mono uppercase with wide tracking. Chrome is a stack of fixed bars: section bar, sub-tabs, quote tape, and a status line pinned to the bottom. Pages are grids of square panels with no gaps wasted. Colour means something. Green and red mean market direction or status, and they always travel with ▲/▼, a sign or a written label. A grey em dash means the value is unavailable.

The Portfolio app (`/portfolio*`) is a deliberate variant of this system, not a separate one. It redefines the same role tokens under `[data-pf-theme]` (navy dark and a light mode, periwinkle/blue accent, serif headings, rounded cards, roomier rows). Shared rules and charts re-theme through roles alone. See **Portfolio variant** under Components.

**Key Characteristics:**
- Near-black tonal stack (`bg` → `press`) with 1px hairlines; no shadows at rest.
- One warm accent, Terminal Amber, used for focus, selection and identity only.
- Mono uppercase micro-labels over tabular mono figures; Inter for prose and page titles.
- Square corners everywhere; the only round shapes are status dots.
- Colour carries meaning (direction, status, series) and is never the only signal.
- Fixed chrome: 36px section bar, 30px sub-tabs, 26px tape, 22px status line.

## Colors

Cool near-blacks and greys, one warm amber, and a reserved set of meaning colours.

### Primary
- **Terminal Amber** (`terminal-amber`): the hub's one accent. It marks the active section tab and its 2px underline, the active sub-tab underline and code, the active segmented button (filled, with **Amber Ink** text), page and panel codes, the command-bar prompt, keyboard focus outlines, text selection, and the last-price marker on charts. **Amber Ink** (`amber-ink`) is the near-black text used on amber fills.

### Neutral
- **Desk Black** (`bg`): the page background, input wells, the tape and treemap grounds.
- **Panel ladder** (`panel-soft` → `panel` → `raised` → `hover` → `press`): each step is slightly lighter. Chrome bars and panel headers sit on `panel-soft`, panel bodies on `panel`, buttons, tooltips and popovers on `raised`, hover on `hover`, pressed and inline count badges on `press`.
- **Hairlines** (`line`, `line-2`, `line-3`): `line` separates panels, rows and chrome; `line-2` outlines controls and inputs; `line-3` marks hover outlines, popover borders, chart reference lines and the zero axis. **Field Line** (`field-line`) is the boundary of text fields and selects, the one border held to 3:1 against `bg` and `panel` so a field reads as a field.
- **Ink ladder** (`ink` → `ink-2` → `ink-3` → `muted` → `faint`): `ink` for values and titles, `ink-2` for table cells and panel headings, `ink-3` for secondary chips and flat numbers, `muted` for labels and inactive tabs, `faint` for footnotes, empty states and absent dots.

### Meaning colours
- **Up / Down / Flat** (`up`, `down`, `flat`): market direction only. Used for signed numbers, sparklines, candles (up candles are hollow), signed bars and heatmaps.
- **Good / Warn / Bad / Info** (`good`, `warn`, `bad`, `info`): status only. They appear as a dot, a chip outline or a 12% tint, always with a word next to them. Every status and direction colour also has `-soft` tint and `-line` border mixes (`color-mix` at 12–14% and 38%) defined in `src/theme/tokens.css`.
- **Series 1–8** (`series-1`…`series-8`): categorical chart slots in fixed order, validated against `panel`. **Grid** and **Axis** (`grid`, `axis`) draw chart scaffolding.

### Named Rules
**The One Lamp Rule.** Terminal Amber marks only what is active, focused or identifying (section, selection, prompt, page code). It is never used for data, status or decoration. If two amber things compete on a screen, one of them is wrong.

**The Never-Alone Rule.** Green, red and amber status never appear without a second signal: ▲/▼, a sign, a status word or a hollow-versus-filled shape. Up/down sits in the colour-vision-deficiency warning band, so a signal carried by colour alone counts as a bug.

**The Roles-Not-Hex Rule.** Page CSS references role tokens (`var(--panel)`, `var(--up)`) and never raw hex. A new colour is added to `tokens.css` first, and to the Portfolio theme's light and dark blocks if Portfolio uses it.

## Typography

**Display / Data / Label Font:** JetBrains Mono (with IBM Plex Mono, SFMono-Regular, Consolas, Liberation Mono, monospace)
**Body / Headline Font:** Inter (with Segoe UI, system-ui, -apple-system, sans-serif)

**Character:** A monospace carries every number, code and label, so figures align and read like a readout. Inter carries the parts meant to be read as sentences: page titles, table prose and explanations. Neither font is bundled (the CSP allows only `'self'`), so both resolve to the installed fallback, which in practice is often Segoe UI and Consolas. Design must hold up in the fallbacks.

### Hierarchy
- **Display** (mono 600, 20px, −0.02em, tabular): KPI values on metric cards. 13px mono 600 is the header-stat size.
- **Headline** (Inter 600, 15px, −0.005em): the one page title in the header strip, beside its amber page code. There is no larger type in the terminal.
- **Title** (mono 600, 10px, 0.08em, uppercase): panel headings and chrome section tabs (11px, 0.12em).
- **Body** (Inter 400, 12px/1.45): base text, sub-tabs (11px 500), page descriptions (11px, muted, max ~900px).
- **Data** (mono 500, 11px, tabular): table values and codes. Table cells use Inter 11px with mono for figures (`.t-num`).
- **Label** (mono 500–600, 9px, 0.06–0.08em, uppercase): column headers, stat labels, chips, status pills and the status line.

### Named Rules
**The Tabular Rule.** Every figure that could sit beside another figure uses the mono face with `font-variant-numeric: tabular-nums` and `white-space: nowrap`. A proportional number in a column is a bug.

**The Ceiling Rule.** Terminal type tops out at 20px for numbers and 15px for words. Hierarchy comes from case, tracking, weight and the ink ladder, not from size.

**The Seven Steps Rule.** Terminal CSS sets every font size through the role scale in `src/theme/tokens.css`: `--fs-2xs` 9px labels, `--fs-xs` 10px meta, `--fs-s` 11px cells, `--fs-m` 12px body, `--fs-l` 13px stats and card heads, `--fs-xl` 15px page titles, `--fs-2xl` 20px KPIs. A literal px font size in terminal CSS is a bug, and so is a size between steps.

## Layout

Full-bleed and dense. Content sits in `main`, up to 2200px wide, with 16px side gutters (12px under 620px). The terminal owns the gutters. The chrome is sticky: the section bar (36px) and sub-tabs (30px) stick together, and page toolbars stick below them at `--sticky`. The quote tape (26px) scrolls with the page and pauses on hover. The status line (22px) is fixed to the bottom, and the body reserves room for it.

Pages are grids of panels with an 8px gap (`t-grid`, `t-grid--2` for halves). The page header strip is at least 44px tall, carries a bottom hairline and is laid out as: code + title + ⓘ, then stats separated by left hairlines, then tools pushed right. Rows are 26px and controls 28px. Table cells are padded 6px × 10px. Tables keep a 960px minimum width and scroll inside their panel instead of squeezing.

Responsive steps: at 1100px the second exchange clock goes. At 900px panel halves stack, clocks hide and the command bar shrinks to an icon. At 620px the gutters tighten, the brand word hides and section tabs compact. Under `prefers-reduced-motion` the tape stops animating and becomes a scrollable strip.

## Elevation & Depth

Flat. Depth comes from the tonal ladder and hairlines, never from shadows at rest. A panel is a `panel` fill with a 1px `line` border and a `panel-soft` header. Hover lifts a surface one step on the ladder, and buttons use an inset 1px ring instead of a border. The only shadow is for things that float above the page.

### Shadow Vocabulary
- **Overlay** (`box-shadow: 0 16px 40px rgba(0,0,0,.55)`, token `--shadow`): the command palette, ⓘ info popovers and menus. The palette also dims the page behind it (`rgba(2,3,5,.66)` plus a 3px blur).
- **Status halo** (`box-shadow: 0 0 0 3px <status>-soft`): the soft ring around a live status dot or an open-market clock dot. It signals a light being on; it is not elevation.

### Named Rules
**The Flat Desk Rule.** If it does not float above the page, it does not cast a shadow. Separate things with a hairline or a ladder step.

## Shapes

Square. Every panel, button, input, chip, segmented control, tooltip and popover has 0px corners, and third-party widgets (React Flow controls, minimap) are forced square too. Circles are reserved for status lights: 6px dots in pills and clocks, 8px for the health light, 16px for the ⓘ button. Active states are inset rules, not outlines: a 2px amber underline on tabs and a 2px amber left bar on the active palette row. Notices have a 2px left border in their status colour.

## Components

### Buttons
Quiet, recessed and square. A button should read as a key, not a call to action.
- **Shape:** square (0px), 28px tall, 11px Inter 600.
- **Default:** `raised` fill, `ink` text, inset 1px `line-2` ring. On hover the fill moves to `hover` and the ring to `line-3`. Disabled is 45% opacity with a not-allowed cursor.
- **Secondary:** transparent with `ink-2` text and a 1px `line` border (`faint` on hover). Used for Retry, Close and minor actions.
- **Focus:** 1px Terminal Amber outline, 2px offset, on every interactive element.

### Segmented controls
- **Style:** a `bg` well with a 1px `line-2` border and 2px inner padding. Options are 10px mono 600 with `muted` text.
- **State:** the active option is filled Terminal Amber with Amber Ink text. Used for ranges (1D/5D/1M…), modes and toggles.

### Chips and status pills
- **Chip:** 18px tall, square, 1px `line-2` outline, 9px mono uppercase in `ink-3`. Status chips (`--ok`, `--warn`, `--critical`) switch to that status's text colour, `-line` border and `-soft` fill.
- **Status pill:** a 6px dot plus a 9px mono uppercase word, no box. The healthy dot has a soft halo. The labels are fixed words: Healthy, Needs attention, Missing, No daily rule, No producer yet, Unknown.

### Panels
- **Corner Style:** square.
- **Background:** `panel` body, `panel-soft` header (28px) with an amber mono code, an uppercase mono title in `ink-2`, and tools on the right. An optional ↗ open link goes amber on hover.
- **Border:** 1px `line`. No shadow.
- **Internal Padding:** 8px × 10px. The footer is 9px mono in `muted` above a hairline.

### Inputs / Fields
- **Style:** a `bg` well, 1px `field-line` border (3:1), square, 28px tall, 11–12px text, dark `color-scheme`.
- **Focus:** the global 1px amber outline with a 2px offset; the border may also turn amber, but the outline is never removed.
- **Labels:** 9px mono uppercase in `muted`, inline beside the field in toolbars.

### Navigation
- **Section bar:** HOME · DATA · PORTFOLIO · OPS, 11px mono 600 uppercase with 0.12em tracking, split by hairlines. Inactive tabs are `muted` (`ink` on a `hover` fill when hovered). The active tab is amber text on `panel` with a 2px amber inset underline.
- **Sub-tabs:** 11px Inter 500 in `ink-3`, each with a small mono code. Active is `ink` text with an amber underline and an amber code. The row scrolls horizontally with the scrollbar hidden.
- **Command bar:** a `bg` well with an amber `>` prompt, a hint in `muted` and `kbd` keycaps. Ctrl+K or `/` opens the palette.
- **Status line:** fixed bottom strip, 9px mono uppercase, segments split by hairlines, values bold amber.

### Page header strip (signature)
An amber code badge (Amber Ink on Terminal Amber, 10px mono 800, 0.08em), the 15px Inter title and an ⓘ disclosure. Then header stats: a 9px mono uppercase label over a 13px tabular value, tinted up/down/warn/accent by meaning. Tools and a `muted` mono "as of" stamp sit on the right.

### Quote tape (signature)
A 26px strip of index and futures quotes scrolling left, edges faded with a mask, paused on hover. Each item is 10px mono with a bold `ink` symbol and a signed coloured change. Stale items drop to 50% opacity. The tape is static under reduced motion.

### Charts
Hand-rolled SVG that measures its own width, so text renders at 1:1. Lines are 1.4–1.6px, with area fills at roughly 10% opacity of the tone. Reference and crosshair lines are dashed `line-3`, and axis text is 9px mono in `axis`. Tooltips sit on a 92% `raised` fill with a `line-2` border. Up candles are hollow (`panel` fill with an `up` stroke) and down candles filled. Treemap tiles are separated by a 1px `bg` outline, and the selected tile gets a 2px amber outline. Empty charts show a dashed `line-2` box with a `faint` mono note. Single-series bars and inline magnitude bars (table size bars) are neutral `ink-3`; amber never fills data.

### Portfolio variant
`[data-pf-theme="dark" | "light"]` keeps every role name and changes the values and the shape of the system:
- **Palette:** navy dark (`bg #0a0f1a`, `panel #111a2a`, accent periwinkle `#8ea8ff`) or a light mode (`bg #f3f5f9`, `panel #fff`, accent `#3b5bdb`, darker up/down/warn for contrast). Chart slots use the reference palette's light steps in light mode.
- **Type:** headings use a serif (Iowan Old Style, Palatino, Georgia). The page title is 30px serif 600 with an accent uppercase eyebrow. Figures switch from mono to Inter with tabular digits, and Portfolio-only CSS sets its own larger sizes (13px base). Portfolio does not redefine the `--fs-*` scale, so shared terminal components (tables, pills, notices) keep terminal sizes inside it.
- **Shape and depth:** cards are rounded (10px, 6px for small parts), and segmented controls and chip buttons are pills (999px). Cards may take the soft `--shadow` and a faint top glow in dark mode. The header bar is 60px with a translucent blur.
- **Focus:** 2px accent outline, 2px offset.
- The terminal's colour-meaning rules (Never-Alone, Tabular, unavailable shown as an em dash) apply unchanged.

## Do's and Don'ts

### Do:
- **Do** reference role tokens from `src/theme/tokens.css` (`var(--panel)`, `var(--up)`, `var(--fs-s)`) in page CSS, and add a new role there before using it.
- **Do** keep terminal surfaces square (0px) and flat, separated by 1px `line` hairlines.
- **Do** set every comparable figure in the mono face with tabular numerals and no wrapping (`.t-num`).
- **Do** pair every up/down or status colour with ▲/▼, a sign or a status word, and draw up candles hollow.
- **Do** show unavailable values as a `faint`/`muted` em dash or "?", and mute stale rows or tape items rather than hiding them.
- **Do** label with 9–10px mono uppercase at 0.06–0.12em tracking, in `muted`.
- **Do** give every interactive element the visible amber focus outline.

### Don't:
- **Don't** use raw hex in page CSS. The `--lime`, `--green`, `--amber`, `--red` and `--blue` aliases exist only for older rules; new code uses the role names.
- **Don't** use Terminal Amber for data, status or decoration. It marks active, focused and identifying elements only.
- **Don't** add rounded corners, pills or resting shadows to terminal pages. Those belong to the Portfolio variant.
- **Don't** exceed 20px type in the terminal, or set a terminal page title larger than 15px.
- **Don't** load third-party fonts, scripts or icon CDNs. The CSP is `default-src 'self'`.
- **Don't** show a missing value as 0 or colour it green.
