import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8010/api/v1"
TENANT = {"X-Tenant-ID": "test-tenant-e2e"}


def try_login(label, body, headers):
    req = urllib.request.Request(BASE + "/account/login", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.load(r)
        token = payload.get("access_token", "")
        print(f"{label:22} OK  token_len={len(token)}")
        return token
    except urllib.error.HTTPError as e:
        detail = e.read()[:160].decode(errors="replace")
        print(f"{label:22} {e.code} {detail}")
        return None


form = urllib.parse.urlencode({"username": "qa0@e.io", "password": "Str0ngFixture!x"}).encode()
token = try_login(
    "form-encoded", form, {**TENANT, "Content-Type": "application/x-www-form-urlencoded"}
)
if not token:
    js = json.dumps({"username": "qa0@e.io", "password": "Str0ngFixture!x"}).encode()
    token = try_login("json", js, {**TENANT, "Content-Type": "application/json"})

if token:
    print("--- endpoints ---")
    for path in (
        "/users/me",
        "/health/live",
        "/account/sessions/count",
        "/account/sessions",
        "/users",
        "/notifications",
    ):
        r2 = urllib.request.Request(
            BASE + path, headers={**TENANT, "Authorization": "Bearer " + token}
        )
        try:
            with urllib.request.urlopen(r2, timeout=15) as r:
                    print(f"  {path:<28} {r.status}")
        except urllib.error.HTTPError as e:
            print(f"  {path:<28} {e.code}")
        except Exception as e:
            print(f"  {path:<28} ERR {e}")
