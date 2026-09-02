import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from debate_ratings import db


def main():
    conn = db.connect()
    db.migrate(conn)
    print("schema up to date")


if __name__ == "__main__":
    main()
