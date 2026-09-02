import json
import os
import tempfile

from . import db
from .fit import mid

MODEL_NAME = "motion_tagger"
MODEL_FILES = ("config.json", "model.safetensors", "tokenizer.json",
               "tokenizer_config.json", "special_tokens_map.json",
               "head.pt", "report.json")
MAXLEN = 64
MAX_TAGS = 4


class MotionTagger:
    def __init__(self, model_dir):
        import numpy as np
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.np = np
        self.torch = torch
        torch.set_num_threads(min(8, os.cpu_count() or 4))
        rep = json.load(open(os.path.join(model_dir, "report.json"), encoding="utf-8"))
        self.tags = rep["tags"]
        self.thresholds = np.array(rep["thresholds"], dtype=np.float32)
        self.tok = AutoTokenizer.from_pretrained(model_dir)
        self.enc = AutoModel.from_pretrained(model_dir).eval()
        self.head = torch.nn.Linear(self.enc.config.hidden_size, len(self.tags))
        self.head.load_state_dict(
            torch.load(os.path.join(model_dir, "head.pt"), map_location="cpu")["head"])
        self.head.eval()

    def scores(self, texts, batch=64):
        np, torch = self.np, self.torch
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), batch):
                b = self.tok(texts[i:i + batch], padding=True, truncation=True,
                             max_length=MAXLEN, return_tensors="pt")
                h = self.enc(**b).last_hidden_state
                m = b["attention_mask"].unsqueeze(-1).float()
                v = (h * m).sum(1) / m.sum(1).clamp(min=1e-9)
                out.append(torch.sigmoid(self.head(v)).numpy())
        return np.concatenate(out) if out else np.zeros((0, len(self.tags)))

    def predict(self, texts, batch=64):
        np = self.np
        res = []
        for p in self.scores(texts, batch=batch):
            hit = [self.tags[j] for j in np.argsort(-p) if p[j] >= self.thresholds[j]]
            if not hit:
                hit = [self.tags[int(np.argmax(p))]]
            res.append(hit[:MAX_TAGS])
        return res

    @classmethod
    def from_db(cls, conn):
        files = db.get_model_files(conn, MODEL_NAME)
        if not files:
            return None
        tmp = tempfile.mkdtemp(prefix="motion_tagger_")
        for name, body in files.items():
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(body)
        return cls(tmp)


def upload(conn, model_dir):
    n = 0
    for name in MODEL_FILES:
        p = os.path.join(model_dir, name)
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            db.put_model_file(conn, MODEL_NAME, name, f.read())
        n += 1
    return n


def untagged_motions(conn):
    tags = db.get_artifact(conn, "motions_tags", {})
    clean = db.get_artifact(conn, "motions_clean", {})
    todo = {}
    with conn.cursor() as cur:
        cur.execute("SELECT payload FROM raw_motions")
        rows = cur.fetchall()
    for (seqs,) in rows:
        if not seqs or seqs.get("_err"):
            continue
        for ms in seqs.values():
            if not isinstance(ms, list):
                continue
            for mo in ms:
                if not isinstance(mo, dict):
                    continue
                t = (mo.get("t") or "").strip()
                if not t:
                    continue
                k = mid(t)
                if k in tags or k in todo:
                    continue
                c = clean.get(k)
                if c and c.get("skip"):
                    continue
                todo[k] = t
    return todo


def tag_new_motions(conn, log=print):
    todo = untagged_motions(conn)
    if not todo:
        log("motion tagging: nothing new to tag")
        return 0
    try:
        tagger = MotionTagger.from_db(conn)
    except ImportError:
        log("motion tagging: torch/transformers unavailable, %d motions left untagged"
            % len(todo))
        return 0
    if tagger is None:
        log("motion tagging: no model uploaded, %d motions left untagged" % len(todo))
        return 0
    keys = list(todo)
    preds = tagger.predict([todo[k] for k in keys])
    tags = db.get_artifact(conn, "motions_tags", {})
    for k, tg in zip(keys, preds, strict=True):
        tags[k] = tg
    db.set_artifact(conn, "motions_tags", tags)
    log("motion tagging: tagged %d new motions" % len(keys))
    return len(keys)
