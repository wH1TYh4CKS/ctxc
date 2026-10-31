"""The map: where a name is defined. Name search over declaration patterns, per language; cheap, no server."""
import re, subprocess
from dataclasses import dataclass
from config import Repo

MAX_HITS = 40


@dataclass
class Def:
    file: str     # repo-relative
    line: int
    text: str     # the declaration line, trimmed
    lang: str


def rg(repo: Repo, pattern: str, exts: list, extra: list | None = None) -> list:
    """[(rel file, line, text)] from ripgrep; respects .gitignore."""
    globs = [a for e in exts for a in ("-g", f"*{e}")]
    try:
        r = subprocess.run(["rg", "-n", "--no-heading", "--color", "never", "-M", "400", *(extra or []), *globs, "-e", pattern, "."],
                           cwd=repo.root, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("ctxc: ripgrep (rg) is not on PATH. find/sites/check need it; install ripgrep or fix PATH.")
    if r.returncode > 1: raise SystemExit(f"ctxc: rg failed: {r.stderr.strip()[:300]}")   # 1 = no matches
    out = r.stdout
    hits = []
    for row in out.splitlines():
        f, l, t = row.split(":", 2)
        hits.append((f.removeprefix("./"), int(l), t))
    return hits


def definitions(repo: Repo, name: str, exact: bool = True) -> list:
    pat_name = re.escape(name) if exact else rf"\w*{re.escape(name)}\w*"
    found = []
    for lang in repo.langs.values():
        pat = lang.decl.replace("{name}", pat_name)
        for f, l, t in rg(repo, pat, lang.exts, [] if exact else ["-i"]):
            found.append(Def(f, l, t.strip()[:200], lang.id))
    return found


def find(repo: Repo, query: str) -> dict:
    """Exact definitions of `query`, then partial-name matches, capped."""
    exact = definitions(repo, query, exact=True)
    seen = {(d.file, d.line) for d in exact}
    partial = [d for d in definitions(repo, query, exact=False) if (d.file, d.line) not in seen]
    return {"exact": exact[:MAX_HITS], "partial": partial[:MAX_HITS], "partial_total": len(partial)}
