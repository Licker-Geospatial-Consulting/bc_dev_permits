import json
from pathlib import Path

for slug in ("north_van", "victoria"):
    rows = json.loads(Path(f"data/processed/{slug}.json").read_text(encoding="utf-8"))
    print("====", slug, len(rows))
    for r in rows[:8]:
        print(f"  addr={r.get('address')!r:45} url=...{(r.get('source_url') or '')[-45:]}")
