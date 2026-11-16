# -*- coding: utf-8 -*-
import os
import re
import sys
import time
from pathlib import Path

OUTBOX_DIR = Path(__file__).resolve().parent / "bridge_outbox"
OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    text = " ".join(sys.argv[1:]).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    clean = re.sub(r"[\r\n]+", " ", text).strip()
    if not clean:
        print("Empty reply text", file=sys.stderr)
        sys.exit(1)
    fname = f"reply_{int(time.time() * 1000)}.txt"
    tmp = OUTBOX_DIR / f".{fname}.tmp"
    final = OUTBOX_DIR / fname
    tmp.write_text(clean, encoding="utf-8")
    os.replace(tmp, final)
    print(f"Queued to {final.name}: {clean}")
