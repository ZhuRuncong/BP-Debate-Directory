import sys
from pathlib import Path

from huggingface_hub import snapshot_download

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from debate_ratings import db, motion_search, tagger

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt")


def main():
    path = snapshot_download(MODEL, allow_patterns=list(FILES))
    n = tagger.upload(db.connect(), path, motion_search.EMBEDDER, FILES)
    print("uploaded %d model files to Postgres" % n)


if __name__ == "__main__":
    main()
