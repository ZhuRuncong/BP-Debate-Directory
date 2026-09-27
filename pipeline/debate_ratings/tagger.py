import json
import os
import random
import time

import httpx

from . import db
from .fit import mid

# the vocabulary; each gloss is what the model is told the tag covers
TAGS = {
    "Art": "art, film, music, literature, games, entertainment and pop culture, and how they're made",
    "Business": "companies, corporate conduct, investors, brands and advertising, consumers",
    "Criminal Justice": "crime, policing, courts, sentencing, prisons, victims and offenders",
    "Development": "developing and low/middle-income countries, aid, poverty, growth strategies",
    "Economics": "markets, taxation, trade, finance, economic systems and macroeconomic policy",
    "Education": "schools, universities, teaching, curricula, students",
    "Environment": "climate, conservation, pollution, energy transition, animals and nature",
    "Feminism": "gender equality, the women's movement, gender roles and norms",
    "Government": "government itself: political systems and branches (judicial, executive, legislative), "
                  "elections, systems of rule, parties",
    "History": "past events or eras, counterfactual history, how the past is remembered or taught",
    "Hypotheticals": "invented scenarios, fictional characters, 'as X, would you...' role-play, "
                     "worlds with made-up rules",
    "Individual choice": "what a person should do with their own life, career, body or beliefs",
    "International Relations": "relations between states, foreign policy, the EU/UN and other blocs, sanctions",
    "Labour": "work, workers, unions, jobs, wages, automation's effect on employment",
    "Media": "journalism, news, social media, influencers, representation in the media",
    "Medicine": "health, healthcare systems, doctors, drugs, disease, medical and genetic technology",
    "Military": "war, armed forces, defence, terrorism, armed conflict and weapons",
    "Narratives": "motions about a trend, narrative, norm or cultural attitude (e.g. 'opposes the "
                  "romanticisation of...', 'regrets the rise of...') rather than a concrete policy",
    "Philosophy": "ethics, moral theories, metaphysics, the meaning of life, abstract value questions",
    "Policy": "a policy a government should adopt; not rules of private bodies (leagues, companies) "
              "and not how government itself is structured (that is Government)",
    "Relationships": "family, parenting, dating, friendship, marriage and other personal relationships",
    "Religion": "religions, faith, churches and religious institutions, God, spirituality",
    "Rights": "civil and human rights and liberties, bodily autonomy, freedom of speech and movement",
    "Social justice": "minorities and marginalised groups (LGBTQ+, race, disability, class) and "
                      "activism for them",
    "Sport": "sports, athletes, sporting bodies and competitions, e-sports",
    "Technology": "AI, the internet, digital platforms, tech companies and emerging technology",
}
MAX_TAGS = 4
BATCH = 50
N_EXAMPLES = 40
RETRY_WAITS = (10, 30, 60)
# any OpenAI-compatible chat endpoint; defaults to Gemini's free tier
BASE_URL = os.environ.get("TAGGER_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
MODEL = os.environ.get("TAGGER_MODEL", "gemini-flash-latest")

PROMPT = """You tag competitive debate motions by topic.
Tags (use only these, spelled exactly):
{tags}
Give each motion 1 to {max_tags} tags, most relevant first. A motion usually gets both its subject (e.g. Economics) and its type (e.g. Policy, Narratives, Hypotheticals) where one fits.
Examples:
{examples}
Reply with only a JSON object mapping each motion's number to its list of tags."""


def motion_texts(conn):
    """Every motion seen in raw_motions, keyed by motion id."""
    texts = {}
    with conn.cursor() as cur:
        cur.execute("SELECT payload FROM raw_motions")
        rows = cur.fetchall()
    for (seqs,) in rows:
        if not seqs or seqs.get("_err"):
            continue
        for ms in seqs.values():
            for mo in ms if isinstance(ms, list) else []:
                t = (mo.get("t") or "").strip() if isinstance(mo, dict) else ""
                if t:
                    texts.setdefault(mid(t), t)
    return texts


def untagged_motions(conn, texts=None):
    tags = db.get_artifact(conn, "motions_tags", {})
    clean = db.get_artifact(conn, "motions_clean", {})
    texts = motion_texts(conn) if texts is None else texts
    return {k: t for k, t in texts.items()
            if k not in tags and not (clean.get(k) or {}).get("skip")}


def build_prompt(hand, texts):
    """System prompt of the tag glosses plus a sample of hand-labelled motions as examples."""
    labelled = sorted(k for k in hand if k in texts)
    sample = random.Random(0).sample(labelled, min(N_EXAMPLES, len(labelled)))
    examples = "\n".join("%s -> %s" % (texts[k], json.dumps(hand[k])) for k in sample)
    tags = "\n".join("- %s: %s" % kv for kv in TAGS.items())
    return PROMPT.format(tags=tags, max_tags=MAX_TAGS, examples=examples)


def parse_tags(reply, n):
    """Keep only known tags, capped; a motion the model skipped or garbled is left for next run."""
    body = reply.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    got = json.loads(body)
    out = {}
    for i in range(n):
        tg = [t for t in got.get(str(i + 1)) or [] if t in TAGS]
        if tg:
            out[i] = list(dict.fromkeys(tg))[:MAX_TAGS]
    return out


def ask(client, key, system, texts):
    user = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(texts))
    body = {"model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for wait in RETRY_WAITS + (None,):
        r = client.post(BASE_URL + "/chat/completions", headers={"Authorization": "Bearer " + key}, json=body)
        # free tiers answer 429/503 when busy; these usually clear within a minute
        if r.status_code not in (429, 503) or wait is None:
            break
        time.sleep(wait)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def tag_new_motions(conn, log=print, client=None):
    texts = motion_texts(conn)
    todo = untagged_motions(conn, texts)
    if not todo:
        log("motion tagging: nothing new to tag")
        return 0
    key = os.environ.get("TAGGER_API_KEY", "")
    if not key:
        log("motion tagging: TAGGER_API_KEY unset, %d motions left untagged" % len(todo))
        return 0
    hand = db.get_artifact(conn, "motions_tags_hand", {})
    if not hand:
        log("motion tagging: no hand-labelled motions to take tags from, %d left untagged" % len(todo))
        return 0
    # hand labels are keyed by the cleaned text
    clean = {mid(v["t"]): v["t"] for v in db.get_artifact(conn, "motions_clean", {}).values() if v.get("t")}
    system = build_prompt(hand, {**texts, **clean})
    tags = db.get_artifact(conn, "motions_tags", {})
    keys, done = list(todo), 0
    client = client or httpx.Client(timeout=120)
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        try:
            got = parse_tags(ask(client, key, system, [todo[k] for k in chunk]), len(chunk))
        except (httpx.HTTPError, ValueError, KeyError, AttributeError) as e:
            # stop rather than hammer a rate-limited free tier; the rest go next run
            log("motion tagging: batch failed (%s), %d motions left for next run" % (e, len(keys) - i))
            break
        for j, tg in got.items():
            tags[chunk[j]] = tg
        done += len(got)
        db.set_artifact(conn, "motions_tags", tags)
    log("motion tagging: tagged %d new motions" % done)
    return done

