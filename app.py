"""Local studio for product photos used on Instagram and KupujemProdajem."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_STORED, ZipFile

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
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
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
_folder_jobs: dict[str, dict] = {}
_folder_lock = threading.Lock()


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


class FolderRequest(BaseModel):
    source: str
    destination: str
    background: str = "#ffffff"
    width: int = 1200
    height: int = 1200
    shadow: bool = True
    image_format: str = "jpeg"
    anchor: float = 0.5


@app.post("/api/pick-folder")
async def pick_folder(kind: str = "source") -> dict:
    title = "Izaberi folder sa slikama" if kind == "source" else "Izaberi folder za gotove slike"
    path = await asyncio.to_thread(_choose_folder, title)
    return {"path": path}


@app.post("/api/folder")
def start_folder(body: FolderRequest) -> dict:
    options = _folder_options(body)
    source = Path(body.source).expanduser()
    destination = Path(body.destination).expanduser()
    if not body.source.strip():
        raise HTTPException(status_code=400, detail="Izaberi folder sa slikama.")
    if not source.is_dir():
        raise HTTPException(status_code=400, detail="Folder sa slikama ne postoji.")
    if not str(body.destination).strip():
        raise HTTPException(status_code=400, detail="Izaberi folder za gotove slike.")
    source_resolved = source.resolve()
    destination_resolved = destination.resolve()
    if source_resolved == destination_resolved or _is_inside(destination_resolved, source_resolved) or _is_inside(source_resolved, destination_resolved):
        raise HTTPException(status_code=400, detail="Gotove slike moraju u drugi folder, van foldera sa originalima.")

    images = _list_images(source_resolved)
    if not images:
        raise HTTPException(status_code=400, detail="U tom folderu nema slika.")

    with _folder_lock:
        active = [job for job in _folder_jobs.values() if job["state"] == "running"]
    if active:
        raise HTTPException(status_code=409, detail="Obrada foldera je već u toku.")

    job_id = str(uuid.uuid4())
    job = {
        "state": "running",
        "source": source_resolved,
        "destination": destination_resolved,
        "files": images,
        "total": len(images),
        "done": 0,
        "saved": 0,
        "current": "",
        "errors": [],
        "cancel": False,
        "started": time.time(),
        "options": options,
    }
    with _folder_lock:
        _folder_jobs[job_id] = job
    threading.Thread(target=_run_folder, args=(job_id,), name="folder-job", daemon=True).start()
    return {"id": job_id, "total": len(images)}


@app.get("/api/folder/{job_id}")
def folder_status(job_id: str) -> dict:
    with _folder_lock:
        job = _folder_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Nema ove obrade.")
        return {
            "state": job["state"],
            "total": job["total"],
            "done": job["done"],
            "saved": job["saved"],
            "current": job["current"],
            "errors": list(job["errors"][-30:]),
            "elapsed": round(time.time() - job["started"], 1),
            "destination": str(job["destination"]),
        }


@app.post("/api/folder/{job_id}/stop")
def stop_folder(job_id: str) -> dict:
    with _folder_lock:
        job = _folder_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Nema ove obrade.")
        job["cancel"] = True
    return {"ok": True}


def _folder_options(body: FolderRequest) -> dict:
    if body.image_format not in {"jpeg", "png"}:
        raise HTTPException(status_code=400, detail="Format mora biti JPEG ili PNG.")
    if body.width != 0 or body.height != 0:
        if not (256 <= body.width <= 4096 and 256 <= body.height <= 4096):
            raise HTTPException(status_code=400, detail="Dimenzije moraju biti između 256 i 4096.")
    background = body.background.strip().lower()
    if background != "transparent" and not re.fullmatch(r"#[0-9a-f]{6}", background):
        raise HTTPException(status_code=400, detail="Boja pozadine nije ispravna.")
    return {
        "background": "transparent" if body.width == 0 or body.height == 0 else background,
        "width": body.width,
        "height": body.height,
        "shadow": body.shadow and background != "transparent" and body.width != 0,
        "image_format": "png" if body.width == 0 or body.height == 0 else body.image_format,
        "anchor": min(max(body.anchor, 0.0), 1.0),
    }


def _run_folder(job_id: str) -> None:
    with _folder_lock:
        job = _folder_jobs[job_id]
        files = list(job["files"])
        source = job["source"]
        destination = job["destination"]
        options = dict(job["options"])
    used: set[str] = set()
    for index, path in enumerate(files, start=1):
        with _folder_lock:
            if job["cancel"]:
                job["state"] = "stopped"
                job["current"] = ""
                return
            relative = path.relative_to(source).as_posix()
            job["current"] = relative
        try:
            target = _target_path(destination, path.relative_to(source), options["image_format"], used)
            _write_processed(path, target, options)
            with _folder_lock:
                job["saved"] += 1
        except (EngineError, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            _log(f"folder {relative}: {exc}")
            with _folder_lock:
                job["errors"].append({"file": relative, "message": _folder_error(exc)})
        except Exception as exc:
            _log(f"folder {relative}:\n{traceback.format_exc()}")
            with _folder_lock:
                job["errors"].append({"file": relative, "message": "Obrada ove slike nije uspela."})
        with _folder_lock:
            job["done"] = index
    with _folder_lock:
        job["state"] = "done"
        job["current"] = ""
    _log(f"folder gotov {job['saved']}/{job['total']} u {destination}")


def _write_processed(source: Path, target: Path, options: dict) -> None:
    if source.stat().st_size > _MAX_BYTES:
        raise EngineError("Slika je veća od 40 MB.")
    with Image.open(source) as incoming:
        incoming.seek(0)
        frame = incoming.copy()
    try:
        data, _media, _width, _height = engine.render(frame, **options)
    finally:
        frame.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _target_path(destination: Path, relative: Path, image_format: str, used: set[str]) -> Path:
    extension = ".png" if image_format == "png" else ".jpg"
    candidate = relative.with_suffix(extension)
    key = candidate.as_posix().lower()
    number = 2
    while key in used:
        candidate = relative.with_name(f"{relative.stem}-{number}{extension}")
        key = candidate.as_posix().lower()
        number += 1
    used.add(key)
    return destination / candidate


def _list_images(source: Path) -> list[Path]:
    found = []
    for path in source.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        found.append(path)
    found.sort()
    return found


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _folder_error(exc: Exception) -> str:
    if isinstance(exc, EngineError):
        return str(exc)
    if isinstance(exc, UnidentifiedImageError):
        return "Ovaj fajl nije slika koju mogu da otvorim."
    return "Ovu sliku ne mogu da obradim."


def _choose_folder(title: str) -> str:
    """Show the folder dialog over the open browser page."""
    if sys.platform == "darwin":
        return _choose_folder_mac(title)
    if sys.platform == "win32":
        return _choose_folder_windows(title)
    return ""


def _choose_folder_mac(title: str) -> str:
    safe = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
use framework "AppKit"
use scripting additions
set picked to ""
set frontName to ""
try
    tell application "System Events"
        set frontName to name of first application process whose frontmost is true
    end tell
end try
if frontName is in {{"osascript", "Python", "python3", "bash", "zsh"}} then
    set frontName to ""
end if
if frontName is not "" then
    try
        tell application frontName
            activate
            set picked to POSIX path of (choose folder with prompt "{safe}")
        end tell
        return picked
    on error errText number errNum
        if errNum is -128 then return ""
    end try
end if
set panel to current application's NSOpenPanel's openPanel()
panel's setCanChooseFiles_(false)
panel's setCanChooseDirectories_(true)
panel's setAllowsMultipleSelection_(false)
panel's setCanCreateDirectories_(true)
panel's setTitle_("{safe}")
panel's setMessage_("{safe}")
panel's setPrompt_("Izaberi")
panel's setLevel_(101)
set response to panel's runModal()
if (response as integer) is 1 then
    set folderURL to panel's |URL|()
    if folderURL is missing value then return ""
    return (folderURL's |path|() as text)
end if
return ""
'''
    result = subprocess.run(
        ["osascript"],
        input=script,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            _log(f"Izbor foldera nije uspeo: {detail}")
        return ""
    return result.stdout.strip()


def _choose_folder_windows(title: str) -> str:
    script = r"""
$ProgressPreference = 'SilentlyContinue'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
function Write-Path([string]$value) {
    if ($null -eq $value) { $value = '' }
    [Console]::Out.Write($value)
}
function Show-SimpleDialog {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $title = $env:POZADINA_FOLDER_TITLE
    if ([string]::IsNullOrWhiteSpace($title)) { $title = 'Izaberi folder' }
    $dialog.Description = $title
    $dialog.ShowNewFolderButton = $true
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Path $dialog.SelectedPath
    }
}
try {
    Add-Type -AssemblyName System.Windows.Forms
    $forms = [System.Windows.Forms.Form].Assembly.Location
    $source = @'
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
public class HwndOwner : IWin32Window {
    IntPtr handle;
    public HwndOwner(IntPtr handle) { this.handle = handle; }
    public IntPtr Handle { get { return handle; } }
}
public class BrowserFolderDialog {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    public static string Pick(string title) {
        using (FolderBrowserDialog dialog = new FolderBrowserDialog()) {
            dialog.Description = title == null ? "" : title;
            dialog.ShowNewFolderButton = true;
            IntPtr hwnd = GetForegroundWindow();
            DialogResult result = hwnd == IntPtr.Zero
                ? dialog.ShowDialog()
                : dialog.ShowDialog(new HwndOwner(hwnd));
            if (result == DialogResult.OK && dialog.SelectedPath != null) return dialog.SelectedPath;
            return "";
        }
    }
}
'@
    Add-Type -TypeDefinition $source -ReferencedAssemblies $forms
    $title = $env:POZADINA_FOLDER_TITLE
    if ([string]::IsNullOrWhiteSpace($title)) { $title = 'Izaberi folder' }
    Write-Path ([BrowserFolderDialog]::Pick($title))
} catch {
    Show-SimpleDialog
}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(powershell):
        powershell = "powershell"
    env = os.environ.copy()
    env["POZADINA_FOLDER_TITLE"] = title
    result = subprocess.run(
        [powershell, "-NoProfile", "-STA", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            _log(f"Izbor foldera nije uspeo: {detail}")
        return ""
    return result.stdout.replace("\ufeff", "").strip()


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
