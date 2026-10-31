"""Commit a passed change to a work branch and push it when allowed. Never main/master; never a change the check has not passed."""
import datetime, re, subprocess
import state
from config import Repo

PROTECTED = {"main", "master", "trunk", "develop"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "change"


def branch_for(repo: Repo, message: str) -> str:
    cur = state.git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if cur.startswith(repo.branch_prefix): return cur
    return f"{repo.branch_prefix}{datetime.date.today():%Y-%m-%d}-{slug(message)}"


def passed_now(repo: Repo) -> str:
    """'' when the last check passed on exactly this diff, else why not."""
    last = state.load(repo).get("last_check")
    if not last: return "no check has run on this change: run ctx_check first"
    if not last["pass"]: return "the last check failed: fix it and run ctx_check again"
    if last["diff"] != state.diff_hash(repo): return "files changed after the last check: run ctx_check again"
    return ""


def ship(repo: Repo, message: str, push: bool | None = None) -> dict:
    why = passed_now(repo)
    if why: return {"shipped": False, "reason": why}
    if not state.changed_files(repo): return {"shipped": False, "reason": "nothing to commit"}
    br = branch_for(repo, message)
    if br in PROTECTED: return {"shipped": False, "reason": f"refusing to commit on {br}"}
    if br != state.git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(): state.git(repo, "switch", "-c", br)
    state.git(repo, "add", "-A")
    state.git(repo, "commit", "-q", "-m", message + "\n\nChecked by ctxc: no new compile errors, every watched site changed or acknowledged.")
    sha = state.git(repo, "rev-parse", "--short", "HEAD").strip()
    want = repo.push if push is None else push and repo.push
    remote = state.git(repo, "remote").split()
    if not want or not remote:
        return {"shipped": True, "branch": br, "commit": sha, "pushed": False,
                "reason": "push off for this repo" if not want else "repo has no remote"}
    r = subprocess.run(["git", "push", "-u", remote[0], br], cwd=repo.root, capture_output=True, text=True)
    return {"shipped": True, "branch": br, "commit": sha, "pushed": r.returncode == 0, "push_output": (r.stdout + r.stderr).strip()[-400:]}
