#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

QA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QA_ROOT))

from agents.ontology_lookup import build_compact_lookup_index, lookup_db_path  # noqa: E402


def main() -> int:
    summary = build_compact_lookup_index(lookup_db_path())
    print(summary)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
