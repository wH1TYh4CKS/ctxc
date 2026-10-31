"""ctxc <find|sites|check|ship|baseline|run> — the one entry point for Pi tools, the pipeline and people.
--json prints machine output; text output is capped at [ctxc] tool_cap chars, the rest saved to a file it names."""
import argparse, json, sys, time
from pathlib import Path
import config, find, sites, pack, check, gate, state, ship


def cap(repo, text: str, what: str) -> str:
    if len(text) <= repo.tool_cap: return text
    p = state.repo_dir(repo) / f"{what}-{int(time.time())}.txt"; p.write_text(text)
    return text[: repo.tool_cap] + f"\n... [cut at {repo.tool_cap} chars; full output: {p}]"


def cmd_find(repo, a) -> tuple:
    r = find.find(repo, a.query)
    rows = [f"{d.file}:{d.line}  {d.text}" for d in r["exact"]]
    txt = (f"Definitions of `{a.query}`:\n" + "\n".join(rows) if rows else f"No definition named exactly `{a.query}`.")
    if r["partial"]:
        txt += f"\n\nNames containing `{a.query}` ({r['partial_total']}" + (", first 40" if r["partial_total"] > 40 else "") + "):\n" + \
               "\n".join(f"{d.file}:{d.line}  {d.text}" for d in r["partial"])
    return txt, {k: [vars(d) for d in v] if isinstance(v, list) else v for k, v in r.items()}


def cmd_sites(repo, a) -> tuple:
    res = sites.find_sites(repo, a.name, a.at or "")
    if not state.changed_files(repo): state.forget(repo)   # clean tree = a new task: drop old watches and acks
    state.watch(repo, res)
    return pack.build(repo, res, a.budget), sites.as_json(res)


def verdict(repo, comp: dict, g: dict, t: dict | None) -> dict:
    reasons = []
    for lid, c in comp.items():
        if c["new"]: reasons.append(f"{len(c['new'])} new {lid} compile/type errors")
    if t and not t["pass"]: reasons.append("tests fail")
    if g["untouched"]: reasons.append(f"{len(g['untouched'])} watched sites not changed and not acknowledged")
    if g["indent_only"]: reasons.append(f"{len(g['indent_only'])} lines changed indentation")
    if not g["files"]: reasons.append("no files changed")
    return {"pass": not reasons, "reasons": reasons}


def render_check(v: dict, comp: dict, g: dict, t: dict | None) -> str:
    out = ["PASS" if v["pass"] else "FAIL: " + "; ".join(v["reasons"])]
    for lid, c in comp.items():
        out.append(f"[{lid}] " + ("no compile/type check ran (no check command, or only check_skip paths changed): gate only" if not c["ran"] else
                                  f"{len(c['new'])} new errors" + (":" if c["new"] else "")))
        out += [f"  {e}" for e in c["new"][:40]]
    if t: out.append(f"[tests] {'pass' if t['pass'] else 'FAIL'}" + ("" if t["pass"] else "\n  " + "\n  ".join(t["tail"])))
    if g["untouched"]:
        out.append("Watched sites you did not change. Fix each one, or if it truly needs no change, acknowledge it "
                   "(ack = the site key, mentions:<symbol> for all its mentions, or all:<symbol> for every site of it):")
        out += [f"  {u['site']} ({u['symbol']}, {u['kind']}) {u['text']}" for u in g["untouched"][:60]]
    if g["indent_only"]:
        out.append("Lines whose indentation changed (often an accident that changes what code runs). "
                   "Undo, or acknowledge with ack = indent:<file:line>:")
        out += [f"  {k}" for k in g["indent_only"][:40]]
    out.append("Changed files: " + (", ".join(g["files"]) or "none"))
    return "\n".join(out)


AUTO_MAX = 8   # each one starts a language server


def def_line(repo, name: str, file: str, head_line: int) -> int | None:
    """Working-tree line of `name`'s definition in `file`, nearest the line it had at HEAD; None if gone (renamed)."""
    lines = [d.line for d in find.definitions(repo, name) if d.file == file]
    return min(lines, key=lambda l: abs(l - head_line)) if lines else None


def watch_changed(repo) -> list:
    """Watch the sites of every function whose signature changed, even if the model never called sites on it."""
    st, added = state.load(repo), []
    for name, file, head_line in gate.changed_signatures(repo)[:AUTO_MAX]:
        line = name not in st["watch"] and def_line(repo, name, file, head_line)
        if not line: continue
        try: state.watch(repo, sites.find_sites(repo, name, f"{file}:{line}"))
        except LookupError: continue
        added.append(name)
    return added


def cmd_check(repo, a) -> tuple:
    if a.ack: state.ack(repo, a.ack)
    auto = watch_changed(repo)
    comp, g = check.compile_check(repo), gate.gate(repo)
    t = check.tests(repo) if a.tests else None
    v = verdict(repo, comp, g, t)
    st = state.load(repo); st["last_check"] = {"pass": v["pass"], "diff": state.diff_hash(repo), "at": time.time()}; state.save(repo, st)
    text = render_check(v, comp, g, t)
    if auto: text += "\nSignature changed, so all its sites are now watched: " + ", ".join(auto)
    return text, {**v, "compile": comp, "gate": g, "tests": t, "auto_watched": auto}


def cmd_ship(repo, a) -> tuple:
    r = ship.ship(repo, a.message, None if not a.no_push else False)
    if not r["shipped"]: return f"NOT SHIPPED: {r['reason']}", r
    return (f"Committed {r['commit']} on branch {r['branch']}; " +
            ("pushed." if r["pushed"] else f"not pushed ({r.get('reason') or r.get('push_output')}).")), r


def cmd_baseline(repo, a) -> tuple:
    out = {}
    for lang in repo.langs.values():
        cmd = check.expand(repo, lang, []) if lang.check and "{changed}" not in lang.check else None
        out[lang.id] = len(check.baseline(repo, lang, cmd, [])) if cmd else "per-file check, computed on demand"
    return "Baseline errors at HEAD: " + json.dumps(out), out


def cmd_run(repo, a) -> tuple:
    import loop
    repo.whole_tests = repo.whole_tests or a.whole_tests
    r = loop.run(repo, a.task, a.symbol, a.url, a.model, a.tries, a.tests, a.log, a.max_tokens)
    return json.dumps(r, indent=1), r


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctxc", description=__doc__)
    p.add_argument("-C", "--repo", default=".", help="any folder inside the git repo")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("find", help="where is a name defined"); s.add_argument("query")
    s = sub.add_parser("sites", help="every use of a symbol, with code; starts watching it")
    s.add_argument("name"); s.add_argument("--at", help="file or file:line of the definition"); s.add_argument("--budget", type=int)
    s = sub.add_parser("check", help="compile check vs HEAD + nothing-missed gate")
    s.add_argument("--ack", nargs="*", default=[]); s.add_argument("--tests", action="store_true")
    s = sub.add_parser("ship", help="commit a passed change to a work branch (+ push if allowed)")
    s.add_argument("message"); s.add_argument("--no-push", action="store_true")
    sub.add_parser("baseline", help="compute compile errors at HEAD now (else done on first check)")
    s = sub.add_parser("run", help="no-agent loop: pack → model → edits → check, 3 tries")
    s.add_argument("task"); s.add_argument("--symbol", action="append", required=True, help="name or name@file:line")
    s.add_argument("--url", default="http://127.0.0.1:8095/v1"); s.add_argument("--model", default="trial"); s.add_argument("--tries", type=int, default=3)
    s.add_argument("--tests", action="store_true", help="also run the repo's tests each try")
    s.add_argument("--max-tokens", type=int, default=6144, help="reply limit per try (raise for thinking models)")
    s.add_argument("--whole-tests", action="store_true", help="show each test that calls a brief symbol whole")
    s.add_argument("--log", default="", help="write the whole conversation (prompts, replies, feedback) to this JSON file")
    return p


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    repo = config.load(a.repo)
    fn = {"find": cmd_find, "sites": cmd_sites, "check": cmd_check, "ship": cmd_ship, "baseline": cmd_baseline, "run": cmd_run}[a.cmd]
    try: text, data = fn(repo, a)
    except (LookupError, TimeoutError, RuntimeError) as e:
        print(json.dumps({"error": str(e)}) if a.json else f"ctxc {a.cmd}: {e}"); return 2
    print(json.dumps(data, default=str) if a.json else cap(repo, text, a.cmd))
    return 0 if a.cmd != "check" or data.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
