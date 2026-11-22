"""Check that everything the scanner needs is set up. Nothing is uploaded or printed.

    python -m scanner.check                                         (on a PC)
    docker compose run --rm scanner python -m scanner.check         (on the Jetson)
"""
from __future__ import annotations

import sys

import httpx

from scanner import inventory
from scanner.camera import controls as v4l2
from scanner.camera.sources import FULL_SIZE, FileCamera, make_camera
from scanner.config import load_env
from scanner.labels import printer
from scanner.lookup.digikey import DigiKey, DigiKeyError

OK, WARN, FAIL = "ok  ", "warn", "FAIL"


def check_camera(env: dict) -> list[tuple[str, str, str]]:
    device = env.get("CAMERA_DEVICE") or "/dev/video0"
    out = []
    try:
        cam = make_camera(device)
        cam.open()
        frame = None
        for _ in range(10):                  # the first frames can be empty while it starts up
            frame = cam.read()
            if frame is not None:
                break
        size = cam.size
        cam.close()
    except RuntimeError as e:
        return [(FAIL, "camera", str(e))]
    if frame is None:
        return [(FAIL, "camera", f"{device} opened but sent no picture")]
    if isinstance(cam, FileCamera):
        return [(WARN, "camera", f"using test images ({device}), not a real camera")]
    status = OK if size == FULL_SIZE else WARN
    note = "" if size == FULL_SIZE else f" (expected {FULL_SIZE[0]}x{FULL_SIZE[1]}; is this the Arducam?)"
    out.append((status, "camera", f"{device} at {size[0]}x{size[1]}{note}"))

    if not v4l2.available():
        out.append((FAIL, "camera controls", "v4l2-ctl missing (apt install v4l-utils)"))
    else:
        ctrls = cam.controls()
        have = ", ".join(sorted(ctrls)) or "none"
        status = OK if "focus" in ctrls else WARN
        note = "" if "focus" in ctrls else " (no focus control: calibration can only tune sharpness)"
        out.append((status, "camera controls", have + note))
    return out


def check_printer(env: dict) -> tuple[str, str, str]:
    problem = printer.printer_problem(env)
    return (FAIL, "printer", problem) if problem else (OK, "printer", printer.printer_url(env))


def check_digikey(env: dict) -> tuple[str, str, str]:
    dk = DigiKey(env)
    if not dk.configured:
        return FAIL, "DigiKey", "DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET not set in .env"
    try:
        dk._headers()                         # gets a token: proves the keys work
    except DigiKeyError as e:
        return FAIL, "DigiKey", str(e)
    return OK, "DigiKey", "API keys accepted"


def check_server(env: dict) -> tuple[str, str, str]:
    if not inventory.configured(env):
        return FAIL, "inventory server", "INVENTORY_API_URL / INVENTORY_API_KEY not set in .env"
    url = env["INVENTORY_API_URL"]
    try:
        # the API only accepts POST; a GET with a good key gets 405, a bad key 401
        r = httpx.get(url, headers={"X-API-Key": env["INVENTORY_API_KEY"]}, timeout=10)
    except httpx.HTTPError as e:
        return FAIL, "inventory server", f"can't reach {url}: {e}"
    if r.status_code == 405:
        return OK, "inventory server", f"{url} reachable, API key accepted"
    if r.status_code == 401:
        return FAIL, "inventory server", "server reachable but the API key was rejected"
    return FAIL, "inventory server", f"unexpected answer {r.status_code} from {url} (wrong URL?)"


def main() -> int:
    env = load_env()
    results = check_camera(env) + [check_printer(env), check_digikey(env), check_server(env)]
    width = max(len(name) for _, name, _ in results)
    for status, name, detail in results:
        print(f"[{status}] {name:<{width}}  {detail}")
    failed = sum(s == FAIL for s, _, _ in results)
    print("\nall good" if not failed else f"\n{failed} problem(s) to fix")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
