#!/usr/bin/env bash
# Builds data/musicbrainz_classical.sqlite from a full MusicBrainz Postgres
# dump (mbdump.tar.bz2, from https://metabrainz.org/datasets/postgres-dumps).
#
# Keeps only the "classical-shaped" slice of MusicBrainz: recordings linked
# to a work via a "performance" relationship, the work-to-work "parts"
# (movement) structure, each work's composer (via the artist-work "composer"
# relationship), and the artist credits those recordings reference. Drops
# everything else (releases, tracks, URLs, non-work-linked recordings, edit
# history, etc.) — see README.md for why and for the size numbers this
# produces.
#
# Requires: a local Postgres server you can create databases on, and the
# sqlite3 CLI (ships with macOS). Uses a scratch Postgres database named
# musicbrainz_trim_build, dropped at the end.
#
# Usage: ./build_db.sh /path/to/mbdump.tar.bz2

set -euo pipefail

DUMP_PATH="${1:?Usage: build_db.sh /path/to/mbdump.tar.bz2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_SQLITE="$SCRIPT_DIR/musicbrainz_classical.sqlite"
WORK_DIR="$(mktemp -d)"
PG_DB="musicbrainz_trim_build"

cleanup() {
  dropdb --if-exists "$PG_DB" 2>/dev/null || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "==> Extracting needed tables from $DUMP_PATH (this fully decompresses the archive once, ~5-10 min)"
mkdir -p "$WORK_DIR/raw"
tar -xjf "$DUMP_PATH" -C "$WORK_DIR/raw" \
  mbdump/work mbdump/l_work_work mbdump/recording mbdump/l_recording_work \
  mbdump/artist_credit mbdump/link mbdump/link_type mbdump/work_type mbdump/artist_type \
  mbdump/artist mbdump/l_artist_work \
  mbdump/track mbdump/medium mbdump/release mbdump/release_country mbdump/release_unknown_country

echo "==> Creating scratch Postgres database $PG_DB"
dropdb --if-exists "$PG_DB"
createdb "$PG_DB"

psql -d "$PG_DB" <<'SQL'
CREATE TABLE stg_work (
    id INTEGER, gid UUID NOT NULL, name VARCHAR NOT NULL, type INTEGER,
    comment VARCHAR(255) NOT NULL DEFAULT '', edits_pending INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE TABLE stg_l_work_work (
    id INTEGER, link INTEGER NOT NULL, entity0 INTEGER NOT NULL, entity1 INTEGER NOT NULL,
    edits_pending INTEGER NOT NULL DEFAULT 0, last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    link_order INTEGER NOT NULL DEFAULT 0, entity0_credit TEXT NOT NULL DEFAULT '', entity1_credit TEXT NOT NULL DEFAULT ''
);
CREATE TABLE stg_recording (
    id INTEGER, gid UUID NOT NULL, name VARCHAR NOT NULL, artist_credit INTEGER NOT NULL,
    length INTEGER, comment VARCHAR(255) NOT NULL DEFAULT '', edits_pending INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(), video BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE stg_l_recording_work (
    id INTEGER, link INTEGER NOT NULL, entity0 INTEGER NOT NULL, entity1 INTEGER NOT NULL,
    edits_pending INTEGER NOT NULL DEFAULT 0, last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    link_order INTEGER NOT NULL DEFAULT 0, entity0_credit TEXT NOT NULL DEFAULT '', entity1_credit TEXT NOT NULL DEFAULT ''
);
CREATE TABLE stg_artist_credit (
    id INTEGER, name VARCHAR NOT NULL, artist_count SMALLINT NOT NULL, ref_count INTEGER DEFAULT 0,
    created TIMESTAMP WITH TIME ZONE DEFAULT NOW(), edits_pending INTEGER NOT NULL DEFAULT 0, gid UUID NOT NULL
);
CREATE TABLE stg_link (
    id INTEGER, link_type INTEGER NOT NULL, begin_date_year SMALLINT, begin_date_month SMALLINT,
    begin_date_day SMALLINT, end_date_year SMALLINT, end_date_month SMALLINT, end_date_day SMALLINT,
    attribute_count INTEGER NOT NULL DEFAULT 0, created TIMESTAMP WITH TIME ZONE DEFAULT NOW(), ended BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE stg_link_type (
    id INTEGER, parent INTEGER, child_order INTEGER NOT NULL DEFAULT 0, gid UUID NOT NULL,
    entity_type0 VARCHAR(50) NOT NULL, entity_type1 VARCHAR(50) NOT NULL, name VARCHAR(255) NOT NULL,
    description TEXT, link_phrase VARCHAR(255) NOT NULL, reverse_link_phrase VARCHAR(255) NOT NULL,
    long_link_phrase VARCHAR(255) NOT NULL, last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_deprecated BOOLEAN NOT NULL DEFAULT false, has_dates BOOLEAN NOT NULL DEFAULT true,
    entity0_cardinality SMALLINT NOT NULL DEFAULT 0, entity1_cardinality SMALLINT NOT NULL DEFAULT 0
);
CREATE TABLE stg_work_type (
    id INTEGER, name VARCHAR(255) NOT NULL, parent INTEGER, child_order INTEGER NOT NULL DEFAULT 0,
    description TEXT, gid UUID NOT NULL
);
CREATE TABLE stg_artist_type (
    id INTEGER, name VARCHAR(255) NOT NULL, parent INTEGER, child_order INTEGER NOT NULL DEFAULT 0,
    description TEXT, gid UUID NOT NULL
);
CREATE TABLE stg_artist (
    id INTEGER, gid UUID NOT NULL, name VARCHAR NOT NULL, sort_name VARCHAR NOT NULL,
    begin_date_year SMALLINT, begin_date_month SMALLINT, begin_date_day SMALLINT,
    end_date_year SMALLINT, end_date_month SMALLINT, end_date_day SMALLINT,
    type INTEGER, area INTEGER, gender INTEGER, comment VARCHAR(255) NOT NULL DEFAULT '',
    edits_pending INTEGER NOT NULL DEFAULT 0, last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ended BOOLEAN NOT NULL DEFAULT FALSE, begin_area INTEGER, end_area INTEGER
);
CREATE TABLE stg_l_artist_work (
    id INTEGER, link INTEGER NOT NULL, entity0 INTEGER NOT NULL, entity1 INTEGER NOT NULL,
    edits_pending INTEGER NOT NULL DEFAULT 0, last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    link_order INTEGER NOT NULL DEFAULT 0, entity0_credit TEXT NOT NULL DEFAULT '', entity1_credit TEXT NOT NULL DEFAULT ''
);
CREATE TABLE stg_track (
    id INTEGER, gid UUID NOT NULL, recording INTEGER NOT NULL, medium INTEGER NOT NULL,
    position INTEGER NOT NULL, number TEXT NOT NULL, name VARCHAR NOT NULL,
    artist_credit INTEGER NOT NULL, length INTEGER, edits_pending INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(), is_data_track BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE stg_medium (
    id INTEGER, release INTEGER NOT NULL, position INTEGER NOT NULL, format INTEGER,
    name VARCHAR NOT NULL DEFAULT '', edits_pending INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(), track_count INTEGER NOT NULL DEFAULT 0, gid UUID NOT NULL
);
CREATE TABLE stg_release (
    id INTEGER, gid UUID NOT NULL, name VARCHAR NOT NULL, artist_credit INTEGER NOT NULL,
    release_group INTEGER NOT NULL, status INTEGER, packaging INTEGER, language INTEGER, script INTEGER,
    barcode VARCHAR(255), comment VARCHAR(255) NOT NULL DEFAULT '', edits_pending INTEGER NOT NULL DEFAULT 0,
    quality SMALLINT NOT NULL DEFAULT -1, last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE TABLE stg_release_country (
    release INTEGER NOT NULL, country INTEGER NOT NULL, date_year SMALLINT, date_month SMALLINT, date_day SMALLINT
);
CREATE TABLE stg_release_unknown_country (
    release INTEGER NOT NULL, date_year SMALLINT, date_month SMALLINT, date_day SMALLINT
);
SQL

echo "==> Loading raw MusicBrainz tables"
cd "$WORK_DIR/raw/mbdump"
psql -d "$PG_DB" -c "\copy stg_work FROM 'work' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_l_work_work FROM 'l_work_work' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_recording FROM 'recording' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_l_recording_work FROM 'l_recording_work' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_artist_credit FROM 'artist_credit' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_link FROM 'link' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_link_type FROM 'link_type' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_work_type FROM 'work_type' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_artist_type FROM 'artist_type' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_artist FROM 'artist' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_l_artist_work FROM 'l_artist_work' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_medium FROM 'medium' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_release FROM 'release' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_release_country FROM 'release_country' WITH (FORMAT text)"
psql -d "$PG_DB" -c "\copy stg_release_unknown_country FROM 'release_unknown_country' WITH (FORMAT text)"
# track is the single biggest table in the dump (~50-60M rows); loads last.
psql -d "$PG_DB" -c "\copy stg_track FROM 'track' WITH (FORMAT text)"
cd - >/dev/null

echo "==> Deriving trimmed classical-only tables"
# link_type 278 = "performance" (recording -> work); 281 = "parts" (work -> work, movements);
# 168 = "composer" (artist -> work). These ids are from the official MusicBrainz schema and
# are stable, but if this script ever errors or produces empty results, re-check them via:
#   SELECT id, name FROM stg_link_type WHERE name IN ('performance', 'parts', 'composer');
psql -d "$PG_DB" <<'SQL'
CREATE TABLE recording_work AS
SELECT DISTINCT lrw.entity0 AS recording_id, lrw.entity1 AS work_id
FROM stg_l_recording_work lrw JOIN stg_link l ON l.id = lrw.link
WHERE l.link_type = 278;

CREATE TABLE work_parts AS
SELECT lww.entity0 AS parent_work_id, lww.entity1 AS child_work_id, lww.link_order
FROM stg_l_work_work lww JOIN stg_link l ON l.id = lww.link
WHERE l.link_type = 281;

CREATE TABLE recording_pre AS
SELECT r.id, r.gid, r.name, r.artist_credit, r.length
FROM stg_recording r
WHERE EXISTS (SELECT 1 FROM recording_work rw WHERE rw.recording_id = r.id);

CREATE UNIQUE INDEX ON recording_pre(id);

-- First release year per recording: earliest date (country-specific or
-- unknown-country) among releases whose tracks include this recording.
-- MusicBrainz doesn't publish its own materialized recording_first_release_date
-- in any dump archive (checked both mbdump.tar.bz2 and mbdump-derived.tar.bz2),
-- so this is derived from track/medium/release/release_country ourselves.
CREATE INDEX idx_stg_track_recording ON stg_track(recording);
CREATE INDEX idx_stg_track_medium ON stg_track(medium);
CREATE INDEX idx_stg_medium_id ON stg_medium(id);
CREATE INDEX idx_stg_release_country_release ON stg_release_country(release);
CREATE INDEX idx_stg_release_unknown_country_release ON stg_release_unknown_country(release);

CREATE TABLE recording_year AS
SELECT t.recording AS recording_id, MIN(y.year) AS year
FROM stg_track t
JOIN stg_medium m ON m.id = t.medium
JOIN (
    SELECT release, date_year AS year FROM stg_release_country WHERE date_year IS NOT NULL
    UNION ALL
    SELECT release, date_year AS year FROM stg_release_unknown_country WHERE date_year IS NOT NULL
) y ON y.release = m.release
WHERE EXISTS (SELECT 1 FROM recording_pre r WHERE r.id = t.recording)
GROUP BY t.recording;

CREATE TABLE recording AS
SELECT rp.id, rp.gid, rp.name, rp.artist_credit, rp.length, ry.year
FROM recording_pre rp
LEFT JOIN recording_year ry ON ry.recording_id = rp.id;

-- One composer per work (first by link_order when a work has multiple).
CREATE TABLE work_composer AS
SELECT DISTINCT ON (law.entity1) law.entity1 AS work_id, a.name AS composer_name
FROM stg_l_artist_work law
JOIN stg_link l ON l.id = law.link
JOIN stg_artist a ON a.id = law.entity0
WHERE l.link_type = 168
ORDER BY law.entity1, law.link_order;

CREATE TABLE work AS
SELECT w.id, w.gid, w.name, wt.name AS work_type, wc.composer_name
FROM stg_work w
LEFT JOIN stg_work_type wt ON wt.id = w.type
LEFT JOIN work_composer wc ON wc.work_id = w.id;

CREATE TABLE artist_credit AS
SELECT ac.id, ac.name
FROM stg_artist_credit ac
WHERE EXISTS (SELECT 1 FROM recording r WHERE r.artist_credit = ac.id);
SQL

echo "==> Exporting to CSV"
cd "$WORK_DIR"
psql -d "$PG_DB" -c "\copy work TO 'work.csv' WITH (FORMAT csv, HEADER true)"
psql -d "$PG_DB" -c "\copy recording TO 'recording.csv' WITH (FORMAT csv, HEADER true)"
psql -d "$PG_DB" -c "\copy artist_credit TO 'artist_credit.csv' WITH (FORMAT csv, HEADER true)"
psql -d "$PG_DB" -c "\copy recording_work TO 'recording_work.csv' WITH (FORMAT csv, HEADER true)"
psql -d "$PG_DB" -c "\copy work_parts TO 'work_parts.csv' WITH (FORMAT csv, HEADER true)"

echo "==> Building SQLite file"
rm -f "$OUT_SQLITE"
sqlite3 "$OUT_SQLITE" <<SQL
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE work (id INTEGER PRIMARY KEY, gid TEXT NOT NULL, name TEXT NOT NULL, work_type TEXT, composer_name TEXT);
CREATE TABLE artist_credit (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE recording (
    id INTEGER PRIMARY KEY, gid TEXT NOT NULL, name TEXT NOT NULL,
    artist_credit INTEGER NOT NULL REFERENCES artist_credit(id), length INTEGER, year INTEGER
);
CREATE TABLE recording_work (
    recording_id INTEGER NOT NULL REFERENCES recording(id),
    work_id INTEGER NOT NULL REFERENCES work(id)
);
CREATE TABLE work_parts (
    parent_work_id INTEGER NOT NULL REFERENCES work(id),
    child_work_id INTEGER NOT NULL REFERENCES work(id),
    link_order INTEGER NOT NULL
);

.mode csv
.import --skip 1 $WORK_DIR/work.csv work
.import --skip 1 $WORK_DIR/artist_credit.csv artist_credit
.import --skip 1 $WORK_DIR/recording.csv recording
.import --skip 1 $WORK_DIR/recording_work.csv recording_work
.import --skip 1 $WORK_DIR/work_parts.csv work_parts

-- sqlite3's CSV importer leaves empty fields as '' rather than NULL.
UPDATE recording SET year = NULL WHERE year = '';
UPDATE recording SET length = NULL WHERE length = '';
UPDATE work SET composer_name = NULL WHERE composer_name = '';
UPDATE work SET work_type = NULL WHERE work_type = '';

CREATE INDEX idx_recording_work_work ON recording_work(work_id);
CREATE INDEX idx_recording_work_recording ON recording_work(recording_id);
CREATE INDEX idx_work_parts_parent ON work_parts(parent_work_id);
CREATE INDEX idx_work_parts_child ON work_parts(child_work_id);
CREATE INDEX idx_work_gid ON work(gid);
CREATE INDEX idx_recording_gid ON recording(gid);

CREATE VIRTUAL TABLE work_fts USING fts5(name, composer_name, content='work', content_rowid='id');
INSERT INTO work_fts(rowid, name, composer_name) SELECT id, name, coalesce(composer_name, '') FROM work;

ANALYZE;
VACUUM;
SQL

echo "==> Done: $OUT_SQLITE"
ls -lh "$OUT_SQLITE"
