"""python -m vidgen --thought \"...\""""
from __future__ import annotations

import argparse
import sys

from vidgen.sufi.engine import generate, result_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="School of Sufi — turn a reflection into a short video")
    parser.add_argument("--thought", required=True, help="The reflection, thought, or voice-note transcript")
    parser.add_argument("--no-publish", action="store_true", help="Render only. Do not call YouTube or the webhook")
    args = parser.parse_args(argv)
    result = generate(args.thought, publish=not args.no_publish)
    print(result_json(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
