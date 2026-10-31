"""Compile/type check per language. Only NEW errors count: the same check at HEAD is the baseline,
compared without line/column numbers (lines move when code is added)."""
import collections, hashlib, json, os, re, subprocess
from dataclasses import replace
from config import Repo, Lang
import state

TIMEOUT = 3600


def expand(repo: Repo, lang: Lang, files: list) -> list | None:
    """Check command for these files; None when it needs {changed} and there are none."""
    mine = [f for f in files if os.path.splitext(f)[1] in lang.exts and (repo.root / f).exists()
            and not any(f.startswith(p) for p in lang.check_skip)]
    out = []
    for a in lang.check:
        if a == "{changed}":
            if not mine: return None
            out += mine
        else: out.append(a.replace("{repo}", str(repo.root)).replace("{tsconfig}", repo.tsconfig))
    return out


def run(repo: Repo, lang: Lang, cmd: list, env: dict | None = None) -> list:
    """Error lines; a crash with no error lines returns its last lines as one error."""
    try:
        r = subprocess.run(cmd, cwd=repo.root, capture_output=True, text=True, timeout=TIMEOUT, env={**os.environ, **lang.check_env, **(env or {})})
    except FileNotFoundError: return [f"check command not found: {cmd[0]}"]
    except subprocess.TimeoutExpired: return [f"check timed out after {TIMEOUT}s: {' '.join(cmd[:4])}"]
    text = r.stdout + "\n" + r.stderr
    errs = [l for l in text.splitlines() if re.search(lang.error, l)]
    if not errs and r.returncode and not re.search(r"\b0 errors?\b", text):
        errs = ["check command failed (exit %d): %s" % (r.returncode, " | ".join(text.strip().splitlines()[-4:]))]
    return errs


def norm(e: str) -> str:
    e = re.sub(r"\(\d+,\d+\)", "", e)
    return re.sub(r":\d+(:\d+)?(?=[:\s-])", "", e).strip()


def baseline_path(repo: Repo, lang: Lang, cmd: list):
    key = hashlib.sha256(json.dumps(cmd).encode()).hexdigest()[:10]
    return state.repo_dir(repo) / f"base-{state.head(repo)[:12]}-{lang.id}-{key}.json"


LINKS = ("node_modules", ".venv", "venv")   # big untracked dirs the HEAD copy borrows (symlink) instead of rebuilding


def head_tree(repo: Repo):
    """A detached git worktree of HEAD in the cache, never the user's files (a stash on a live repo can lose
    work when other agents write meanwhile). One per HEAD; older ones are removed."""
    wt = state.repo_dir(repo) / f"wt-{state.head(repo)[:12]}"
    if not wt.exists():
        for old in state.repo_dir(repo).glob("wt-*"):
            subprocess.run(["git", "worktree", "remove", "--force", str(old)], cwd=repo.root, capture_output=True)
        state.git(repo, "worktree", "add", "--detach", "-q", str(wt), "HEAD")
        for name in LINKS:
            if (repo.root / name).is_dir() and not (wt / name).exists(): (wt / name).symlink_to(repo.root / name)
    return replace(repo, root=wt)


def baseline(repo: Repo, lang: Lang, cmd: list, files: list) -> collections.Counter:
    p = baseline_path(repo, lang, cmd)
    if not p.exists():
        head = head_tree(repo)
        base_cmd = expand(head, lang, files)                           # files new since HEAD drop out (not in the copy)
        env = {"CARGO_TARGET_DIR": str(state.repo_dir(repo) / "target")} if lang.id == "rs" else {}
        errs = run(head, lang, base_cmd, env) if base_cmd else []
        p.write_text(json.dumps([norm(e.replace(str(head.root), str(repo.root))) for e in errs]))
    return collections.Counter(json.loads(p.read_text()))


def new_errors(repo: Repo, lang: Lang, cmd: list, files: list) -> list:
    errs = run(repo, lang, cmd)
    extra = collections.Counter(norm(e) for e in errs) - baseline(repo, lang, cmd, files)
    out, left = [], dict(extra)
    for e in errs:
        k = norm(e)
        if left.get(k, 0) > 0: out.append(e); left[k] -= 1
    return out


def compile_check(repo: Repo, files: list | None = None) -> dict:
    """{lang: {"ran": cmd or None, "new": [errors]}} for every language with changed files."""
    files = state.changed_files(repo) if files is None else files
    out = {}
    for lang in repo.langs.values():
        if not any(os.path.splitext(f)[1] in lang.exts for f in files): continue
        cmd = expand(repo, lang, files) if lang.check else None
        out[lang.id] = {"ran": cmd, "new": new_errors(repo, lang, cmd, files) if cmd else []}
    return out


def tests(repo: Repo) -> dict | None:
    if not repo.test: return None
    r = subprocess.run(repo.test, cwd=repo.root, capture_output=True, text=True, timeout=TIMEOUT)
    return {"ran": repo.test, "pass": r.returncode == 0, "tail": (r.stdout + r.stderr).strip().splitlines()[-15:]}
