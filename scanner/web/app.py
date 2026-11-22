"""The scanner's local web interface (FastAPI). Run with:
    .venv/bin/python -m scanner            # then open http://localhost:8080

One person uses it at a time on the Jetson's screen, so state (camera,
current calibration run) lives in this process.
"""
from __future__ import annotations

import asyncio
import io
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scanner import inventory
from scanner.camera.calibrate import Cancelled, calibrate
from scanner.camera.manager import CameraManager, load_settings, save_settings
from scanner.camera.sources import FileCamera, make_camera
from scanner.captures import confirm_capture, save_capture
from scanner.config import CAMERA_SETTINGS_FILE, load_env
from scanner.decode.parse import DISTRIBUTORS, interpret
from scanner.decode.reader import decode_frame
from scanner.labels import printer
from scanner.labels.store import save_label
from scanner.lookup.digikey import DigiKey
from scanner.lookup.enrich import enrich, missing
from scanner.scan import scan

STATIC = Path(__file__).parent / "static"
TYPES = ["RESISTOR", "CAPACITOR", "INDUCTOR", "DIODE", "CRYSTAL", "SENSOR", "IC", "CONNECTOR", "OTHER"]
MOUNTS = ["SMD", "THT"]
STREAM_FPS = 12


# ---------------------------------------------------------------- request bodies

class SetControl(BaseModel):
    name: str
    value: int

class NewCapture(BaseModel):
    description: str
    distributor: str | None = None

class ConfirmCapture(BaseModel):
    values: dict

class Fields(BaseModel):
    fields: dict

class Upload(BaseModel):
    fields: dict
    member: str

class PrintLabel(BaseModel):
    item_id: int
    name: str



def create_app(env: dict | None = None) -> FastAPI:
    env = load_env() if env is None else env
    cam = CameraManager(make_camera(env.get("CAMERA_DEVICE")), CAMERA_SETTINGS_FILE)
    dk = DigiKey(env)
    calib = {"running": False, "progress": 0.0, "message": "", "result": None, "error": None,
             "cancel": threading.Event()}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        calib["cancel"].set()       # release the camera when the server stops
        cam.close()

    app = FastAPI(title="BUMRC Scanner", lifespan=lifespan)
    app.state.cam = cam

    def run_camera(fn, *a):
        try:
            return fn(*a)
        except RuntimeError as e:
            raise HTTPException(503, str(e))

    # ------------------------------------------------------------ page, status

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        return {
            "camera": {"open": cam.is_open, "source": cam.camera.description, "error": cam.error,
                       "test_images": isinstance(cam.camera, FileCamera), "size": cam.camera.size,
                       "settings": load_settings(CAMERA_SETTINGS_FILE)},
            "printer": printer.printer_problem(env),
            "digikey": dk.configured,
            "server": inventory.configured(env),
            "options": {"distributors": DISTRIBUTORS, "types": TYPES, "mounts": MOUNTS},
        }

    # ------------------------------------------------------------ camera

    @app.post("/api/camera/open")
    def camera_open():
        run_camera(cam.open)
        return {"open": True, "warnings": cam.warnings}

    @app.post("/api/camera/close")
    def camera_close():
        calib["cancel"].set()
        cam.close()
        return {"open": False}

    @app.post("/api/camera/next-image")
    def next_image():
        if not isinstance(cam.camera, FileCamera):
            raise HTTPException(400, "only available with test images")
        return {"image": cam.camera.next_image()}

    @app.get("/stream.mjpg")
    async def stream():
        async def frames():
            while cam.is_open:
                jpg = cam.preview_jpeg()
                if jpg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                await asyncio.sleep(1 / STREAM_FPS)
        if not cam.is_open:
            raise HTTPException(409, "camera is closed")
        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/camera/controls")
    def get_controls():
        return {k: c.to_dict() for k, c in cam.camera.controls().items()}


    @app.post("/api/camera/controls")
    def set_control(body: SetControl):
        run_camera(cam.camera.set, body.name, body.value)
        return {"ok": True}

    @app.post("/api/camera/settings")
    def save_camera_settings():
        """Save the camera's current focus/sharpness/... as the defaults."""
        keep = ("autofocus", "focus", "sharpness", "brightness", "contrast", "gain", "auto_exposure", "exposure")
        values = {k: c.value for k, c in cam.camera.controls().items() if k in keep}
        return save_settings(CAMERA_SETTINGS_FILE, values)

    # ------------------------------------------------------------ calibration

    def calibration_worker():
        calib.update(running=True, progress=0.0, message="starting", result=None, error=None)
        calib["cancel"].clear()
        try:
            r = calibrate(cam, lambda f, m: calib.update(progress=f, message=m), calib["cancel"])
            calib["result"] = r.to_dict()
        except Cancelled:
            calib["error"] = "cancelled"
        except Exception as e:      # shown on the page; the thread must not die silently
            calib["error"] = str(e)
        finally:
            calib["running"] = False

    @app.post("/api/calibrate")
    def calibrate_start():
        if calib["running"]:
            raise HTTPException(409, "calibration already running")
        run_camera(cam.open)
        threading.Thread(target=calibration_worker, name="calibrate", daemon=True).start()
        return {"started": True}

    @app.get("/api/calibrate")
    def calibrate_status():
        return {k: v for k, v in calib.items() if k != "cancel"}

    @app.delete("/api/calibrate")
    def calibrate_cancel():
        calib["cancel"].set()
        return {"cancelling": True}

    # ------------------------------------------------------------ captures (test cases)


    @app.post("/api/captures")
    def capture(body: NewCapture):
        if not cam.is_open:
            raise HTTPException(409, "open the camera first")
        frame = run_camera(cam.fresh_frame)
        fields = interpret(decode_frame(frame, thorough=True)).fields
        name = save_capture(frame, body.description.strip(), body.distributor, fields)
        return {"name": name, "decoded": fields}


    @app.post("/api/captures/{name}")
    def capture_confirm(name: str, body: ConfirmCapture):
        try:
            return confirm_capture(name, body.values)
        except FileNotFoundError:
            raise HTTPException(404, "no such capture")

    # ------------------------------------------------------------ scanning

    @app.post("/api/scan")
    async def do_scan():
        await run_in_threadpool(run_camera, cam.open)
        camera_warnings = list(cam.warnings)
        try:
            s = await run_in_threadpool(run_camera, scan, cam)
        finally:
            await run_in_threadpool(cam.close)
        fields, problem = await run_in_threadpool(enrich, s.result.fields, dk)
        app.state.snapshot = s.snapshot
        return {"fields": fields, "missing": missing(fields), "lookup_problem": problem,
                "camera_warnings": camera_warnings,
                "codes": [c.to_dict() for c in s.result.codes], "frames": s.frames, "seconds": s.seconds}

    @app.get("/api/scan/snapshot.jpg")
    def snapshot():
        jpg = getattr(app.state, "snapshot", None)
        if not jpg:
            raise HTTPException(404, "no scan yet")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


    @app.post("/api/lookup")
    async def lookup(body: Fields):
        fields, problem = await run_in_threadpool(enrich, body.fields, dk)
        return {"fields": fields, "missing": missing(fields), "lookup_problem": problem}


    @app.post("/api/upload")
    async def upload(body: Upload):
        try:
            item_id = await run_in_threadpool(inventory.upload, body.fields, body.member, env)
        except inventory.UploadError as e:
            raise HTTPException(502, {"message": str(e), "existing_id": e.existing_id})
        return {"id": item_id}

    # ------------------------------------------------------------ labels

    def label_image(item_id: int, name: str):
        try:
            return printer.create_label({"name": name}, item_id)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/label.png")
    def label_png(item_id: int, name: str = ""):
        buf = io.BytesIO()
        label_image(item_id, name).save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


    @app.post("/api/print")
    async def print_label(body: PrintLabel):
        img = label_image(body.item_id, body.name)
        path = save_label(img, body.item_id)       # saved even if printing fails
        try:
            await run_in_threadpool(printer.print_label, img, env)
        except Exception as e:                     # brother_ql raises assorted errors
            raise HTTPException(502, {"message": f"print failed: {e}", "saved": str(path)})
        return {"printed": True, "saved": str(path)}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    return app
