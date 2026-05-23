import re
from pathlib import Path


def slug_run_name(name: str) -> str:
    s = name.strip().lower()
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in s)
    safe = re.sub(r"-+", "-", safe)
    safe = safe.strip("-")
    return safe or "unnamed"


def allocate_group_slug(runs_root: Path, base_slug: str) -> str:
    root = Path(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / base_slug).exists():
        return base_slug
    n = 2
    while (root / f"{base_slug}_{n}").exists():
        n += 1
    return f"{base_slug}_{n}"
