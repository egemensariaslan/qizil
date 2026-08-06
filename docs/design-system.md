# The "Instrument" design system

The UI (`qizil ui`) and the standalone report (`qizil report`) share one visual
language, defined entirely in `src/qizil/ui/static/app.css`. This is what it is
and why, so additions stay coherent.

## The mix

Three influences, chosen because the content is measurement data that has to
read as evidence:

| influence | what it contributes |
| --- | --- |
| **Linear / Vercel dark minimalism** | near-black surface, 1px hairline separation instead of shadows, a single signal accent |
| **Swiss editorial typography** | numbered sections, small-caps rails with wide tracking, a strict 12-column grid, tabular figures, information ordered by hierarchy rather than decoration |
| **Bento grid** | cells of varying span (5/7, 6/6, 12) so panel size tracks panel importance |

Dark is the canonical theme — this is a lab instrument. A light variant exists
with identical structure for printing, papers and PR comments; the toggle sits
in the masthead and remembers your choice in `localStorage`.

## Rules

1. **Red is the brand and the cost signal.** *Qızıl* is Turkic for a sharp red,
   so `--accent` is red and it does double duty: it marks the optimized side
   (the "after" bar, the active `-O`, section numbers) and it marks anything
   that costs you — a regression, a deleted line, a failed proof. One red
   family, disambiguated by context. Green is the only "this got better"
   colour.
2. **Category colour is reserved for gate semantics.** `--clifford`,
   `--tgate`, `--rot`, `--two`, `--meter` say what a gate *is*. Never reuse
   them as decoration — a reader must be able to trust that gold means T gate.
   (Gold is the other half of *qızıl*, which also names the metal.)
3. **Separation is a hairline, never a shadow.** One glow in the system: the
   active `-O` button and the primary action.
4. **Every number is monospace and tabular.** `font-variant-numeric:
   tabular-nums` is set on `body`; digits must align down a column.
5. **Motion is 150–650ms, ease-out, and never blocks reading.** Cells rise 12px
   on entry with a 45ms stagger; bars grow from zero. All of it collapses under
   `prefers-reduced-motion`.
6. **Nothing loads from the network.** No web fonts, no CDN, no icon library —
   a report has to render offline in ten years. Type is the system stack;
   symbols are Unicode.
7. **Every string must do work.** No taglines, no restating what a panel
   obviously shows, no status shown twice in two places. A label names a
   number, a caption carries a parameter or a count, an empty state explains
   why it is empty. If a sentence would survive being deleted, delete it.

## Tokens

All tokens live in `:root` and are re-declared in `:root[data-theme="light"]`.
Never hard-code a colour in a rule or in JS — the SVG circuit renderer styles
gates with `var(--<class>)` / `var(--<class>-bg)` so both themes work for free.

```
surfaces   --bg  --bg-2  --bg-3  --bg-4      four planes, darkest at the back
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
| section title | 11.5px / 650 | sans, uppercase | 0.14em |
| rail label (`.k`, `.fk`, `th`) | 9–9.5px | mono, uppercase | 0.14–0.16em |
| body | 14px | sans | normal |
| data | 11–13px | mono | normal |

The jump from 9.5px rails to 27px hero numbers is deliberate: Swiss hierarchy
comes from *contrast in scale*, not from many intermediate sizes.

## Layout

```
.canvas               12-column grid, 14px gutter, 1420px max
  .hero               span 12 — auto-fit stat strip, 1px internal rules
  .proof              span 12 — verdict banner
  .cell.s5 / .s7      metrics + resources
  .cell               span 12 — circuit
  .cell.s6 / .s6      trace + histogram
  .cell               span 12 — diff
```

Below 1120px every cell collapses to full width; the grid is the only
breakpoint the design needs.

## Adding a panel

```js
var node = cell("Panel title", "right-aligned meta", 6);  // 6 = span
node.appendChild(/* content */);
app.appendChild(node);
```

`cell()` assigns the section number and the entry-animation delay
automatically, so panels stay numbered in DOM order. Use `rows()` for
before/after bars, `el`/`s` for DOM/SVG, and pull every colour from a token.
