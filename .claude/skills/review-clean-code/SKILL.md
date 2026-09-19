---
name: review-clean-code
description: Audit changed files in this repo against its Clean Code conventions and the fragile spots CLAUDE.md names. Invoke before pushing a non-trivial change.
disable-model-invocation: true
allowed-tools: Read, Grep, Bash(git diff:*), Bash(git status:*), Bash(wc:*), Bash(grep:*)
---

# Clean-code review for LeafGreen Maximizer

Review the change under review (default: the working diff — run `git diff` and `git status`; if the
user named files or a commit range, use that instead). Judge it against this repo's own conventions,
not a generic checklist. `CLAUDE.md` is the contract; this skill is its review pass.

Report findings grouped **Important** (a real defect) then **Nit** (style/consistency). For each:
`file:line — one-sentence problem — concrete fix`. If the diff is clean, say so plainly; don't invent
findings. Cap nits at ~8 and summarize the rest as "plus N similar".

## Check, in priority order

1. **The positional-array payload contract.** The single most bug-prone spot. If the diff touches
   `compact.py`'s packers (`pack_stop`, `member_row`, `move_row`, the `sec`/`leg` dicts, `team_row`,
   `counter_row`, …) OR the array indices in `app_template.html` (`s[11]`, `node.stop[12]`, etc.):
   - A new field must be **appended at the end** of the packer, and **every reader index** updated to
     match. Grep the app for the array to find all readers.
   - Cross-check the field-map comment above each packer against the actual tuple order — an
     off-by-one here is silent (verify.py won't catch a display bug).

2. **Decomp sourcing.** Any new game fact (a level, rate, position, mechanic, item, flag) must come
   from `$LGMAX_POKEFIRERED`, not a constant typed from memory or a wiki. A hard-coded number that
   encodes a game rule should cite its source file in a comment, or be parsed. Flag magic numbers
   that look like game data with no source.

3. **verify.py coverage.** If the change fixes a bug a playthrough could hit, or adds a new kind of
   route stop / party rule / mechanic, is there a guard for it? If a plausible regression would slip
   past all 38 checks, recommend the guard (name it). Never suggest weakening an existing guard.

4. **Comments say why, and cite.** House style: comments explain the *mechanic or source*, never
   restate the code. Flag a new comment that narrates what the next line does. Flag a new public
   function with no docstring.

5. **Function and module size.** Flag a new or grown function past ~40 lines that mixes concerns, and
   any addition to `sections.py` (already the largest module) that should be its own helper. Watch
   `assign_hms`-style loops that mutate a shared list — every `.remove(x)` needs `if x in list`.

6. **Naming & duplication.** Names state intent (no `process`, `do_thing`, single letters outside
   math/loops). Flag logic copy-pasted into a 3rd place that wants a shared helper (the vs/wild/trade
   band functions and the three hunt-display sites are existing examples to not extend).

7. **Build hygiene.** Built artifacts (`app.html`, `data/`) are gitignored — flag if the diff stages
   one. Source (`app_template.html`, the `.py` files) is what gets committed.

## Useful commands

- `git diff` / `git diff --stat` — the change under review
- `grep -n "^def \|^    def " <file>` — function boundaries, to size them
- `grep -n "s\[1[0-9]\]\|node.stop\[" app_template.html` — payload array readers
- `grep -rn "except:" *.py` — bare excepts (there should be none)

Do not run the pipeline or edit files — this is a read-only review. If asked to apply fixes, do them
as a separate explicit step after the report.
