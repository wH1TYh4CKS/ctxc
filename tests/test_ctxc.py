"""ctxc on a tiny real git repo (Python, pyright): find, sites, check (baseline, gate, indent), ship."""
import json, subprocess, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402

LIB = '''def area(w: int, h: int) -> int:
    return w * h
'''
APP = '''from lib import area


def main() -> None:
    # area of the box
    print(area(2, 3))
    if True:
        print(area(4, 5))
'''


def git(root, *a):
    subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))       # cache under tmp, not the real ~/.cache
    root = tmp_path / "proj"; root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'p'\n")
    (root / "lib.py").write_text(LIB); (root / "app.py").write_text(APP)
    git(root, "init", "-q", "-b", "main"); git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    return root


def run(root, *args):
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): rc = cli.main(["-C", str(root), "--json", *args])
    return rc, json.loads(buf.getvalue())


def test_find_exact_and_partial(repo):
    _, r = run(repo, "find", "are")
    assert not r["exact"] and r["partial"][0]["file"] == "lib.py"
    _, r = run(repo, "find", "area")
    assert [(d["file"], d["line"]) for d in r["exact"]] == [("lib.py", 1)]


def test_sites_lists_def_import_calls_mention(repo):
    _, r = run(repo, "sites", "area")
    kinds = {(s["file"], s["line"]): s["kind"] for s in r["sites"]}
    assert kinds[("lib.py", 1)] == "def" and kinds[("app.py", 1)] == "import"
    assert kinds[("app.py", 6)] == "ref" and kinds[("app.py", 8)] == "ref" and kinds[("app.py", 5)] == "mention"
    assert r["tier"] == 1


def edit(root, f, old, new):
    p = root / f; p.write_text(p.read_text().replace(old, new))


def test_missed_caller_fails_then_passes(repo):
    run(repo, "sites", "area")
    edit(repo, "lib.py", "h: int)", "h: int, d: int)")
    edit(repo, "app.py", "area(2, 3)", "area(2, 3, 1)")            # second caller missed
    rc, r = run(repo, "check")
    assert rc == 1 and any("compile" in x for x in r["reasons"]) and any("watched" in x for x in r["reasons"])
    edit(repo, "app.py", "area(4, 5)", "area(4, 5, 1)")
    rc, r = run(repo, "check")
    assert r["reasons"] == ["1 watched sites not changed and not acknowledged"]   # only the comment is left
    rc, r = run(repo, "check", "--ack", "mentions:area")
    assert rc == 0 and r["pass"]


def test_indent_change_flagged(repo):
    run(repo, "sites", "area")
    edit(repo, "app.py", "    if True:\n        print(area(4, 5))", "    if True:\n        pass\n    print(area(4, 5))")
    rc, r = run(repo, "check", "--ack", "mentions:area", "all:area")
    assert rc == 1 and r["gate"]["indent_only"] == ["app.py:9"]


def test_ship_needs_pass_and_never_main(repo):
    rc, r = run(repo, "ship", "x")
    assert not r["shipped"]
    run(repo, "sites", "area")
    edit(repo, "app.py", "# area of the box", "# area of the rectangle")
    rc, r = run(repo, "check", "--ack", "all:area")
    assert r["pass"]
    edit(repo, "app.py", "rectangle", "rect")                       # changed after check
    assert not run(repo, "ship", "x")[1]["shipped"]
    run(repo, "check", "--ack", "all:area")
    rc, r = run(repo, "ship", "Reword area comment")
    assert r["shipped"] and r["branch"].startswith("ctx/") and r["branch"].endswith("reword-area-comment") and not r["pushed"]
    out = subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert out == r["branch"]
    main_log = subprocess.run(["git", "log", "--oneline", "main"], cwd=repo, capture_output=True, text=True).stdout
    assert len(main_log.splitlines()) == 1                           # main untouched


def test_signature_change_watches_callers_without_sites(repo):
    edit(repo, "lib.py", "h: int)", "h: int, d: int = 1)")          # compiles: only the gate can catch it
    rc, r = run(repo, "check")
    assert rc == 1 and r["auto_watched"] == ["area"]
    assert {u["site"] for u in r["gate"]["untouched"]} == {"app.py:5", "app.py:6", "app.py:8"}
    rc, r = run(repo, "check", "--ack", "all:area")
    assert rc == 0 and r["pass"]


def test_body_change_watches_nothing(repo):
    edit(repo, "lib.py", "return w * h", "return h * w")
    rc, r = run(repo, "check")
    assert rc == 0 and r["auto_watched"] == [] and not r["gate"]["untouched"]


def test_indent_gate_allows_wrapping_in_new_block():
    """2026-10-25 gh trial: wrapping code in try/except was flagged, the model 'undid' it and broke the fix."""
    import gate
    rem = ["        if x == y:", "            return True"]
    case = lambda add: gate.indent_changes([(0, 0, 10, 0, rem, add)], "f.py")
    assert case(["        try:", "            if x == y:", "                return True", "        except ValueError:", "            pass"]) == []
    assert case(["    if x == y:", "        return True"]) == ["f.py:10", "f.py:11"]              # dedent: still flagged
    assert case(["            if x == y:", "                return True"]) == ["f.py:10", "f.py:11"]  # deeper, no new opener
    assert case(["        if x == y:", "                return True"]) == ["f.py:11"]
