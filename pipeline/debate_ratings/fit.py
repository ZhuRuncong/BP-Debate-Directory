import collections
import gzip
import hashlib
import json
import re
from pathlib import Path

import debaterskill
from debaterskill import Gaussian, Speaker, Tab

from . import db, priors
from .adapters import add_extras, load_debates
from .settings import QUALITY_PRIOR_STRENGTH

GAP_SCALE = 0.85
SCALE_MULT = 2.5 * GAP_SCALE
EXTRA_SOURCES = ("sheets", "videos", "hague", "scoreonly")
CACHE_MODEL = "fit_cache"
FIT_SOURCES = ("rooms.py", "fit.py", "adapters.py", "judges.py", "idnorm.py",
               "priors.py", "settings.py")


def mid(text: str) -> str:
    """Stable short id for whitespace/case-normalized motion text."""
    return hashlib.md5(re.sub(r"\s+", " ", text).strip().lower()
                       .encode("utf-8")).hexdigest()[:10]


def motion_map(conn) -> tuple[dict, dict, dict]:
    """Map (row_id, seq) -> motion slug, plus slug -> occurrences and slug -> text."""
    clean = db.get_artifact(conn, "motions_clean", {})
    mm, occs, texts = {}, collections.defaultdict(list), {}
    with conn.cursor() as cur:
        cur.execute("SELECT row_id, payload FROM raw_motions ORDER BY row_id")
        rows = cur.fetchall()
    for row, seqs in rows:
        if not seqs or seqs.get("_err"):
            continue
        for seq, ms in seqs.items():
            for mo in ms or []:
                if not isinstance(mo, dict):
                    continue
                c = clean.get(mid(mo.get("t") or ""))
                if not c or c["skip"]:
                    continue
                slug = "m" + mid(c["t"])
                mm[(int(row), str(seq))] = slug
                occs[slug].append((int(row), str(seq)))
                texts[slug] = c["t"]
                break  # one motion per round; ignore alternates
    return mm, occs, texts


def fit(conn, motions: bool, mm: dict, log=print) -> Tab:
    """Fit the skill model over all rooms and extra games; motions toggles side-bias terms."""
    tab = Tab(mu=0.0, sigma=1.2, beta=1.0, gamma=0.024,
              between="ordinal", within="gap",
              motions=motions, motion_sigma=0.28, period="date")
    prior_mus = priors.quality_prior_mus(conn, QUALITY_PRIOR_STRENGTH)
    if prior_mus:
        tab.enroll(*(Speaker(name, mu=mu) for name, mu in sorted(prior_mus.items())))
        log("quality priors: %d players enrolled (strength %g)"
            % (len(prior_mus), QUALITY_PRIOR_STRENGTH))
    debates = load_debates(db.iter_rooms(conn), motions=motions, motion_map=mm)
    for d in debates:
        d.scale *= SCALE_MULT
    for d in debates:
        tab.add(d)
    meta = db.get_artifact(conn, "rooms_meta", {})
    speak_scale = meta.get("speak_scale", 3.0)
    n_extra = add_extras(tab, db.iter_extra_games(conn, EXTRA_SOURCES),
                         speak_scale, motions=motions, scale_mult=SCALE_MULT)
    log(f"motions={motions}: {len(debates)} rooms + {n_extra} extras -> {tab.size} ballots")
    step, iters = tab.fit(iterations=400, epsilon=1e-6)
    log(f"converged in {iters} iterations, step {max(step):.2e}, "
        f"log evidence {tab.log_evidence():.1f}")
    return tab


def table_stamp(conn, table, key):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), md5(coalesce(string_agg({key}::text || ':' || xmin::text, "
                    f"',' ORDER BY {key}), '')) FROM {table}")
        return list(cur.fetchone())


def fingerprint(conn, mm: dict) -> str:
    h = hashlib.sha256()

    def feed(obj):
        h.update(json.dumps(obj, sort_keys=True, default=str).encode("utf-8"))

    feed(db.get_artifact(conn, "id_merges", {}))
    feed(sorted(db.get_artifact(conn, "excluded_rows", [])))
    for table, key in (("raw_tabs", "row_id"), ("raw_judges", "row_id"), ("extra_games", "id")):
        feed(table_stamp(conn, table, key))
    feed(sorted([row, seq, slug] for (row, seq), slug in mm.items()))
    feed(QUALITY_PRIOR_STRENGTH)
    if QUALITY_PRIOR_STRENGTH:
        feed(db.get_artifact(conn, "tier_shape", {}))
        feed(table_stamp(conn, "tournaments", "row_id"))
    here = Path(__file__).parent
    for name in FIT_SOURCES:
        h.update((here / name).read_bytes())
    pkg = Path(debaterskill.__file__).parent
    for f in sorted(p for p in pkg.iterdir() if p.is_file()):
        h.update(f.name.encode("utf-8") + f.read_bytes())
    return h.hexdigest()


def _keep(key):
    if isinstance(key, tuple):
        return key[0] != "team"
    return isinstance(key, str) and not key.startswith("anon::")


def _pack(tab):
    return [[list(k) if isinstance(k, tuple) else k, [[t, g.mu, g.sigma] for t, g in pts]]
            for k, pts in tab.curves().items() if _keep(k)]


class CachedTab:
    def __init__(self, packed):
        self._curves = {tuple(k) if isinstance(k, list) else k:
                        [(t, Gaussian(mu, sigma)) for t, mu, sigma in pts] for k, pts in packed}

    def curves(self):
        return self._curves

    def motion_bias(self):
        return {k: pts[-1][1] for k, pts in self._curves.items()
                if isinstance(k, tuple) and k[0] != "team"}


def save_cache(conn, stamp: str, base, abl) -> None:
    db.put_model_file(conn, CACHE_MODEL, "fingerprint", b"")
    for name, tab in (("base", base), ("abl", abl)):
        db.put_model_file(conn, CACHE_MODEL, name,
                          gzip.compress(json.dumps(_pack(tab)).encode("utf-8"), 6))
    db.put_model_file(conn, CACHE_MODEL, "fingerprint", stamp.encode("utf-8"))


def load_cache(conn, stamp: str):
    files = db.get_model_files(conn, CACHE_MODEL)
    if files.get("fingerprint", b"").decode("utf-8") != stamp:
        return None
    return tuple(CachedTab(json.loads(gzip.decompress(files[n]))) for n in ("base", "abl"))
