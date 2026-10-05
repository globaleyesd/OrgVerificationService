"""End-to-end check of a RUNNING local stack (started from the Control Center). Standard library only.

    python scripts/smoke_local.py [--base http://localhost:8000]

Prompts for the service-switch password if the service is off. Needs `python -m app.cli create-demo-users`
and `python -m app.cli ingest samples/...` to have been run first. Prints PASS/FAIL per step and exits 1 on any failure.
"""
import getpass
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
import uuid

base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "http://localhost:8000"
failures = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(opener, method, path, body=None, raw=None, headers=None):
    data, h = raw, dict(headers or {})
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


anon, eileen, other = client(), client(), client()
code, st = call(anon, "GET", "/api/service/status")
check("server reachable", code == 200)
if not st.get("on"):
    code, _ = call(anon, "POST", "/api/service/on", {"password": getpass.getpass("Service switch password: ")})
    check("service switched on", code == 200)
check("demo sign-in: Eileen", call(eileen, "POST", "/api/auth/demo-login", {"username": "Eileen"})[0] == 200)
check("demo sign-in: AllMinusEileen", call(other, "POST", "/api/auth/demo-login", {"username": "AllMinusEileen"})[0] == 200)

# --- add knowledge (no sign-in), as an anonymous visitor ---
marker = "zebracorn-" + uuid.uuid4().hex[:6]
b = uuid.uuid4().hex
body = (f'--{b}\r\nContent-Disposition: form-data; name="text"\r\n\r\nSmoke test note {marker}: the marker phrase is {marker}.\r\n--{b}--\r\n').encode()
code, res = call(anon, "POST", "/api/upload", raw=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
check("anonymous visitor can add a text entry", code == 200 and res.get("chunks", 0) >= 1, res)
check("upload response does not reveal the level", "level" not in res)
code, res = call(anon, "POST", "/api/upload", raw=(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="x.exe"\r\nContent-Type: application/octet-stream\r\n\r\nMZ\r\n--{b}--\r\n').encode(),
                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
check("a disallowed file type is refused (415)", code == 415, res)

# --- ask needs sign-in; Super sees the new entry, Employee does not until it is relabelled ---
check("ask without sign-in is refused (401)", call(anon, "POST", "/api/ask", {"question": "hello"})[0] == 401)
code, a = call(eileen, "POST", "/api/ask", {"question": f"What is the marker phrase {marker}?"})
check("Eileen gets an answer with a verified citation", code == 200 and a.get("citations") and a["citations"][0].get("verified") and "level" in a["citations"][0], a)
code, a2 = call(other, "POST", "/api/ask", {"question": f"What is the marker phrase {marker}?"})
check("AllMinusEileen cannot see it yet", code == 200 and not a2.get("citations"), a2)
check("AllMinusEileen is told something was held back", a2.get("withheld") is True, a2)

# --- review: only the top role may relabel ---
code, docs = call(eileen, "GET", "/api/documents")
check("Eileen can list documents", code == 200 and docs.get("documents"))
check("AllMinusEileen cannot list documents (403)", call(other, "GET", "/api/documents")[0] == 403)
new = next((d for d in docs.get("documents", []) if d["title"].startswith("Entry: Smoke test note " + marker)), None)
check("the new entry is listed at the top level", new is not None and new["level"] == "super", new)
if new:
    check("AllMinusEileen cannot relabel (403)", call(other, "POST", "/api/documents/level", {"id": new["id"], "level": "employee"})[0] == 403)
    check("Eileen marks it readable by Employees", call(eileen, "POST", "/api/documents/level", {"id": new["id"], "level": "employee"})[0] == 200)
    code, a3 = call(other, "POST", "/api/ask", {"question": f"What is the marker phrase {marker}?"})
    check("now AllMinusEileen gets the answer", code == 200 and a3.get("citations"), a3)
    check("Employee citations carry no level tag", a3.get("citations") and "level" not in a3["citations"][0])
    call(eileen, "POST", "/api/documents/level", {"id": new["id"], "level": "super"})   # put it back

print("\nALL PASSED" if not failures else f"\n{len(failures)} FAILED: " + "; ".join(failures))
sys.exit(1 if failures else 0)
