"""Per-repo working state, kept OUT of the repo (generated files inside a repo broke the agent's grep in the trial):
watched sites, acknowledgements, last check result. Keyed by HEAD so a new commit starts clean."""
import hashlib, json, subprocess
from pathlib import Path
from config import Repo


def git(repo: Repo, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=repo.root, capture_output=True, text=True)
    if check and r.returncode: raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def head(repo: Repo) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def changed_files(repo: Repo) -> list:
    tracked = git(repo, "diff", "--name-only", "HEAD").split()
    new = git(repo, "ls-files", "--others", "--exclude-standard").split()
    return sorted(set(tracked) | set(new))


def diff_hash(repo: Repo) -> str:
    h = hashlib.sha256(git(repo, "diff", "HEAD", "--binary").encode())
    for f in git(repo, "ls-files", "--others", "--exclude-standard").split():
        p = repo.root / f
        if p.is_file(): h.update(f.encode() + p.read_bytes())
    return h.hexdigest()[:16]


def repo_dir(repo: Repo) -> Path:
    d = repo.cache / str(repo.root).strip("/").replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    return d


def path(repo: Repo) -> Path:
    return repo_dir(repo) / f"state-{head(repo)[:12]}.json"


def load(repo: Repo) -> dict:
    p = path(repo)
    return json.loads(p.read_text()) if p.exists() else {"watch": {}, "ack": [], "last_check": None}


def save(repo: Repo, st: dict) -> None:
    path(repo).write_text(json.dumps(st, indent=1))


def watch(repo: Repo, res: dict) -> None:
    """Remember every site of a symbol with its line text, so the gate can find it again after edits."""
    from pack import statement_end
    st, rows = load(repo), {}
    lines = lambda f: rows.setdefault(f, (repo.root / f).read_text(errors="replace").split("\n"))
    st["watch"][res["name"]] = {"def": res["def"], "tier": res["tier"], "sites": [
        {"file": s.file, "line": s.line, "kind": s.kind, "text": lines(s.file)[s.line - 1],
         "end": statement_end(lines(s.file), s.line, res["name"])} for s in res["sites"]]}   # a call's edit can land on its last line
    save(repo, st)


def ack(repo: Repo, keys: list) -> None:
    st = load(repo); st["ack"] = sorted(set(st["ack"]) | set(keys)); save(repo, st)


def forget(repo: Repo) -> None:
    p = path(repo)
    if p.exists(): p.unlink()
