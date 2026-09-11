import json

import psycopg

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    row_id      integer PRIMARY KEY,
    name        text NOT NULL,
    start_date  date,
    source_url  text,
    speaking_class text,
    format      text,
    status      text NOT NULL DEFAULT 'pending',
    error       text,
    fetched_at  timestamptz
);
CREATE TABLE IF NOT EXISTS raw_tabs (
    row_id  integer PRIMARY KEY REFERENCES tournaments,
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_motions (
    row_id  integer PRIMARY KEY,
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_judges (
    row_id  integer PRIMARY KEY,
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
    id      bigserial PRIMARY KEY,
    row_id  integer NOT NULL,
    t       integer NOT NULL,
    stage   text NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS rooms_row_idx ON rooms (row_id);
CREATE TABLE IF NOT EXISTS extra_games (
    id      bigserial PRIMARY KEY,
    source  text NOT NULL,
    row_id  integer NOT NULL,
    t       integer NOT NULL,
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    name    text PRIMARY KEY,
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS payloads (
    name     text NOT NULL,
    encoding text NOT NULL,
    built_at timestamptz NOT NULL,
    body     bytea NOT NULL,
    PRIMARY KEY (name, encoding)
);
CREATE TABLE IF NOT EXISTS model_files (
    model text NOT NULL,
    name  text NOT NULL,
    body  bytea NOT NULL,
    PRIMARY KEY (model, name)
);
CREATE TABLE IF NOT EXISTS rating_snapshots (
    built_at   timestamptz PRIMARY KEY,
    n_rooms    integer,
    n_speakers integer,
    summary    jsonb
);
"""


def connect():
    return psycopg.connect(settings.DATABASE_URL)


def migrate(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def get_artifact(conn, name, default=None):
    with conn.cursor() as cur:
        cur.execute("SELECT payload FROM artifacts WHERE name = %s", (name,))
        row = cur.fetchone()
    return row[0] if row else default


def set_artifact(conn, name, payload):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO artifacts (name, payload) VALUES (%s, %s) "
            "ON CONFLICT (name) DO UPDATE SET payload = EXCLUDED.payload",
            (name, json.dumps(payload, ensure_ascii=False)),
        )
    conn.commit()


def put_model_file(conn, model, name, body):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_files (model, name, body) VALUES (%s, %s, %s) "
            "ON CONFLICT (model, name) DO UPDATE SET body = EXCLUDED.body",
            (model, name, body))
    conn.commit()


def get_model_files(conn, model):
    with conn.cursor() as cur:
        cur.execute("SELECT name, body FROM model_files WHERE model = %s", (model,))
        return {name: bytes(body) for name, body in cur.fetchall()}


def iter_rooms(conn):
    with conn.cursor(name="rooms_cur") as cur:
        cur.itersize = 5000
        cur.execute("SELECT payload FROM rooms ORDER BY row_id, id")
        for (payload,) in cur:
            yield payload


def iter_extra_games(conn, sources=None):
    """Score-only games, with player keys resolved through id_merges like tab rosters are."""
    merges = get_artifact(conn, "id_merges", {})
    for g in _extra_games(conn, sources):
        g["c"] = [[merges.get(k, k) for k in team] for team in g["c"]]
        yield g


def _extra_games(conn, sources):
    q = "SELECT payload FROM extra_games"
    params = ()
    if sources is not None:
        q += " WHERE source = ANY(%s)"
        params = (list(sources),)
    with conn.cursor(name="extra_cur") as cur:
        cur.itersize = 5000
        cur.execute(q + " ORDER BY row_id, id", params)
        for (payload,) in cur:
            yield payload
