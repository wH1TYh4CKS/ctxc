"""One language server, started per call: initialize, wait until ready, references, stop.
Quirks from the 2026-10-24 trials: rust-analyzer needs to reach quiescent; pyright's first answer can be empty
and it needs workspaceFolders + a workspace/configuration reply."""
import json, os, queue, subprocess, threading, time
from urllib.parse import unquote, urlparse
from pathlib import Path


class Server:
    def __init__(self, cmd: list, root: Path, lang_id: str, wait: str = "", init: dict | None = None, twice: bool = False):
        self.root, self.lang_id, self.wait, self.twice = root, lang_id, wait, twice
        self.proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.inbox, self.nid, self.quiescent, self.opened = queue.Queue(), 0, False, set()
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {"processId": os.getpid(), "rootUri": root.as_uri(),
                                    "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
                                    "capabilities": {"experimental": {"serverStatusNotification": True},
                                                     "window": {"workDoneProgress": True},
                                                     "textDocument": {"references": {}, "definition": {}}},
                                    "initializationOptions": init or {}})
        self.notify("initialized", {})
        if wait == "quiescent": self._wait_quiescent()

    # ---- wire ----
    def _send(self, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.proc.stdin.write(f"Content-Length: {len(b)}\r\n\r\n".encode() + b); self.proc.stdin.flush()

    def _read(self) -> None:
        out = self.proc.stdout
        while True:
            hdr = b""
            while not hdr.endswith(b"\r\n\r\n"):
                c = out.read(1)
                if not c: return
                hdr += c
            n = next(int(l.split(":")[1]) for l in hdr.decode().split("\r\n") if l.lower().startswith("content-length"))
            self.inbox.put(json.loads(out.read(n)))

    def _handle(self, m: dict) -> None:
        if m.get("method") == "experimental/serverStatus": self.quiescent = m["params"].get("quiescent", False)
        elif "id" in m and "method" in m:   # server -> client request: answer so the server does not stall
            res = [{} for _ in m["params"]["items"]] if m["method"] == "workspace/configuration" else None
            self._send({"jsonrpc": "2.0", "id": m["id"], "result": res})

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float = 900) -> dict:
        self.nid += 1; my = self.nid
        self._send({"jsonrpc": "2.0", "id": my, "method": method, "params": params})
        end = time.time() + timeout
        while time.time() < end:
            try: m = self.inbox.get(timeout=max(0.1, end - time.time()))
            except queue.Empty: break
            self._handle(m)
            if m.get("id") == my and "method" not in m: return m
        raise TimeoutError(f"language server gave no answer to {method} in {timeout:.0f}s")

    def _wait_quiescent(self, timeout: float = 1800) -> None:
        end = time.time() + timeout
        while not self.quiescent and time.time() < end:
            try: self._handle(self.inbox.get(timeout=1))
            except queue.Empty: pass

    # ---- api ----
    def open(self, path: Path) -> None:
        if path in self.opened: return
        self.notify("textDocument/didOpen", {"textDocument": {"uri": path.as_uri(), "languageId": self.lang_id, "version": 1,
                                                              "text": path.read_text(errors="replace")}})
        self.opened.add(path)

    def references(self, path: Path, line: int, col: int) -> list:
        """[(abs path, 1-based line)] of every reference incl. the declaration and import/export lines."""
        self.open(path)
        q = lambda: self.request("textDocument/references", {"textDocument": {"uri": path.as_uri()},
                                                              "position": {"line": line - 1, "character": col},
                                                              "context": {"includeDeclaration": True}})
        r = q()
        if self.twice or not r.get("result"): r = q()
        return sorted({(Path(unquote(urlparse(l["uri"]).path)), l["range"]["start"]["line"] + 1) for l in r.get("result") or []})

    def stop(self) -> None:
        try:
            self.request("shutdown", {}, timeout=10); self.notify("exit", {})
        except Exception: pass
        self.proc.kill()
