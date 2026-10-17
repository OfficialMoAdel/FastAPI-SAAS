import urllib.error
import urllib.request

for path in ("/api/health/live", "/api/v1/health/live", "/api/v1/health", "/api/health"):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8010" + path, timeout=10) as r:
            print(f"{path:<26} {r.status}")
    except urllib.error.HTTPError as e:
        print(f"{path:<26} {e.code}")
    except Exception as e:  # noqa: BLE001
        print(f"{path:<26} ERR {e}")
