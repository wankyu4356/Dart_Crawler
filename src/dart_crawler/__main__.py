"""Entry point: python -m dart_crawler"""
from __future__ import annotations

import json
import sys

from .client import DartAPIError, DartClient


def main() -> int:
    try:
        client = DartClient()
        result = client.list_disclosures(page_count=5)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DartAPIError as exc:
        print(f"DART API error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
