"""Every use of one symbol: language-server references (callers, imports, the definition) + name-search mentions
(comments, docs, strings, other languages). Tier 2 languages (no server): every name hit is a possible use."""
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from config import Repo, lang_of
from find import definitions, rg
from lsp import Server

ALL_TEXT = ["*"]   # mentions: every file type ripgrep sees


@dataclass(frozen=True)
class Site:
    file: str     # repo-relative
    line: int
    kind: str     # def | ref | import | mention | use? (tier 2)

    def key(self) -> str: return f"{self.file}:{self.line}"


def resolve(repo: Repo, name: str, at: str = "") -> tuple:
    """(rel file, line) of the definition. `at` = 'file' or 'file:line' to pick one when several exist."""
    if at and re.search(r":\d+$", at):
        f, l = at.rsplit(":", 1); return f, int(l)
    defs = [d for d in definitions(repo, name) if not at or d.file == at or d.file.endswith("/" + at)]
    if len(defs) == 1: return defs[0].file, defs[0].line
    if not defs: raise LookupError(f"no definition of `{name}` found" + (f" in {at}" if at else ""))
    raise LookupError(f"`{name}` is defined {len(defs)} times; pass at=<file:line>:\n" +
                      "\n".join(f"  {d.file}:{d.line}  {d.text}" for d in defs[:20]))


def column(text: str, name: str) -> int:
    m = re.search(rf"\b{re.escape(name)}\b", text)
    if not m: raise LookupError(f"`{name}` is not on that line: {text.strip()[:120]}")
    return m.start()


def server_refs(repo: Repo, file: str, line: int, name: str) -> list | None:
    """[(rel, line)] from the language server, None when this language has none (tier 2)."""
    lang = lang_of(repo, file)
    if not lang or not lang.lsp: return None
    path = repo.root / file
    col = column(path.read_text(errors="replace").split("\n")[line - 1], name)
    srv = Server(lang.lsp, repo.root, lang.lsp_id, lang.lsp_wait, lang.lsp_init, lang.lsp_twice)
    try: refs = srv.references(path, line, col)
    finally: srv.stop()
    return [(str(p.relative_to(repo.root)), l) for p, l in refs if p.is_relative_to(repo.root)]


def mentions(repo: Repo, name: str) -> list:
    return [(f, l) for f, l, _ in rg(repo, rf"\b{re.escape(name)}\b", [])]


IMPORT = re.compile(r"^\s*(pub(\([^)]*\))?\s+)?(use|import|from|export\s*\{|export\s+\*|export\s+type\s*\{)\b|^[\s\w,{}]*$")


def is_import(repo: Repo, file: str, line: int) -> bool:
    """An import/use/re-export line, or a bare `name,` line inside a multi-line import list."""
    return bool(IMPORT.search((repo.root / file).read_text(errors="replace").split("\n")[line - 1]))


def find_sites(repo: Repo, name: str, at: str = "") -> dict:
    file, line = resolve(repo, name, at)
    refs = server_refs(repo, file, line, name)
    words = set(mentions(repo, name))
    if refs is None:
        sites = {Site(f, l, "use?") for f, l in words - {(file, line)}} | {Site(file, line, "def")}
        tier = 2
    else:
        ref_set = set(refs) | {(file, line)}
        sites = {Site(f, l, "def" if (f, l) == (file, line) else "import" if is_import(repo, f, l) else "ref") for f, l in ref_set}
        sites |= {Site(f, l, "mention") for f, l in words - ref_set}
        tier = 1
    order = sorted(sites, key=lambda s: (s.kind != "def", s.file, s.line))
    return {"name": name, "def": f"{file}:{line}", "tier": tier, "sites": order}


def as_json(res: dict) -> dict:
    return {**res, "sites": [asdict(s) for s in res["sites"]]}
