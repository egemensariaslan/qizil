# The "Instrument" design system

The UI (`qizil ui`) and the standalone report (`qizil report`) share one visual
language, defined entirely in `src/qizil/ui/static/app.css`. This is what it is
and why, so additions stay coherent.

## The mix

Two influences, chosen because the content is measurement data that has to
read as evidence, not a marketing surface:

| influence | what it contributes |
| --- | --- |
| **Linear / Vercel / Grafana dark developer tools** | layered fills carry hierarchy before hairlines do, one signal accent, clean sans-serif section titles instead of tracked all-caps labels on every header, glow reserved for things that actually mean something (verified, primary action) |
| **Bento grid** | cells of varying span (5/7, 6/6, 12) so panel size tracks panel importance |

Dark is the canonical theme — this is a lab instrument. A light variant exists
with identical structure for printing, papers and PR comments; the toggle sits
in the masthead and remembers your choice in `localStorage`.

An earlier version of this system leaned harder on Swiss editorial
typography — numbered sections (`01`, `02`...), every header in tracked
uppercase monospace. It read as an *idea* about design more than an actual
tool: numbering sections nobody references by number, and shouting every
label at the same volume, are both classic tells of a UI designed to *look*
deliberate rather than to *be* legible. Removed; see the rules below for what
replaced each one.

## Rules

1. **Red is the brand and the cost signal.** *Qızıl* is Turkic for a sharp red,
   so `--accent` is red and it does double duty: it marks the optimized side
   (the "after" column, the active `-O`, the primary action) and it marks
   anything that costs you — a regression, a deleted line, a failed proof.
   One red family, disambiguated by context. Green is the only "this got
   better" colour.
2. **Category colour is reserved for gate semantics.** `--clifford`,
   `--tgate`, `--rot`, `--two`, `--meter` say what a gate *is*. Never reuse
   them as decoration — a reader must be able to trust that gold means T gate.
   (Gold is the other half of *qızıl*, which also names the metal.)
3. **Hierarchy comes from layered fills first, hairlines second.** Three
   surface steps — `--bg` (page) < `--bg-2` (card) < `--bg-3` (inset: nested
   code, diffs, the circuit stage, dropzone) — do the work that a lesser
   version of this system asked borders alone to do. A hairline still marks
   every edge, but it is reinforcement, not the only signal.
4. **A repeated element earns its repetition, or it collapses.** The
   rewrite trace groups consecutive identical-pass entries behind one
   `<details>` disclosure instead of a colored badge on every line (a pass
   commonly fires dozens of times in a row — see `groupTrace()` in
   `app.js`). Before/after metrics are a compact table, not two progress
   bars per row: `100 → 72 | −28%` said in one line what a stacked bar
   pair said in three, with less to look at for the same information.
5. **A neutral value is plain text, not an empty pill.** A delta chip
   (`.stat .d`, `.chg`) only gets its colored, bordered pill treatment when
   there is a real change to flag; "no change" renders as muted text with
   no wrapper. The pill should mean something, not decorate every stat
   whether it moved or not.
6. **Every number is monospace and tabular.** `font-variant-numeric:
   tabular-nums` is set on `body`; digits must align down a column.
7. **Motion is 150–650ms, ease-out, and never blocks reading.** Cells rise 12px
   on entry with a 45ms stagger. All of it collapses under
   `prefers-reduced-motion`.
8. **Nothing loads from the network.** No web fonts, no CDN, no icon library —
   a report has to render offline in ten years. Type is the system stack;
   symbols are Unicode or inline SVG (the dropzone icon is two `<path>`s,
   not an icon font).
9. **Every string must do work.** No taglines, no restating what a panel
   obviously shows, no status shown twice in two places. A label names a
   number, a caption carries a parameter or a count, an empty state explains
   why it is empty. If a sentence would survive being deleted, delete it.
   This applies to *interface chrome* too, not just copy: the "equivalent
   CLI command" readout reconstructs only the flags actually in effect for
   the run being shown (`data.verification !== null` decides whether
   `--verify` appears, not the checkbox's live state) — a command string
   that always shows the same flags regardless of what happened is a
   prop, not information.

## Tokens

All tokens live in `:root` and are re-declared in `:root[data-theme="light"]`.
Never hard-code a colour in a rule or in JS — the SVG circuit renderer styles
gates with `var(--<class>)` / `var(--<class>-bg)` so both themes work for free.

```
surfaces   --bg  --bg-2  --bg-3  --bg-4      page / card / inset / track
lines      --line  --line-2  --sheen         hairlines and the top-edge gradient
ink        --ink  --ink-2  --ink-3           primary / secondary / tertiary text
signal     --accent --accent-2 --accent-ink --accent-bg --accent-line --glow
state      --ok (green) --bad (= the accent red) --warn (amber) (+ -bg, -line)
gates      --clifford --tgate --rot --two --meter (+ -bg)
type       --sans  --mono
geometry   --r  --r-sm  --gap  --page  --ease
```

## Type scale

| role | size | family | tracking |
| --- | --- | --- | --- |
| hero stat | 27px / 600 | mono | −0.02em |
| masthead wordmark | 19px / 700 | sans | 0.18em |
| section title (`.cell-head h2`) | 14.5px / 640 | sans, title case | −0.005em |
| rail label (`.k`, `.fk`, `th`) | 9–9.5px | mono, uppercase | 0.14–0.16em |
| body | 14px | sans | normal |
| data | 11–13px | mono | normal |

Section titles are sans-serif and set in ordinary title case now, not
tracked uppercase monospace — the jump from a 9.5px rail label to a 14.5px
sans header already reads as a hierarchy change without needing every
header to also shout in caps.

## Layout

```
.masthead              wordmark + theme toggle, nothing else
.toolbar               one elevated card, two rows:
  toolbar-row            circuit / level / gate set / switches / dropzone
  toolbar-run            command readout + Optimize, aligned together

.canvas                12-column grid, 14px gutter, 1420px max
  .hero               span 12 — auto-fit stat strip, 1px internal rules
  .proof              span 12 — verdict banner
  .cell.s5 / .s7      metrics + resources
  .cell               span 12 — circuit
  .cell.s6 / .s6      trace + histogram
  .cell               span 12 — diff
```

Below 1120px every cell collapses to full width; the grid is the only
breakpoint the design needs. The command-string readout used to float alone
in the masthead, disconnected from the controls it described; it now sits in
the toolbar's second row, next to the button that runs it.

## Adding a panel

```js
var node = cell("Panel title", "right-aligned meta", 6);  // 6 = span
node.appendChild(/* content */);
app.appendChild(node);
```

`cell()` handles the entry-animation stagger and the span class. For a
before/after comparison, use `metricTable(list)` (a compact
metric/before/after/delta table — see `renderMetrics` for the shape of
`list`), not a custom bar chart; for a naturally repetitive list (one entry
per event), group consecutive same-kind entries the way `groupTrace()` does
before rendering, rather than repeating a badge per row. Pull every colour
from a token.
