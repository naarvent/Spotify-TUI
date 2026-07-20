# Table columns fill the panel width

Date: 2026-07-20
Status: approved

## Problem

Result tables leave an unused gap on the right of the panel. Two causes:

1. `TablesMixin._FLEX_MAX` (`spt_tui/app/tables.py`) caps Title/Artist/Album at
   42/28/26. On a wide terminal the flexible columns hit their cap and the
   remaining space is deliberately left unused ("leftover stays unused").
2. `_column_widths` reserves 2 columns for a vertical scrollbar unconditionally.

A second problem sits at the other end of the range: below roughly 90 columns
every flexible column is squeezed to its 6-character floor, which is unreadable.

Column geometry is also duplicated: each table hardcodes its fixed widths by
column *index* (`{0: 3, 4: 9, 5: 12}` for tracks, `{0: 3, 5: 9}` for queue,
`{0: 3, 1: 7, 5: 9}` for search), so the same field has a different definition
per table and nothing can reason about a column by name.

## Goals

- No gap on the right at any terminal width.
- Heart, Duration, Added and the other identity columns keep a fixed width.
- Below a usable width, low-priority columns are dropped rather than squeezed.
- A horizontal scrollbar must never appear.

## Design

### Column catalogue

One field-name-keyed catalogue in `TablesMixin` replaces the per-table
index maps:

```python
_FIXED_W    = {"heart": 3, "num": 3, "mark": 3, "type": 7, "dur": 9,
               "added": 12, "device_type": 14}
_FLEX_WEIGHT = {"title": 1.4}                       # default 1.0
_FLEX_MIN   = {"title": 16, "artist": 12, "album": 12, "source": 12}  # default 10
_DROP_ORDER = ("source", "added", "album", "artist")
```

Every table declares its columns as a list of field names; labels stay per
profile because the same field is called "Artist" in one view and
"Artist/Owner" in another.

### Fitting algorithm

`_fit_columns(fields, labels)` returns `(kept_fields, kept_labels, widths)`:

1. Read the right panel's content width; fall back to the screen width.
2. Loop: with the current kept set, compute
   `overhead = 4 + 2 * n` (round border + per-column cell padding + a permanent
   2-column vertical-scrollbar reserve) and `avail = panel_width - overhead`.
   If the flexible columns cannot all reach their `_FLEX_MIN`, drop the first
   field of `_DROP_ORDER` still present and repeat.
3. Give each flexible column its `_FLEX_MIN` as a base, then split
   `extra = avail - fixed_total - sum(bases)` in proportion to `_FLEX_WEIGHT`.
   The rounding remainder goes to the highest-weight flexible column, so
   `sum(widths) == avail` exactly.
4. If even the bases do not fit (absurdly narrow terminal), scale them down
   proportionally with a hard floor of 4.

Fixed columns are never scaled and never dropped except `added` (and `source`),
which are in `_DROP_ORDER`.

### Scrollbar reserve

The 2-column vertical-scrollbar reserve stays unconditional. Making it depend
on the current row count would recover 2 columns but reintroduces a horizontal
scrollbar the moment a list grows past the viewport, which is explicitly
unwanted.

### Resize

`_recompute_table_widths` currently only rewrites widths on existing columns. It
must now also handle a change in the kept set: rebuild the columns
(`clear(columns=True)` + `add_column`), update the table's field list, and
repaint from `_model_rows`, which preserves cursor and scroll. A widened
terminal therefore brings a dropped column back.

### Affected tables

| Table | Fields |
| --- | --- |
| tracks (playlist / recent) | heart, title, artist, album, dur, added |
| tracks (album) | heart, title, artist, dur |
| search (5 layouts) | saved, type, title, artist, album, dur |
| queue | num, heart, title, artist, album, dur, source |
| devices | mark, title, device_type |

The queue table builds its cells positionally today; it becomes field-driven so
dropping a column stays consistent with the header.

## Testing

New `tests/test_table_fill.py`:

- rendered widths plus overhead equal the panel width at 200/160/120/100
  columns, for tracks, search and queue; no horizontal scrollbar at any of them
- Title stays wider than Artist, ratio between 1.2 and 1.6
- heart / dur / added are exactly 3 / 9 / 12 at every width
- Added drops first, then Album, then Artist; heart, Title and Duration always
  survive
- widening by resize brings a dropped column back, preserving cursor and scroll

`tests/test_responsive_tables.py::test_flex_columns_capped_on_wide_terminals`
asserts the old 42/28/26 caps and contradicts this design; it is replaced by a
fill assertion.
