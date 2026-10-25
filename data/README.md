# musicbrainz_classical.sqlite

A trimmed, read-only copy of MusicBrainz, built from the full Postgres dump
at https://metabrainz.org/datasets/postgres-dumps. Queried by
`musicbrainz_db_core.py` (same interface as the live-API version in
`musicbrainz_core.py`, but local and much faster — no rate limiting, no
network).

## Why it's trimmed

The full dump is ~7.6GB compressed / ~25GB restored, dominated by tables that
have little to do with classical music: `track` (7.9GB), `recording` (4.6GB,
40.6M rows total), `url` (2.8GB), `l_artist_recording` (1.3GB). Most of that
is pop/rock/electronic catalog breadth — remasters, singles, bootlegs, and
their Discogs/Spotify URL links.

The filter: MusicBrainz's `work` entity (a composition, with movements
modeled as child works via a "parts" relationship) is mostly a classical/jazz
modeling convention — most non-classical recordings have no work link at
all. So keeping only recordings linked to a work via a "performance"
relationship cuts 40.6M recordings down to 7.85M, and dropping every
non-essential column/table (releases, tracks, URLs, edit history, aliases)
on top of that gets the final SQLite file to **~1.8GB**. `track`/`medium`/
`release`/`release_country` do get pulled in too, but only transiently,
to derive each recording's release year (see below) — they're dropped
again before the final export, so they don't add to that number.

### Recording year

The live API exposes a `first-release-date` per recording, which turned out
to be a server-side materialized value (`recording_first_release_date`)
that MusicBrainz doesn't publish in any dump archive (checked both
`mbdump.tar.bz2` and `mbdump-derived.tar.bz2` — neither has it). So `year`
is derived ourselves: for each kept recording, `MIN` of the release dates
(country-specific `release_country.date_year`, falling back to
`release_unknown_country.date_year`) among every release whose tracks
include that recording, joined via `track.recording -> track.medium ->
medium.release`. This matches the live API where it's checkable (e.g. the
1972 Perlman/Zukerman/Barenboim Bach BWV 1043 recording explored earlier
in development came back as 1972 from both).

## Schema

- `work(id, gid, name, work_type, composer_name)` — all 2.88M works (kept
  whole; cheap). `composer_name` is the first composer (by link_order) from
  the artist-work "composer" relationship (link_type 168); ~71% of works
  have one (2.04M of 2.88M) — the rest are mostly movements, arrangements,
  or works MusicBrainz hasn't had a composer credited on yet.
- `work_parts(parent_work_id, child_work_id, link_order)` — movement
  structure, from work-work relationships of type "parts" (link_type 281).
- `recording(id, gid, name, artist_credit, length, year)` — only recordings
  with a "performance" link to a work (link_type 278) and a known duration.
  `year` is the earliest release year among releases whose tracks include
  this recording (derived from `track`/`medium`/`release`/`release_country`
  — see "Recording year" below); present for 93.6% of recordings
  (7.34M of 7.85M).
- `artist_credit(id, name)` — the rendered performer-credit string, only
  rows referenced by a kept recording.
- `recording_work(recording_id, work_id)` — the performance links themselves.
- `work_fts` — FTS5 index over `work.name` and `work.composer_name` (so
  "Bach BWV 1043" or "Beethoven Symphony 5" style composer-first queries
  work) for search.

`id` columns are MusicBrainz's internal integer row ids (fast joins); `gid`
is the public MBID (UUID) and is what `musicbrainz_db_core.py` exposes
externally, for parity with the live-API module.

## Known gaps vs. the live API

- **No work aliases.** MusicBrainz's own search also matches alternate
  titles (e.g. "Double Violin Concerto" as an alias of "Concerto for 2
  Violins in D minor, BWV 1043"); this DB only indexes the canonical name.
  Searching the exact/near-exact title works well; casual aliases may not
  resolve. Fixable by pulling in `work_alias` if it matters in practice.
- **Recording year is ~94% complete, not 100%.** The 6.4% without a year
  are mostly recordings on releases/mediums with no recorded date at all
  in MusicBrainz (not a bug in the derivation — there's genuinely nothing
  to find for those).
- **Duplicate/unmerged works.** Like the live catalog itself, MusicBrainz
  sometimes has near-duplicate work entries that editors haven't merged
  (e.g. a sparse duplicate of a popular work with only a handful of
  recordings). FTS ranking generally surfaces the richer one first, but it's
  not guaranteed.
- **Frozen in time.** Built from a specific dump snapshot (twice-weekly
  dumps are published); rebuild periodically if that matters to you.

## Rebuilding

```
./build_db.sh /path/to/mbdump.tar.bz2
```

Requires a local Postgres you can create scratch databases on, and the
`sqlite3` CLI, plus a few GB of scratch disk space (`track` alone loads
~58M rows, dropped again once the year derivation is done). Takes
~15-20 minutes, dominated by decompressing the archive (~6 min) and loading
`track` into Postgres (~2 min). See the script for the exact table/link_type
filtering logic.
