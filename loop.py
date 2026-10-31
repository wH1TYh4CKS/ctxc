"""No-agent mode (arm B of the 2026-10-24 trial, 12/12): one prompt with the sites pack, the model answers with
SEARCH/REPLACE blocks, the system applies them and runs the check; problems go back, max `tries`."""
import json, re, time, urllib.request
import sites, pack, state, check, gate
from cli import verdict, render_check, watch_changed
from config import Repo

FORMAT = """Reply ONLY with edit blocks, one per change, in this exact form:
FILE: <path>:<line number where the SEARCH text starts>
<<<<<<< SEARCH
<the exact existing lines, copied WITHOUT the "123| " line-number prefix>
=======
<the new lines>
>>>>>>> REPLACE
SEARCH text must match the file exactly (same indentation); copy it from the code above.
If a listed site needs no change, add a line `ACK <file:line>` (or `ACK mentions:<name>` for all mentions of a name)."""
BLOCK = re.compile(r"FILE:\s*([^\s:]+)(?::(\d+))?\s*\n<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n?>>>>>>> REPLACE", re.S)
ACK = re.compile(r"^\s*ACK\b\s*=?\s*`?([^\s`]+)`?\s*$", re.M | re.I)   # "ACK key"; also "ack = key", the Pi-tool wording


def chat(url: str, model: str, msgs: list, max_tokens: int = 6144) -> dict:
    body = json.dumps({"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0.6, "top_p": 0.95, "top_k": 20}).encode()
    req = urllib.request.Request(f"{url}/chat/completions", data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=1800))


def around(src: str, line: int, n: int = 6) -> str:
    rows = src.split("\n")
    return "\n".join(f"{i}| {rows[i - 1]}" for i in range(max(1, line - n), min(len(rows), line + n) + 1))


def apply(repo: Repo, reply: str) -> list:
    """Apply every block; duplicate SEARCH text → the match nearest the given line. Returns problems."""
    problems, blocks = [], BLOCK.findall(reply)
    if not blocks and not ACK.search(reply): return ["no edit blocks found in the reply"]
    for f, line, search, repl in blocks:
        p = repo.root / f.strip("`")
        if not p.is_file(): problems.append(f"{f}: no such file"); continue
        src = p.read_text()
        if re.match(r"^\s*\d+\| ", search): search = re.sub(r"^\s*\d+\| ", "", search, flags=re.M)
        hits = [m.start() for m in re.finditer(re.escape(search), src)]
        if not hits:
            problems.append(f"{f}:{line or '?'}: SEARCH text not found. Current file around that line:\n{around(src, int(line or 1))}"); continue
        at = min(hits, key=lambda i: abs(src.count("\n", 0, i) + 1 - int(line or 0)))
        p.write_text(src[:at] + repl + src[at + len(search):])
    state.ack(repo, ACK.findall(reply))
    return problems


FAILED_PY = re.compile(r"^FAILED\s+([\w/.\-]+\.py)::(?:\w+::)*(\w+)", re.M)
FAILED_RS = re.compile(r"^---- (?:[\w:]+::)?(\w+) stdout ----", re.M)
MAX_FAILED = 3


def failed_tests(repo: Repo, tail: list) -> str:
    """Source of the failing tests (no-agent mode cannot open files; the tail alone made the model loop, attrs 2026-10-25)."""
    import find as finder
    text, out, seen = "\n".join(tail), [], set()
    hits = [(f, n) for f, n in FAILED_PY.findall(text)] + [("", n) for n in FAILED_RS.findall(text)]
    for f, name in hits:
        if name in seen or len(seen) >= MAX_FAILED: continue
        seen.add(name)
        for d in [d for d in finder.definitions(repo, name) if not f or d.file == f][:1]:
            rows = (repo.root / d.file).read_text(errors="replace").split("\n")
            end = pack.body_end(rows, d.line)
            out += [f"=== failing test {name}: {d.file} (lines {d.line}-{end}) ==="] + [f"{i}| {rows[i - 1]}" for i in range(d.line, end + 1)]
    return "\n".join(out)


def prompt(repo: Repo, task: str, symbols: list) -> str:
    parts = [task, ""]
    for s in symbols:
        name, _, at = s.partition("@")
        res = sites.find_sites(repo, name, at)   # context for the model; callers get watched only if a signature changes
        parts += [pack.build(repo, res), ""]
    return "\n".join(parts + [FORMAT])


def run(repo: Repo, task: str, symbols: list, url: str, model: str, tries: int = 3, tests: bool = False, log: str = "", max_tokens: int = 6144) -> dict:
    if not state.changed_files(repo): state.forget(repo)
    msgs = [{"role": "user", "content": prompt(repo, task, symbols)}]
    meta = {"tries": 0, "prompt_tokens": [], "completion_tokens": 0, "pass": False, "t0": time.time()}
    for n in range(1, tries + 1):
        meta["tries"] = n
        r = chat(url, model, msgs, max_tokens); u = r.get("usage", {})
        reply = r["choices"][0]["message"]["content"] or ""
        meta["prompt_tokens"].append(u.get("prompt_tokens", 0)); meta["completion_tokens"] += u.get("completion_tokens", 0)
        problems = apply(repo, reply)
        watch_changed(repo)
        comp, g = check.compile_check(repo), gate.gate(repo)
        t = check.tests(repo) if tests else None
        v = verdict(repo, comp, g, t)
        st = state.load(repo); st["last_check"] = {"pass": v["pass"] and not problems, "diff": state.diff_hash(repo), "at": time.time()}; state.save(repo, st)
        if v["pass"] and not problems: meta["pass"] = True; break
        feedback = "\n".join(problems + [render_check(v, comp, g, t)]).replace("ack = ", "a line ACK ")   # this mode reads ACK lines
        if t and not t["pass"]: feedback += "\n\n" + failed_tests(repo, t["tail"])
        msgs += [{"role": "assistant", "content": reply},
                 {"role": "user", "content": f"Your edits were applied where possible. Problems:\n{feedback}\n\n"
                                             "Fix them. Reply ONLY with new edit blocks (and ACK lines) against the CURRENT files."}]
    meta["seconds"] = round(time.time() - meta.pop("t0"))
    if log: open(log, "w").write(json.dumps({"meta": meta, "messages": msgs + [{"role": "assistant", "content": reply}]}, indent=1))
    return meta
