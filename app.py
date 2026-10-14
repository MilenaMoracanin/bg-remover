"""Local studio for product photos used on Instagram and KupujemProdajem."""

from __future__ import annotations

import os
import re
import threading
import time
import traceback
import webbrowser
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_STORED, ZipFile

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from engine import Engine, EngineError

ROOT = Path(__file__).resolve().parent
engine = Engine()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    engine.start()
    yield


app = FastAPI(title="Pozadina", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_JOB_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_LABEL = re.compile(r"^[a-z0-9_]{1,24}$")
_MAX_BYTES = 40 * 1024 * 1024


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/status")
def status() -> dict:
    return engine.status()


@app.post("/api/process")
async def process(
    file: UploadFile = File(...),
    job: str = Form(...),
    background: str = Form("#ffffff"),
    width: int = Form(1200),
    height: int = Form(1200),
    padding: float = Form(0.09),
    shadow: str = Form("1"),
    image_format: str = Form("jpeg"),
    anchor: float = Form(0.5),
    label: str = Form("kp"),
) -> Response:
    if not _JOB_ID.fullmatch(job):
        raise HTTPException(status_code=400, detail="Nepoznata obrada.")
    if image_format not in {"jpeg", "png"}:
        raise HTTPException(status_code=400, detail="Format mora biti JPEG ili PNG.")
    if not _LABEL.fullmatch(label):
        label = "slika"
    if width != 0 or height != 0:
        if not (256 <= width <= 4096 and 256 <= height <= 4096):
            raise HTTPException(status_code=400, detail="Dimenzije moraju biti između 256 i 4096.")
    background = background.strip().lower()
    if background != "transparent" and not re.fullmatch(r"#[0-9a-f]{6}", background):
        raise HTTPException(status_code=400, detail="Boja pozadine nije ispravna.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Fajl je prazan.")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="Slika je veća od 40 MB.")

    _log(f"primljena slika {file.filename!r} ({len(raw)} B)")
    try:
        with Image.open(BytesIO(raw)) as incoming:
            incoming.seek(0)
            frame = incoming.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        _log(f"ne mogu da otvorim: {exc}")
        raise HTTPException(status_code=400, detail="Ovaj fajl nije slika koju mogu da otvorim.") from exc

    started = time.perf_counter()
    try:
        data, media, out_w, out_h = engine.render(
            frame,
            background=background,
            width=width,
            height=height,
            padding=padding,
            shadow=shadow == "1" and background != "transparent",
            image_format=image_format,
            anchor=anchor,
        )
    except EngineError as exc:
        _log(f"obrada nije uspela: {exc}")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _log(f"greska:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Obrada ove slike nije uspela. Pokušaj ponovo.") from exc
    finally:
        frame.close()

    filename = _output_name(file.filename or "slika", label, "png" if media == "image/png" else "jpg")
    filename = _remember(job, filename, data)
    elapsed = time.perf_counter() - started
    _log(f"gotovo {filename} za {elapsed:.1f}s")
    return Response(
        content=data,
        media_type=media,
        headers={
            "X-Filename": quote(filename),
            "X-Width": str(out_w),
            "X-Height": str(out_h),
            "X-Seconds": f"{elapsed:.1f}",
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/jobs/{job_id}/zip")
def download_zip(job_id: str) -> Response:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Nema ove obrade.")
    with _jobs_lock:
        job = _jobs.get(job_id)
        files = list(job["files"]) if job else []
    if not files:
        raise HTTPException(status_code=404, detail="Nema spremnih slika za preuzimanje.")

    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_STORED) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=pozadina.zip"},
    )


def _log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with (ROOT / "pozadina.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def _output_name(original: str, label: str, ext: str) -> str:
    stem = Path(original).stem
    stem = re.sub(r"[^\w\-]+", "-", stem, flags=re.UNICODE).strip("-") or "slika"
    stem = stem[:80]
    return f"{stem}-{label}.{ext}"


def _remember(job_id: str, filename: str, data: bytes) -> str:
    now = time.time()
    with _jobs_lock:
        stale = [key for key, job in _jobs.items() if now - job["created"] > 3600]
        for key in stale:
            del _jobs[key]
        job = _jobs.setdefault(job_id, {"created": now, "files": [], "names": set()})
        name = filename
        index = 2
        while name in job["names"]:
            path = Path(filename)
            name = f"{path.stem}-{index}{path.suffix}"
            index += 1
        job["names"].add(name)
        job["files"].append((name, data))
        if len(job["files"]) > 40:
            dropped = job["files"].pop(0)
            job["names"].discard(dropped[0])
        return name


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    print(f"Pozadina radi na http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
