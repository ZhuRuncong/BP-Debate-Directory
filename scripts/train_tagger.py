import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from debate_ratings import db, tagger
from debate_ratings.fit import mid

BASE = "sentence-transformers/all-MiniLM-L6-v2"
MAXLEN, BATCH, LR, SEED = 64, 32, 5e-5, 20260830


def load_corpus(conn):
    hand = db.get_artifact(conn, "motions_tags_hand", {})
    clean = db.get_artifact(conn, "motions_clean", {})
    texts = {}
    for v in clean.values():
        if not v.get("skip"):
            texts.setdefault(mid(v["t"]), v["t"])
    tags = sorted({t for v in hand.values() for t in v}, key=str.lower)
    ti = {t: i for i, t in enumerate(tags)}
    rows = []
    for cid, tg in hand.items():
        text = texts.get(cid)
        if not text or not tg:
            continue
        y = np.zeros(len(tags), dtype=np.float32)
        for t in tg:
            y[ti[t]] = 1.0
        rows.append((text, y))
    return rows, tags


class DS(Dataset):
    def __init__(self, rows, ids):
        self.rows = rows
        self.ids = ids

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, i):
        return self.rows[self.ids[i]]


class Tagger(nn.Module):
    def __init__(self, n_out, src=BASE, head_state=None):
        super().__init__()
        self.enc = AutoModel.from_pretrained(src)
        self.drop = nn.Dropout(0.1)
        self.head = nn.Linear(self.enc.config.hidden_size, n_out)
        if head_state:
            self.head.load_state_dict(head_state)

    def pool(self, enc):
        out = self.enc(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        return (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)

    def forward(self, enc):
        return self.head(self.drop(self.pool(enc)))


def prf(pred, gold):
    tp = (pred * gold).sum(0)
    fp = (pred * (1 - gold)).sum(0)
    fn = ((1 - pred) * gold).sum(0)
    p = tp / np.maximum(tp + fp, 1e-9)
    r = tp / np.maximum(tp + fn, 1e-9)
    f = 2 * p * r / np.maximum(p + r, 1e-9)
    micro_p = tp.sum() / max(tp.sum() + fp.sum(), 1e-9)
    micro_r = tp.sum() / max(tp.sum() + fn.sum(), 1e-9)
    micro = 2 * micro_p * micro_r / max(micro_p + micro_r, 1e-9)
    return p, r, f, micro, float(f.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "tagger_model"))
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--resume", metavar="DIR",
                    help="continue training from a saved model dir instead of the base")
    ap.add_argument("--lr", type=float, default=LR)
    ap.add_argument("--pos-weight", choices=("none", "sqrt", "inv"), default="none",
                    help="upweight positives per tag by neg/pos (inv) or its square root")
    args = ap.parse_args()

    torch.set_num_threads(min(12, os.cpu_count() or 4))
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    conn = db.connect()
    rows, TAGS = load_corpus(conn)
    print("labelled motions: %d, tags: %d" % (len(rows), len(TAGS)), flush=True)

    idx = list(range(len(rows)))
    random.Random(SEED).shuffle(idx)
    n_te = len(idx) // 10
    n_va = len(idx) // 10
    te, va, tr = idx[:n_te], idx[n_te:n_te + n_va], idx[n_te + n_va:]
    print("split: train %d / val %d / test %d" % (len(tr), len(va), len(te)), flush=True)

    src, head_state = BASE, None
    if args.resume:
        src = args.resume
        prev = json.load(open(os.path.join(src, "report.json"), encoding="utf-8"))
        if prev["tags"] != TAGS:  # head columns are positional
            raise SystemExit("resume dir was trained on a different tag set")
        head_state = torch.load(os.path.join(src, "head.pt"), map_location="cpu")["head"]
        print("resuming from %s (%d epochs, test micro-F1 %.4f)"
              % (src, prev["epochs"], prev["test"]["micro_f1"]), flush=True)

    tok = AutoTokenizer.from_pretrained(src)

    def collate(batch):
        texts = [b[0] for b in batch]
        ys = torch.tensor(np.stack([b[1] for b in batch]))
        enc = tok(texts, padding=True, truncation=True, max_length=MAXLEN, return_tensors="pt")
        return enc, ys

    model = Tagger(len(TAGS), src, head_state)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = math.ceil(len(tr) / BATCH) * args.epochs
    sched = get_linear_schedule_with_warmup(opt, int(steps * 0.1), steps)
    pw = None
    if args.pos_weight != "none":
        # from the train split only, so the weights carry no val/test signal
        pos = np.stack([rows[i][1] for i in tr]).sum(0)
        ratio = (len(tr) - pos) / np.maximum(pos, 1.0)
        if args.pos_weight == "sqrt":
            ratio = np.sqrt(ratio)
        pw = torch.tensor(ratio, dtype=torch.float32)
        print("pos_weight %s: min %.2f max %.2f" % (args.pos_weight, pw.min(), pw.max()),
              flush=True)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pw)
    dl_tr = DataLoader(DS(rows, tr), batch_size=BATCH, shuffle=True, collate_fn=collate)

    def logits_for(ids):
        model.eval()
        outs, ys = [], []
        with torch.no_grad():
            for enc, y in DataLoader(DS(rows, ids), batch_size=64, collate_fn=collate):
                outs.append(torch.sigmoid(model(enc)).numpy())
                ys.append(y.numpy())
        return np.concatenate(outs), np.concatenate(ys)

    t0 = time.time()
    for ep in range(args.epochs):
        model.train()
        tot = n = 0
        for enc, y in dl_tr:
            opt.zero_grad()
            loss = lossf(model(enc), y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tot += loss.item()
            n += 1
        pv, yv = logits_for(va)
        _, _, _, mi, ma = prf((pv >= 0.5).astype(np.float32), yv)
        print("epoch %d  loss %.4f  val micro-F1 %.4f  macro-F1 %.4f  (%.0fs)"
              % (ep + 1, tot / n, mi, ma, time.time() - t0), flush=True)

    pv, yv = logits_for(va)
    TH = np.full(len(TAGS), 0.5, dtype=np.float32)
    for j in range(len(TAGS)):
        best, bf = 0.5, -1
        for t in np.arange(0.05, 0.95, 0.025):
            pr = (pv[:, j] >= t).astype(np.float32)
            tp = (pr * yv[:, j]).sum()
            fp = (pr * (1 - yv[:, j])).sum()
            fn = ((1 - pr) * yv[:, j]).sum()
            p = tp / max(tp + fp, 1e-9)
            r = tp / max(tp + fn, 1e-9)
            f = 2 * p * r / max(p + r, 1e-9)
            if f > bf:
                bf, best = f, float(t)
        TH[j] = best

    pt, yt = logits_for(te)
    p05, r05, f05, mi05, ma05 = prf((pt >= 0.5).astype(np.float32), yt)
    p, r, f, micro, macro = prf((pt >= TH).astype(np.float32), yt)
    exact = float(((pt >= TH).astype(np.float32) == yt).all(1).mean())
    print("TEST  micro-F1 %.4f  macro-F1 %.4f  (at 0.5: %.4f / %.4f)  exact-set %.3f"
          % (micro, macro, mi05, ma05, exact), flush=True)

    out = args.out
    os.makedirs(out, exist_ok=True)
    model.enc.save_pretrained(out)
    tok.save_pretrained(out)
    torch.save({"head": model.head.state_dict()}, os.path.join(out, "head.pt"))
    counts = np.stack([r[1] for r in rows]).sum(0)
    report = {
        "base_model": BASE, "tags": TAGS, "thresholds": TH.tolist(),
        "n_labelled": len(rows),
        "split": {"train": len(tr), "val": len(va), "test": len(te)},
        "epochs": args.epochs + (prev["epochs"] if args.resume else 0),
        "pos_weight": args.pos_weight,
        "test": {"micro_f1": float(micro), "macro_f1": float(macro),
                 "micro_f1_at_0.5": float(mi05), "macro_f1_at_0.5": float(ma05),
                 "exact_set_match": exact},
        "per_tag": [{"tag": TAGS[j], "support_total": int(counts[j]),
                     "threshold": float(TH[j]), "precision": float(p[j]),
                     "recall": float(r[j]), "f1": float(f[j])}
                    for j in range(len(TAGS))],
    }
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    for e in sorted(report["per_tag"], key=lambda e: e["f1"])[:8]:
        print("  %-22s n=%4d  F1 %.3f" % (e["tag"], e["support_total"], e["f1"]))
    print("saved to", out, flush=True)

    if args.upload:
        n = tagger.upload(conn, out)
        print("uploaded %d model files to Postgres" % n)


if __name__ == "__main__":
    main()
