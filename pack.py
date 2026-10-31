"""Numbered code around every site, whole statements, inside a token budget.
A fixed ±N window cut a 15-line call in the trial (both models broke it the same way), so a call/signature
that starts on a site line is always shown to its closing bracket."""
import re
from config import Repo

DEF_LINES = 14        # fallback lines shown from the definition down when its body end is not found
MAX_BODY = 300        # longest definition body shown whole (bug fixes need the whole function, 2026-10-25 gh trial)
WINDOWS = (8, 4, 2, 0)  # tried in order until the pack fits the budget
MENTION = 1           # lines around a comment/doc mention
MAX_CALL = 80         # longest call/signature followed to its end
CHARS_PER_TOKEN = 3.5


def close_paren(src: str, i: int) -> int:
    """Index of the bracket closing the one opened just before i; -1 if not found within reach."""
    depth, end = 1, min(len(src), i + 20000)
    while i < end:
        c = src[i]
        depth += c in "([{"; depth -= c in ")]}"
        if not depth: return i
        i += 1
    return -1


def statement_end(lines: list, line: int, name: str) -> int:
    """Last line of the call/signature of `name` that opens on `line` (1-based); `line` if none."""
    text = "\n".join(lines[line - 1: line - 1 + MAX_CALL])
    first = lines[line - 1]
    m = re.search(rf"\b{re.escape(name)}\b\s*(::<[^>]*>|<[^>(]*>)?\s*\(", first)
    if not m: return line
    c = close_paren(text, m.end())
    return line if c < 0 else line + text.count("\n", 0, c)


def body_end(lines: list, line: int) -> int:
    """Last line (1-based) of the definition starting on `line`: indentation for Python-style blocks, else the
    bracket that closes the first `{` at or after it. Falls back to DEF_LINES."""
    first = lines[line - 1]
    head = "\n".join(lines[line - 1: line - 1 + 12])
    if re.match(r"\s*(async\s+def|def|class)\b", first):
        ind = len(first) - len(first.lstrip())
        end = line
        for i in range(line, min(len(lines), line + MAX_BODY)):
            t = lines[i]
            if t.strip() and len(t) - len(t.lstrip()) <= ind and not t.lstrip().startswith((")", "]", "}")): break
            if t.strip(): end = i + 1
        return end
    text = "\n".join(lines[line - 1: line - 1 + MAX_BODY])
    b = text.find("{")
    if b < 0 or b > len(head): return line + DEF_LINES
    c = close_paren(text, b + 1)
    return line + DEF_LINES if c < 0 else line + text.count("\n", 0, c)


TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.(py|rs|go)$|[^/]*\.(test|spec)\.[jt]sx?$)")
TEST_DEF = re.compile(r"^\s*(?:async\s+def|def|(?:pub\s+)?(?:async\s+)?fn|it|test|describe)\b")
MAX_WHOLE_TESTS = 30   # the pack token budget is the real limit (build shrinks windows below 4 = no whole tests)


def enclosing_def(lines: list, line: int) -> int | None:
    """Start line of the function enclosing `line` (nearest less-indented def/fn/it/test above it)."""
    me = len(lines[line - 1]) - len(lines[line - 1].lstrip())
    for i in range(line - 1, max(0, line - 400), -1):
        t = lines[i - 1]
        if t.strip() and len(t) - len(t.lstrip()) < me and TEST_DEF.match(t): return i
    return None


def ranges(repo: Repo, sites: list, name: str, window: int, mentions_full: bool) -> dict:
    """file -> merged [(start, end)] 1-based inclusive."""
    by, cache, whole = {}, {}, 0
    for s in sites:
        if s.kind == "mention" and not mentions_full: continue
        lines = cache.setdefault(s.file, (repo.root / s.file).read_text(errors="replace").split("\n"))
        lo, hi = {"def": (s.line, body_end(lines, s.line)), "mention": (s.line - MENTION, s.line + MENTION), "import": (s.line, s.line)}.get(s.kind, (s.line - window, s.line + window))
        hi = max(hi, statement_end(lines, s.line, name) + (1 if window else 0))
        calls = s.kind == "ref" or re.search(rf"\b{re.escape(name)}\s*\(", lines[s.line - 1])   # tests are often name-search hits only
        if repo.whole_tests and calls and s.kind != "def" and window >= 4 and TEST_FILE.search(s.file) and whole < MAX_WHOLE_TESTS:
            start = enclosing_def(lines, s.line)
            if start: lo, hi, whole = min(lo, start), max(hi, body_end(lines, start)), whole + 1
        by.setdefault(s.file, []).append((max(1, lo), min(len(lines), hi)))
    merged = {}
    for f, rs in by.items():
        rs.sort(); m = [list(rs[0])]
        for a, b in rs[1:]:
            if a <= m[-1][1] + 1: m[-1][1] = max(m[-1][1], b)
            else: m.append([a, b])
        merged[f] = m
    return merged


def render(repo: Repo, res: dict, rng: dict, one_liners: list) -> str:
    sites = res["sites"]
    kinds = {k: sum(s.kind == k for s in sites) for k in ("def", "ref", "import", "mention", "use?")}
    src = "language server (complete: definition, imports, every caller)" if res["tier"] == 1 else \
        "name search only (no language server for this language: some hits may be other things with the same name)"
    out = [f"`{res['name']}` defined at {res['def']}. {len(sites)} sites from {src}: "
           + ", ".join(f"{v} {k}" for k, v in kinds.items() if v) + ".",
           "Mentions = the name in comments, docs, strings or other languages: update them if the change makes them wrong.", ""]
    for f, rs in rng.items():
        lines = (repo.root / f).read_text(errors="replace").split("\n")
        for a, b in rs:
            out.append(f"=== {f} (lines {a}-{b}) ===")
            out += [f"{i}| {lines[i - 1]}" for i in range(a, b + 1)]
            out.append("")
    if one_liners:
        out.append("Not shown in full (budget): " + str(len(one_liners)) + " sites, one line each:")
        for s in one_liners:
            text = (repo.root / s.file).read_text(errors="replace").split("\n")[s.line - 1].strip()
            out.append(f"{s.file}:{s.line} ({s.kind}) {text[:160]}")
    return "\n".join(out)


def build(repo: Repo, res: dict, budget: int | None = None) -> str:
    """Biggest pack that fits: shrink caller windows, then drop mention code, then list sites one per line."""
    budget = budget or repo.pack_budget
    sites, name = res["sites"], res["name"]
    text = ""
    for mentions_full in (True, False):
        for w in WINDOWS:
            rest = [s for s in sites if s.kind == "mention"] if not mentions_full else []
            text = render(repo, res, ranges(repo, sites, name, w, mentions_full), rest)
            if len(text) / CHARS_PER_TOKEN <= budget: return text
    shown, cost = [], 0   # still too big: definition + as many callers as fit, rest one line each
    for s in sites:
        cost += 2 * w + 3 if s.kind != "mention" else 0
        if s.kind == "def" or cost * 40 / CHARS_PER_TOKEN < budget * 0.7: shown.append(s)
    rest = [s for s in sites if s not in shown]
    return render(repo, res, ranges(repo, shown, name, 0, False), rest)
