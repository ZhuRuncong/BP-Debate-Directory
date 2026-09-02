import collections
import hashlib
import re

from debaterskill import Speaker, Tab

from . import db, priors
from .adapters import add_extras, load_debates
from .settings import QUALITY_PRIOR_STRENGTH

GAP_SCALE = 0.85
SCALE_MULT = 2.5 * GAP_SCALE
EXTRA_SOURCES = ("sheets", "videos", "hague", "scoreonly")


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
