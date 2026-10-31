"""Settings for one repo: [ctxc] in spaces.toml, language table, per-repo overrides. No other module reads the toml."""
import os, shutil, subprocess, tomllib
from dataclasses import dataclass, field
from pathlib import Path

SPACES_TOML = Path(os.environ.get("CTXC_CONFIG", Path.home() / "spaces/spaces.toml"))


@dataclass
class Lang:
    id: str
    exts: list
    lsp: list            # resolved server command, [] = no language server (tier 2)
    lsp_id: str
    lsp_wait: str
    lsp_init: dict
    lsp_twice: bool
    check: list          # unexpanded: {repo} {tsconfig} {changed}
    check_env: dict
    check_skip: list     # path prefixes the project itself never type-checks (e.g. tests/ in dynamic-Python repos)
    error: str
    decl: str
    false: str


@dataclass
class Repo:
    root: Path
    push: bool
    branch_prefix: str
    pack_budget: int
    tool_cap: int
    cache: Path
    langs: dict = field(default_factory=dict)   # id -> Lang, only languages this repo has
    tsconfig: str = ""
    test: list = field(default_factory=list)
    whole_tests: bool = False   # pack shows each calling test function whole (no-agent bug fixes need the tests they break)


def load_toml() -> dict:
    return tomllib.load(open(SPACES_TOML, "rb")).get("ctxc", {})


def git_root(path: Path) -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True)
    if out.returncode: raise SystemExit(f"ctxc: {path} is not inside a git repo")
    return Path(out.stdout.strip())


def expand(arg: str, root: Path) -> str:
    return arg.replace("{repo}", str(root))


def pick_server(candidates: list, root: Path) -> list:
    """First candidate whose program exists (repo-local first, then PATH)."""
    for cmd in candidates:
        prog = expand(cmd[0], root)
        if os.path.isabs(prog) and os.access(prog, os.X_OK) or shutil.which(prog):
            return [expand(a, root) for a in cmd]
    return []


def tracked_exts(root: Path) -> set:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout
    return {os.path.splitext(f)[1] for f in out.splitlines()}


def has_lang(root: Path, spec: dict, exts: set) -> bool:
    """A marker file (Cargo.toml, package.json...) or any tracked file of the language."""
    return any((root / d).exists() for d in spec.get("detect", [])) or bool(exts & set(spec.get("exts", [])))


def make_lang(lid: str, spec: dict, over: dict, root: Path) -> Lang:
    s = {**spec, **{k: v for k, v in over.items() if k in spec or k in ("check_env", "check_skip")}}
    return Lang(id=lid, exts=s["exts"], lsp=pick_server(s.get("lsp", []), root), lsp_id=s.get("lsp_id", lid),
                lsp_wait=s.get("lsp_wait", ""), lsp_init=s.get("lsp_init", {}), lsp_twice=s.get("lsp_twice", False),
                check=s.get("check", []), check_env=s.get("check_env", {}), check_skip=s.get("check_skip", []), error=s["error"], decl=s["decl"], false=s["false"])


def load(path: Path | str = ".") -> Repo:
    cfg = load_toml(); root = git_root(Path(path).resolve())
    over = cfg.get("repos", {}).get(str(root), {})
    r = Repo(root=root, push=over.get("push", cfg.get("push", False)), branch_prefix=cfg.get("branch_prefix", "ctx/"),
             pack_budget=cfg.get("pack_budget", 12000), tool_cap=cfg.get("tool_cap", 12000),
             cache=Path(os.path.expanduser(cfg.get("cache", "~/.cache/ctxc"))), tsconfig=over.get("tsconfig", ""),
             test=over.get("test", []), whole_tests=cfg.get("pack_whole_tests", False))
    exts = tracked_exts(root)
    for lid, spec in cfg.get("langs", {}).items():
        if has_lang(root, spec, exts) or (lid == over.get("lang")):
            r.langs[lid] = make_lang(lid, spec, over, root)
    if not r.tsconfig and "ts" in r.langs:
        r.tsconfig = next((p for p in ("tsconfig.json", "src/tsconfig.json") if (root / p).exists()), "tsconfig.json")
    return r


def lang_of(repo: Repo, file: str) -> Lang | None:
    ext = Path(file).suffix
    return next((l for l in repo.langs.values() if ext in l.exts), None)
