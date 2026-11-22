"""The local web app, end to end, with test images instead of a camera and a
fake inventory server / printer."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from scanner import inventory
from scanner.labels import printer
from scanner.web import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "CAMERA_SETTINGS_FILE", tmp_path / "camera.json")
    monkeypatch.setattr("scanner.config.OUT_DIR", tmp_path / "labels")
    monkeypatch.setattr("scanner.config.CAPTURES_DIR", tmp_path / "captures")
    monkeypatch.setattr("scanner.scan.SCAN_SECONDS", 1.5)
    env = {"CAMERA_DEVICE": "file:references/captures", "LABEL_PRINTER": f"file://{tmp_path}/lp0",
           "INVENTORY_API_URL": "https://example.invalid/api/items.php", "INVENTORY_API_KEY": "k"}
    app = webapp.create_app(env)
    cam = app.state.cam.camera
    cam.values["focus"] = cam.BEST_FOCUS
    while cam.next_image() != "newark1.png":
        pass
    with TestClient(app) as c:
        yield c


def test_status(client):
    s = client.get("/api/status").json()
    assert s["camera"]["test_images"] and s["server"] and "CONNECTOR" in s["options"]["types"]
    assert "not found" in s["printer"]


def test_page_and_static(client):
    assert "BUMRC" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_scan_decodes_and_closes_camera(client, monkeypatch):
    monkeypatch.setattr(webapp, "enrich", lambda f, dk: webapp.Enriched(f, "skipped in tests"))
    r = client.post("/api/scan").json()
    assert r["fields"]["mpn"] == "EHR-3" and r["fields"]["original_qty"] == 50
    assert r["missing"] == [] and r["frames"] >= 1
    assert client.get("/api/scan/snapshot.jpg").headers["content-type"] == "image/jpeg"
    assert client.get("/api/status").json()["camera"]["open"] is False


def test_upload_success_and_errors(client, monkeypatch):
    calls = []

    def fake_server(request):
        calls.append(json.loads(request.content))
        assert request.headers["X-API-Key"] == "k"
        if calls[-1]["mpn"] == "DUP":
            return httpx.Response(409, json={"id": 7, "error": "already in inventory"})
        return httpx.Response(201, json={"id": 142})

    real = httpx.Client
    monkeypatch.setattr(inventory.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(fake_server)))
    fields = {"mpn": "EHR-3", "distributor": "NEWARK", "original_qty": 50, "value_text": "4.99kΩ"}
    assert client.post("/api/upload", json={"fields": fields, "member": "Sam"}).json() == {"id": 142}
    assert calls[0]["member"] == "Sam" and calls[0]["qty_remaining"] == 50 and calls[0]["value_num"] == 4990.0

    r = client.post("/api/upload", json={"fields": {**fields, "mpn": "DUP"}, "member": "Sam"})
    assert r.status_code == 502 and "00000007" in r.json()["detail"]["message"]

    r = client.post("/api/upload", json={"fields": fields, "member": ""})
    assert r.status_code == 502 and "your name" in r.json()["detail"]["message"]

    r = client.post("/api/upload", json={"fields": {**fields, "original_qty": -1}, "member": "Sam"})
    assert r.status_code == 502 and "0 or more" in r.json()["detail"]["message"]


def test_unreachable_server_is_an_error(client, monkeypatch):
    def down(request):
        raise httpx.ConnectError("down")
    real = httpx.Client
    monkeypatch.setattr(inventory.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(down)))
    r = client.post("/api/upload", json={"fields": {"mpn": "X", "original_qty": 1}, "member": "Sam"})
    assert r.status_code == 502 and "can't reach" in r.json()["detail"]["message"]


def test_label_preview_and_print(client, tmp_path, monkeypatch):
    r = client.get("/api/label.png", params={"item_id": 142, "name": "1UF CAP"})
    assert r.content[:4] == b"\x89PNG"
    r = client.post("/api/print", json={"item_id": 142, "name": "1UF CAP"})   # no printer: error, image kept
    assert r.status_code == 502 and r.json()["detail"]["saved"]
    monkeypatch.setattr(printer, "print_label", lambda img, env: None)
    assert client.post("/api/print", json={"item_id": 142, "name": "1UF CAP"}).json()["printed"]
    assert len(list((tmp_path / "labels").glob("*.png"))) == 2


def test_camera_controls_settings_and_capture(client, tmp_path):
    assert client.post("/api/camera/open").json()["open"]
    assert client.post("/api/camera/controls", json={"name": "sharpness", "value": 70}).json()["ok"]
    assert client.post("/api/camera/settings").json()["controls"]["sharpness"] == 70
    r = client.post("/api/captures", json={"description": "Newark EHR-3", "distributor": "NEWARK"}).json()
    assert r["decoded"]["mpn"] == "EHR-3"
    spec = client.post(f"/api/captures/{r['name']}", json={"values": {
        "distributor": "NEWARK", "mpn": "EHR-3", "original_qty": "50", "lot": "LOT9"}}).json()
    assert spec["expected"] == {"distributor": "NEWARK", "mpn": "EHR-3", "original_qty": 50}
    assert spec["expected_todo"] == {"lot": "LOT9"}
    assert (tmp_path / "captures" / f"{r['name']}.jpg").exists()
    client.post("/api/camera/close")


def test_capture_name_cannot_escape_folder(client):
    assert client.post("/api/captures/..%2F..%2Fetc%2Fpasswd", json={"values": {}}).status_code == 404
