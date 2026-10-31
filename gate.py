"""Nothing missed: every watched site was changed or acknowledged ("checked, no change"),
and no line changed only its indentation (both blind-agent failures in the trial were re-indents that still parsed)."""
import re, subprocess
import state
from config import Repo

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def hunks(repo: Repo, file: str) -> list:
    """[(old_start, old_len, new_start, new_len, removed_lines, added_lines)] from git diff -U0 HEAD."""
    if not state.git(repo, "ls-files", "--", file).strip():   # new untracked file: all of it is added
        n = len((repo.root / file).read_text(errors="replace").split("\n"))
        return [(0, 0, 1, n, [], [])]
    out, cur = [], None
    for row in state.git(repo, "diff", "-U0", "HEAD", "--", file).splitlines():
        m = HUNK.match(row)
        if m:
            cur = [int(m[1]), int(m[2] or 1), int(m[3]), int(m[4] or 1), [], []]; out.append(cur)
        elif cur and row.startswith("-") and not row.startswith("---"): cur[4].append(row[1:])
        elif cur and row.startswith("+") and not row.startswith("+++"): cur[5].append(row[1:])
    return [tuple(h) for h in out]


def head_line(repo: Repo, file: str, line: int) -> str | None:
    r = subprocess.run(["git", "show", f"HEAD:{file}"], cwd=repo.root, capture_output=True, text=True)
    rows = r.stdout.split("\n") if r.returncode == 0 else []
    return rows[line - 1] if 0 < line <= len(rows) else None


def touched(repo: Repo, site: dict, hs: list) -> bool:
    """Site line (recorded before or after edits) falls inside a changed hunk."""
    l, e = site["line"], site.get("end", site["line"])
    hit = lambda start, n: start <= e and l < start + n if n else l <= start < e   # n=0: pure insert after `start`
    if head_line(repo, site["file"], l) == site["text"]:          # recorded on the HEAD version
        return any(hit(os_, ol) for os_, ol, *_ in hs)
    return any(hit(ns, nl) for _, _, ns, nl, *_ in hs)            # recorded after edits: working-tree numbering


def indent_changes(hs: list, file: str) -> list:
    """Lines whose indentation changed: same text with new leading whitespace, or (hunks that replace
    line for line) a replaced line with a different indent than the line it replaced."""
    out = []
    ind = lambda t: len(t) - len(t.lstrip())
    for _, _, ns, _, rem, add in hs:
        rem_by = {r.strip(): r for r in rem if r.strip()}
        old = set(rem_by)
        for i, a in enumerate(add):
            r = rem_by.get(a.strip())
            if r is None and len(rem) == len(add) and a.strip() and rem[i].strip(): r = rem[i]
            if r is not None and ind(r) != ind(a) and not wrapped(add, i, ind(r), old): out.append(f"{file}:{ns + i}")
    return out


def wrapped(add: list, i: int, old_ind: int, old: set) -> bool:
    """add[i] moved deeper because a NEW block opener (`try:`, `if x:`, `{`) was added above it, e.g. code wrapped in
    try/except (2026-10-25: the gate flagged such a fix and the model broke it to 'undo'). Dedents are never wrapped."""
    me = len(add[i]) - len(add[i].lstrip())
    if me <= old_ind: return False
    floor = me                       # lowest indent seen between the opener and add[i]: the opener must enclose them all
    for a in reversed(add[:i]):
        if not a.strip(): continue
        lvl = len(a) - len(a.lstrip())
        if lvl < floor:
            t = a.rstrip()
            if a.strip() not in old and (t.endswith(":") or t.endswith("{")): return True
            floor = lvl
    return False


KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "new", "await", "typeof", "super", "match"}
BODY_AFTER = re.compile(r"^\s*(?::[^;{]*)?\s*(?:\{|=>)")   # a TS method signature: `) {` or `): T {` (a call ends `);`)


def signature_spans(lang, src: str) -> list:
    """[(name, first, last)] 1-based: every function signature in a file (def line to the line that opens its body)."""
    from pack import close_paren
    pat = re.compile(lang.decl.replace("{name}", r"(\w+)"), re.M)
    rows, out = src.split("\n"), []
    for i, row in enumerate(rows):
        for m in pat.finditer(row):
            name = next((g for g in m.groups() if g), None)
            call = name and re.search(rf"\b{re.escape(name)}\s*(?:<[^>(]*>)?\s*\(", row)
            if not call or name in KEYWORDS: continue
            text = "\n".join(rows[i:i + 80]); c = close_paren(text, call.end())
            if c < 0: continue
            if row.lstrip().startswith(name) and not BODY_AFTER.match(text[c + 1:c + 200]): continue   # a call, not a method
            last = i + 1 + text.count("\n", 0, c)
            while last < min(len(rows), i + 6) and not ("{" in rows[last - 1] or rows[last - 1].rstrip().endswith(":")): last += 1   # return type lines
            out.append((name, i + 1, last)); break
    return out


def changed_signatures(repo: Repo) -> list:
    """[(name, file, HEAD line)] functions whose signature lines (HEAD version) a hunk touches. Body-only edits are not listed:
    their callers need no change, and watching them would make every bug fix ack a list of callers."""
    from config import lang_of
    out = []
    for f in state.changed_files(repo):
        lang = lang_of(repo, f)
        old = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=repo.root, capture_output=True, text=True)
        if not lang or old.returncode: continue                     # new file: no callers yet
        hs = hunks(repo, f)
        for name, l, e in signature_spans(lang, old.stdout):
            if any((os_ <= e and l < os_ + ol) if ol else l <= os_ < e for os_, ol, *_ in hs):
                out.append((name, f, l))
    return out


def gate(repo: Repo) -> dict:
    st = state.load(repo); acked = set(st["ack"])
    files = state.changed_files(repo)
    hs = {f: hunks(repo, f) for f in files}
    missed, unchanged_ok = [], []
    for name, w in st["watch"].items():
        for s in w["sites"]:
            key = f"{s['file']}:{s['line']}"
            if s["kind"] == "import" or touched(repo, s, hs.get(s["file"], [])): continue   # imports never need an edit
            ok = key in acked or f"all:{name}" in acked or (s["kind"] == "mention" and f"mentions:{name}" in acked)
            (unchanged_ok if ok else missed).append({"symbol": name, "site": key, "kind": s["kind"], "text": s["text"].strip()[:160]})
    indents = [k for f in files for k in indent_changes(hs[f], f) if f"indent:{k}" not in acked]
    return {"untouched": missed, "acked": unchanged_ok, "indent_only": indents, "files": files}
