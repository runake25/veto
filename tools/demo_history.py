#!/usr/bin/env python3
"""Put honest, real history into a demo workspace — through the product's own API.

The implementation lives in ``veto/demo_history.py`` so the runner can replay the
same history on boot when ``VETO_DEMO_HISTORY=1`` and the record is empty (a free
instance has an ephemeral disk). This CLI runs it against any runner:

    python3 tools/demo_history.py                          # http://127.0.0.1:8787
    python3 tools/demo_history.py --base https://veto-bracken-field.onrender.com
    python3 tools/demo_history.py --skip-routines

It spends sandbox money only (PAYPAL_LIVE=1 with a vaulted sandbox wallet).
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veto.demo_history import DEFAULT_BASE, populate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="runner origin")
    parser.add_argument("--skip-routines", action="store_true", help="do not run the standing jobs")
    args = parser.parse_args()
    summary = populate(args.base, skip_routines=args.skip_routines)
    print(f"\n{summary['requests']} requests · {summary['captured']} captured · "
          f"{summary['awaiting']} awaiting")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
