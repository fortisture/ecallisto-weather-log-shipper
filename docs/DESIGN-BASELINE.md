# Design baseline — v4.0.0

A record of how the site looked **before** the `ui-ux-pro-max` design pass,
so the current appearance is never lost and any change can be compared
against it rather than argued about from memory.

Tagged in git as **`design-v4-baseline`**.

## Restoring this look

If a later design change is not wanted, this brings the old one back
without touching any behaviour:

```bash
git checkout design-v4-baseline -- web/styles.css
```

Markup changed as well as CSS, restore both:

```bash
git checkout design-v4-baseline -- web/
```

Neither command touches `station/`, `tools/`, `data/` or `secrets/`, so
restoring the design cannot break the pipeline.

---

## What it looks like

A dark "instrument panel": flat near-black ground, hairline-bordered
panels, monospace for anything numeric, a single teal accent used
sparingly, and amber reserved for warnings.

### Colour tokens

| Token | Value | Used for |
|---|---|---|
| `--bg` | `#0A0E13` | page ground |
| `--panel` | `#111820` | panel surface |
| `--panel-raised` | `#151D27` | cells inside a panel |
| `--line` | `#263140` | panel borders |
| `--line-faint` | `#1A2229` | internal dividers, grid lines |
| `--text` | `#DCE4EC` | primary text, values |
| `--text-dim` | `#6B7A8B` | labels, body copy |
| `--text-faint` | `#45525F` | captions, axis ticks |
| `--signal` | `#4FD8C4` | accent: units, links, "up" state |
| `--signal-dim` | `#2A6D63` | active button fill, corner brackets |
| `--warn` | `#E8A855` | warnings, sunset marker, max values |

### Chart series palette

Validated with the `dataviz` skill's checker for colour-blind separation
and contrast against `#111820`:

| Colour | Value | Series |
|---|---|---|
| blue | `#3987e5` | humidity, LNA, sunrise |
| orange | `#d95926` | temperature, dew heater, sunset |
| green | `#199e70` | dew point, CALLISTO |
| yellow | `#c98500` | pressure, BME, daylight |
| grey (dashed) | `#8FA0B3` | median reference lines — annotation, not a series |

### Type

- **IBM Plex Mono** — all numbers, labels, tags, nav, axis ticks.
- **IBM Plex Sans** — body copy on the About page.
- Uppercase + `letter-spacing: 0.1–0.14em` for small labels.
- `font-variant-numeric: tabular-nums` in columns; proportional for large
  display figures.

### Recognisable details

- 4px rounded corners on panels.
- Decorative corner brackets (`.panel::before` / `::after`) in dim teal.
- Two faint radial gradients on `body` (teal top-left, amber bottom-right).
- Text-shadow glow on the hero reading.
- Pulsing status dot (disabled under `prefers-reduced-motion`).
- Teal/amber tinting on the Avg / Max stat tiles.
- 1px gaps between grid cells, revealing `--line-faint` as a hairline rule.

### History

An earlier pass removed the gradients, glow, corner brackets, rounded
corners and hover flourishes for a flatter look. It was rejected and
reverted — this baseline is the restored original, plus the components
added since (period selectors, overview cards, sun arc, station log).

---

## Page inventory at this baseline

| Page | Layout |
|---|---|
| `index.html` | Sun arc panel, 3 overview cards, stream-status strip |
| `weather.html` | Hero reading + stat tiles, 4 charts in a 2-up grid |
| `power.html` | Hero total draw, 4 rail cells, 4 current charts, voltage chart |
| `status.html` | Explanation, stream cells, 3 coverage charts, day heat-grid |
| `sun.html` | Sun arc, twilight strip, 2 year charts, method note |
| `fits.html` | Year/month/day selectors, sweep strip, annotated spectrogram |
| `about.html` | Counters, centred prose, people, gallery, station log |
