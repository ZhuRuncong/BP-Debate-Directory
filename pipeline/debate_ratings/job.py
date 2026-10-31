"""One pipeline run in a process of its own, so its memory goes back when it ends.

Python keeps what a run allocated for as long as the process lives, and the API
lives all month. Stdout is the log; the last line carries the outcome.
"""
import json
import sys

from . import db, pipeline

RESULT = "\x02result "
ERROR = "\x02error "


def main():
    conn = None
    try:
        conn = db.connect()
        result = pipeline.run_pipeline(conn, **json.loads(sys.argv[1]))
    except Exception as e:
        print(ERROR + "%s: %s" % (type(e).__name__, e), flush=True)
        return 1
    finally:
        if conn is not None:
            conn.close()
    print(RESULT + json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
