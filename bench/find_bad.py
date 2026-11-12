import sys
from pathlib import Path
from lxml import objectify
bad = []
for f in sorted(Path(sys.argv[1]).iterdir()):
    try:
        objectify.parse(open(f, encoding="utf-8"))
    except Exception as e:
        bad.append((f.name, type(e).__name__, str(e)[:120]))
print(len(bad)); [print(b) for b in bad[:5]]
