"""Local product-photo background removal and ad-format compositing."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
import urllib.request
from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFilter, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models" / "birefnet-general-lite.onnx"
MODEL_URL = (
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
    "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx"
)
MODEL_MD5 = "4fab47adc4ff364be1713e97b7e66334"
MODEL_SIZE = 224_005_088
MAX_SOURCE_SIDE = 8192
INFER_SIZE = 1024
INFER_PAD = 0.12
JPEG_QUALITY = 100

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class EngineError(Exception):
    pass


def _download_model(url: str, dest: Path, report) -> None:
    """Download the model. On Windows, fall back to the system downloader."""
    errors: list[str] = []
    for attempt in range(1, 4):
        try:
            _download_with_python(url, dest, report)
            return
        except Exception as exc:
            errors.append(str(exc))
            dest.unlink(missing_ok=True)
            time.sleep(attempt)
    if sys.platform == "win32":
        try:
            _download_with_curl(url, dest)
            return
        except Exception as exc:
            errors.append(str(exc))
            dest.unlink(missing_ok=True)
    detail = errors[-1] if errors else "nepoznata greška"
    raise EngineError(detail)


def _download_with_python(url: str, dest: Path, report) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Pozadina/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        with dest.open("wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                report(downloaded, total)


def _download_with_curl(url: str, dest: Path) -> None:
    curl = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
    if not os.path.exists(curl):
        curl = "curl.exe"
    result = subprocess.run(
        [curl, "-L", "--fail", "--retry", "3", "-o", str(dest), url],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "curl nije uspeo").strip()
        raise EngineError(message)


class Engine:
    def __init__(self) -> None:
        self.state = "idle"
        self.message = "Model još nije učitan."
        self.progress = 0.0
        self.error = ""
        self._session: ort.InferenceSession | None = None
        self._input_name = ""
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._load, name="model-load", daemon=True).start()

    def status(self) -> dict:
        return {
            "state": self.state,
            "message": self.message,
            "progress": round(self.progress, 3),
        }

    def wait_until_ready(self, timeout: float = 300) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state == "ready":
                return
            if self.state == "error":
                raise EngineError(self.message)
            time.sleep(0.2)
        raise EngineError("Model se predugo učitava. Proveri internet ako se preuzima prvi put.")

    def cutout(self, image: Image.Image) -> Image.Image:
        self.wait_until_ready()
        rgb = _prepare_rgb(image)
        mask = self._mask(rgb)
        soft = mask.filter(ImageFilter.GaussianBlur(radius=0.4))
        rgba = rgb.convert("RGBA")
        rgba.putalpha(soft)
        return _decontaminate_white(rgba)

    def render(
        self,
        image: Image.Image,
        *,
        background: str,
        width: int,
        height: int,
        shadow: bool,
        image_format: str,
        anchor: float,
    ) -> tuple[bytes, str, int, int]:
        return self.compose(
            self.cutout(image),
            background=background,
            width=width,
            height=height,
            shadow=shadow,
            image_format=image_format,
            anchor=anchor,
        )

    def compose(
        self,
        cut: Image.Image,
        *,
        background: str,
        width: int,
        height: int,
        shadow: bool,
        image_format: str,
        anchor: float,
    ) -> tuple[bytes, str, int, int]:
        box = cut.getbbox()
        if box is None:
            raise EngineError("Na slici nije pronađen proizvod.")

        subject = cut.crop(box)
        anchor = min(max(anchor, 0.0), 1.0)
        transparent = background == "transparent" or width == 0 or height == 0

        if transparent:
            canvas = subject
        else:
            color = _parse_hex(background)
            canvas = _place_on_canvas(
                subject,
                width=width,
                height=height,
                color=color,
                shadow=shadow,
                anchor=anchor,
            )

        buf = BytesIO()
        if image_format == "png" or transparent:
            canvas.save(buf, format="PNG", optimize=True)
            media = "image/png"
        else:
            flat = canvas.convert("RGB")
            flat.save(
                buf,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
                progressive=True,
                subsampling=0,
            )
            media = "image/jpeg"
        return buf.getvalue(), media, canvas.width, canvas.height

    def _load(self) -> None:
        with self._load_lock:
            if self._session is not None:
                return
            try:
                path = self._ensure_model()
                self.state = "loading"
                self.message = "Učitavam model…"
                self.progress = 1.0
                options = ort.SessionOptions()
                cores = min(4, os.cpu_count() or 2)
                options.intra_op_num_threads = cores
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(path),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                self._input_name = self._session.get_inputs()[0].name
                self.state = "ready"
                self.message = "Spreman. Sve ostaje na ovom računaru."
                self.progress = 1.0
            except Exception as exc:
                self.state = "error"
                self.error = str(exc)
                self.message = f"Model nije spreman: {exc}"

    def _ensure_model(self) -> Path:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        if MODEL_PATH.exists() and MODEL_PATH.stat().st_size == MODEL_SIZE:
            return MODEL_PATH
        if MODEL_PATH.exists():
            MODEL_PATH.unlink()

        self.state = "downloading"
        self.progress = 0.0
        self.message = "Prvi put preuzimam model (oko 214 MB). Posle toga radi bez interneta."
        tmp = MODEL_PATH.with_suffix(".onnx.part")
        if tmp.exists():
            tmp.unlink(missing_ok=True)

        def report(downloaded: int, total: int) -> None:
            if total > 0:
                self.progress = min(downloaded / total, 0.99)
                percent = int(self.progress * 100)
                self.message = f"Preuzimam model… {percent}%"

        try:
            _download_model(MODEL_URL, tmp, report)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            print(f"Preuzimanje modela nije uspelo: {exc}", flush=True)
            raise EngineError(
                "Preuzimanje modela nije uspelo. Proveri internet i pokušaj ponovo. "
                "Ako si na firmnoj mreži, probaj kućni internet."
            ) from exc

        digest = hashlib.md5(tmp.read_bytes()).hexdigest()
        if digest != MODEL_MD5:
            tmp.unlink(missing_ok=True)
            raise EngineError("Preuzeti model je oštećen. Pokušaj ponovo.")
        tmp.replace(MODEL_PATH)
        self.progress = 1.0
        return MODEL_PATH

    def _mask(self, rgb: Image.Image) -> Image.Image:
        prob = self._infer_probability(rgb)
        prob = _flip_if_background_kept(prob)
        prob = self._refine_subject(rgb, prob)
        if float(prob.max() - prob.min()) < 1e-6:
            raise EngineError("Model nije mogao da odvoji proizvod od pozadine.")
        return _mask_from_probability(prob)

    def _infer_probability(self, rgb: Image.Image) -> np.ndarray:
        assert self._session is not None
        # A plain border keeps a product that touches the photo edge from
        # being read as the backdrop.
        pad_x = max(1, int(round(rgb.width * INFER_PAD)))
        pad_y = max(1, int(round(rgb.height * INFER_PAD)))
        framed = Image.new("RGB", (rgb.width + 2 * pad_x, rgb.height + 2 * pad_y), (255, 255, 255))
        framed.paste(rgb, (pad_x, pad_y))

        resized = framed.resize((INFER_SIZE, INFER_SIZE), Image.Resampling.LANCZOS)
        array = np.asarray(resized).astype(np.float32)
        array = array / max(float(array.max()), 1e-6)
        array = (array - MEAN) / STD
        blob = np.transpose(array, (2, 0, 1))[None, ...].astype(np.float32)

        with self._infer_lock:
            outputs = self._session.run(None, {self._input_name: blob})

        pred = np.squeeze(outputs[0][:, 0, :, :])
        pred = 1.0 / (1.0 + np.exp(-pred))
        pred = np.clip(pred, 0.0, 1.0)
        mask = Image.fromarray((pred * 255).astype(np.uint8), mode="L")
        fx = pad_x / framed.width
        fy = pad_y / framed.height
        box = (
            int(round(INFER_SIZE * fx)),
            int(round(INFER_SIZE * fy)),
            int(round(INFER_SIZE * (1 - fx))),
            int(round(INFER_SIZE * (1 - fy))),
        )
        mask = mask.crop(box).resize(rgb.size, Image.Resampling.LANCZOS)
        return np.asarray(mask).astype(np.float32) / 255.0

    def _refine_subject(self, rgb: Image.Image, prob: np.ndarray) -> np.ndarray:
        """Look again at a close crop when the product does not fill the photo."""
        binary = prob > 0.5
        height, width = prob.shape
        if int(binary.sum()) < 64:
            return prob
        ys, xs = np.where(binary)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        box_w = x1 - x0 + 1
        box_h = y1 - y0 + 1
        if box_w * box_h > 0.62 * width * height:
            return prob
        if box_w > 0.9 * width and box_h > 0.9 * height:
            return prob

        pad_x = int(box_w * 0.28) + 12
        pad_y = int(box_h * 0.28) + 12
        left = max(0, x0 - pad_x)
        top = max(0, y0 - pad_y)
        right = min(width, x1 + 1 + pad_x)
        bottom = min(height, y1 + 1 + pad_y)
        if right - left > 0.92 * width and bottom - top > 0.92 * height:
            return prob

        crop = rgb.crop((left, top, right, bottom))
        zoom = _flip_if_background_kept(self._infer_probability(crop))
        zoom_fg = float((zoom > 0.5).mean())
        if zoom_fg < 0.04 or zoom_fg > 0.94:
            return prob

        refined = prob.copy()
        region = refined[top:bottom, left:right]
        before = int((region > 0.5).sum())
        merged = np.maximum(region, zoom)
        added = int((merged > 0.5).sum())
        if before > 0 and added > before * 2.4:
            near = np.asarray(
                Image.fromarray((region > 0.5).astype(np.uint8) * 255, mode="L").filter(
                    ImageFilter.MaxFilter(9)
                )
            ) > 127
            merged = np.maximum(region, np.where(near, zoom, 0))
        refined[top:bottom, left:right] = merged
        return refined


def _flip_if_background_kept(prob: np.ndarray) -> np.ndarray:
    """Turn the mask around when the model kept the backdrop and dropped the product."""
    height, width = prob.shape
    band = max(4, int(min(height, width) * 0.06))
    border = np.concatenate(
        [
            prob[:band, :].ravel(),
            prob[-band:, :].ravel(),
            prob[:, :band].ravel(),
            prob[:, -band:].ravel(),
        ]
    )
    center = prob[height // 4 : height - height // 4, width // 4 : width - width // 4]
    if float(border.mean()) > float(center.mean()) + 0.12 and float(border.mean()) > 0.45:
        return 1.0 - prob
    return prob


def _fill_subject_holes(binary: np.ndarray) -> np.ndarray:
    """Fill gaps inside the product, such as a white label the model treated as background."""
    background = ~binary
    padded = np.pad(background, 1, constant_values=True)
    outside = Image.fromarray(np.where(padded, np.uint8(255), np.uint8(0))).copy()
    ImageDraw.floodfill(outside, (0, 0), 128)
    reached = np.asarray(outside)[1:-1, 1:-1] == 128
    holes = background & ~reached
    if holes.any() and float(holes.mean()) <= 0.28:
        binary = binary | holes
    return binary


def _mask_from_probability(prob: np.ndarray) -> Image.Image:
    height, width = prob.shape
    work = prob
    if max(height, width) > 1400:
        small = Image.fromarray((np.clip(prob, 0, 1) * 255).astype(np.uint8), mode="L")
        small = small.resize(
            (max(1, int(width * 1400 / max(height, width))), max(1, int(height * 1400 / max(height, width)))),
            Image.Resampling.BILINEAR,
        )
        work = np.asarray(small).astype(np.float32) / 255.0

    solid = _fill_subject_holes(work > 0.45)
    alpha = np.where(solid, np.float32(255), np.float32(0))
    rim = (work > 0.18) & ~solid
    alpha[rim] = work[rim] * 255
    mask = Image.fromarray(alpha.astype(np.uint8), mode="L")
    if mask.size != (width, height):
        mask = mask.resize((width, height), Image.Resampling.LANCZOS)
    return mask.filter(ImageFilter.GaussianBlur(radius=0.4))


def _prepare_rgb(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode == "RGBA":
        base = Image.new("RGB", image.size, (255, 255, 255))
        base.paste(image, mask=image.getchannel("A"))
        image = base
    else:
        image = image.convert("RGB")

    long_side = max(image.size)
    if long_side > MAX_SOURCE_SIDE:
        scale = MAX_SOURCE_SIDE / long_side
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def _decontaminate_white(image: Image.Image) -> Image.Image:
    """Pull white background bleed out of semi-transparent edges."""
    arr = np.asarray(image).astype(np.float32)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]
    amount = alpha / 255.0
    edge = (alpha > 0) & (alpha < 250)
    safe = np.clip(amount, 1 / 255, 1)[:, :, None]
    foreground = np.clip((rgb - 255.0 * (1.0 - amount)[:, :, None]) / safe, 0, 255)
    lighter = foreground.mean(axis=2) + 10 < rgb.mean(axis=2)
    pale = rgb.min(axis=2) > 185
    use = edge & lighter & pale
    if np.any(use):
        rgb = rgb.copy()
        rgb[use] = foreground[use]
        arr = arr.copy()
        arr[:, :, :3] = rgb
    return Image.fromarray(arr.astype(np.uint8), mode="RGBA")


def _parse_hex(value: str) -> tuple[int, int, int, int]:
    text = value.strip()
    if len(text) != 7 or not text.startswith("#"):
        raise EngineError("Boja pozadine treba da bude u obliku #RRGGBB.")
    try:
        red = int(text[1:3], 16)
        green = int(text[3:5], 16)
        blue = int(text[5:7], 16)
    except ValueError as exc:
        raise EngineError("Boja pozadine treba da bude u obliku #RRGGBB.") from exc
    return red, green, blue, 255


def _place_on_canvas(
    subject: Image.Image,
    *,
    width: int,
    height: int,
    color: tuple[int, int, int, int],
    shadow: bool,
    anchor: float,
) -> Image.Image:
    scale = min(width / subject.width, height / subject.height, 1.0)
    size = (
        min(width, max(1, int(round(subject.width * scale)))),
        min(height, max(1, int(round(subject.height * scale)))),
    )
    if size == subject.size:
        fitted = subject
    else:
        fitted = subject.resize(size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (width, height), color)
    x = (width - fitted.width) // 2
    y = int((height - fitted.height) * anchor)
    y = min(max(y, 0), max(0, height - fitted.height))

    if shadow:
        canvas = _paint_shadow(canvas, fitted, x, y)
    canvas.paste(fitted, (x, y), fitted)
    return canvas


def _paint_shadow(canvas: Image.Image, subject: Image.Image, x: int, y: int) -> Image.Image:
    blur = max(8, int(subject.width * 0.045))
    alpha = subject.getchannel("A").filter(ImageFilter.GaussianBlur(radius=blur))
    alpha = alpha.point(lambda px: int(px * 0.22))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ink = Image.new("RGBA", subject.size, (0, 0, 0, 0))
    ink.putalpha(alpha)
    shift = max(6, int(subject.height * 0.018))
    layer.paste(ink, (x, y + shift), ink)
    return Image.alpha_composite(canvas, layer)
