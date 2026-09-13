# Walk order — meeting trainers where they actually stand

Built by `route_order.py`.

## The bug

A section's battles used to be sorted by a hand-written floor table and then by **party level**.
Level is only a proxy for "further in", and it is not even monotonic along a route: Route 3's eight
trainers came out L9, L9, L10, L10, L11, L11, L14, L14, matching nothing you would walk. The wild
battles were then spread evenly across the whole section regardless of map, so you fought Mt. Moon
Zubats while standing on Route 3.

## The fix, in two levels

**Which map first.** Ordered by the curated floor table where one exists — it encodes what geometry
cannot, like which of Silph Co.'s eleven identical floors comes next — and otherwise by hop
distance through the real map graph, built from route `connections` and interior `warp_events`.

**Where in the map.** Every trainer is an object event with real x/y. Joining each object's `script`
label to the `trainerbattle_*` line in that label's body puts **418 trainers on the tile they stand
on**. Then:

- **Outdoor routes** get a direction of travel: project each trainer onto the line from the edge
  you enter by to the edge you leave by. Route 3 reads x = 12, 17, 19, 25, 29, 30, 32, 40 — west to
  east, exactly as you walk it from Pewter.
- **Interiors** have no single line of travel (Mt. Moon 1F has four staircases), so they order by
  distance from the entrance: you meet what is nearest the way in first.

The ~56 trainers without a tile are almost all gym leaders, rivals and Elite Four members standing
alone in their own room, where order is moot.

## Routes you walk twice

Route 10 is the clean case. You come in from Route 9 **halfway up it**, walk north to Rock Tunnel,
and only meet the southern half on the way back out toward Lavender. Projecting onto the
entry→exit line makes those southern trainers **negative** — behind the way in, so on a later leg.

A second leg only exists where the shape warrants it: an outdoor route that leaves the section
through a *door* rather than off its own edge. That is the signature of "walk in, duck into a cave,
come back out and carry on". An interior is pushed through once.

Section 13 now reads: Route 9 (west→east) → Route 10 north → Rock Tunnel 1F → B1F → **Route 10
south**, y = 60, 62, 68, 70, walking down to Lavender.

## The Pokémon Center at Mt. Moon's entrance

The geography is the opposite of what the model assumed. **Route 3 does not touch Mt. Moon.** It
runs *up* into the **west end of Route 4**, and that is where both the Pokémon Center (warp at
12,5) and the cave mouth (19,5) sit. Route 4 then continues east past Mt. Moon's far exit.

Heals were keyed off battle *locations*, and no stage-5 battle is on Route 4, so the Center was
never found. Now the walk between consecutive maps is checked: if the path passes a map with a
Pokémon Center door, the party heals there. It also picked up a Saffron heal between the Fighting
Dojo and Silph Co. that was being missed the same way.

A section is one level-and-badge bracket — level 15, one badge, on both sides of that Center — so
splitting it into two numbered sections would produce two sections identical in every field but
the name. What changes at the Center is HP and PP, which is what a heal point models.

## Wild battles follow the same walk

Each map's wild battles sit among that map's own trainers, and a map with no trainers at all
(Mt. Moon B1F) empties out at the point in the walk where you cross it.

## Guard rails

- a section's trainers are grouped by map, in at most two legs
- every trainer the model orders has a real tile on a real map (>400 placed)
