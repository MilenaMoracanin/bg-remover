"""Local product-photo background removal and ad-format compositing."""

from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.request
from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter, ImageOps

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
MAX_SOURCE_SIDE = 3200
INFER_SIZE = 1024

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class EngineError(Exception):
    pass


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
        soft = mask.filter(ImageFilter.GaussianBlur(radius=0.7))
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
        padding: float,
        shadow: bool,
        image_format: str,
        anchor: float,
    ) -> tuple[bytes, str, int, int]:
        cut = self.cutout(image)
        box = cut.getbbox()
        if box is None:
            raise EngineError("Na slici nije pronađen proizvod.")

        subject = cut.crop(box)
        padding = min(max(padding, 0.0), 0.28)
        anchor = min(max(anchor, 0.0), 1.0)
        transparent = background == "transparent" or width == 0 or height == 0

        if transparent:
            canvas = _canvas_around(subject, padding)
        else:
            color = _parse_hex(background)
            canvas = _place_on_canvas(
                subject,
                width=width,
                height=height,
                color=color,
                padding=padding,
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
                quality=93,
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

        def report(blocks: int, block_size: int, total: int) -> None:
            if total > 0:
                self.progress = min(blocks * block_size / total, 0.99)
                percent = int(self.progress * 100)
                self.message = f"Preuzimam model… {percent}%"

        try:
            urllib.request.urlretrieve(MODEL_URL, tmp, reporthook=report)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise EngineError("Preuzimanje modela nije uspelo. Proveri internet i pokušaj ponovo.")

        digest = hashlib.md5(tmp.read_bytes()).hexdigest()
        if digest != MODEL_MD5:
            tmp.unlink(missing_ok=True)
            raise EngineError("Preuzeti model je oštećen. Pokušaj ponovo.")
        tmp.replace(MODEL_PATH)
        self.progress = 1.0
        return MODEL_PATH

    def _mask(self, rgb: Image.Image) -> Image.Image:
        assert self._session is not None
        resized = rgb.resize((INFER_SIZE, INFER_SIZE), Image.Resampling.LANCZOS)
        array = np.asarray(resized).astype(np.float32)
        array = array / max(float(array.max()), 1e-6)
        array = (array - MEAN) / STD
        blob = np.transpose(array, (2, 0, 1))[None, ...].astype(np.float32)

        with self._infer_lock:
            outputs = self._session.run(None, {self._input_name: blob})

        pred = outputs[0][:, 0, :, :]
        pred = 1.0 / (1.0 + np.exp(-pred))
        pred = np.squeeze(pred)
        span = float(pred.max() - pred.min())
        if span < 1e-6:
            raise EngineError("Model nije mogao da odvoji proizvod od pozadine.")
        pred = (pred - float(pred.min())) / span
        mask = Image.fromarray((pred * 255).astype(np.uint8), mode="L")
        mask = mask.resize(rgb.size, Image.Resampling.LANCZOS)
        cleaned = np.asarray(mask)
        cleaned = cleaned.copy()
        cleaned[cleaned < 8] = 0
        cleaned[cleaned > 250] = 255
        return Image.fromarray(cleaned, mode="L")


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


def _canvas_around(subject: Image.Image, padding: float) -> Image.Image:
    pad_x = max(12, int(subject.width * padding))
    pad_y = max(12, int(subject.height * padding))
    canvas = Image.new(
        "RGBA",
        (subject.width + pad_x * 2, subject.height + pad_y * 2),
        (0, 0, 0, 0),
    )
    canvas.paste(subject, (pad_x, pad_y), subject)
    return canvas


def _place_on_canvas(
    subject: Image.Image,
    *,
    width: int,
    height: int,
    color: tuple[int, int, int, int],
    padding: float,
    shadow: bool,
    anchor: float,
) -> Image.Image:
    avail_w = max(1, int(width * (1 - 2 * padding)))
    avail_h = max(1, int(height * (1 - 2 * padding)))
    scale = min(avail_w / subject.width, avail_h / subject.height)
    size = (max(1, int(subject.width * scale)), max(1, int(subject.height * scale)))
    fitted = subject.resize(size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (width, height), color)
    x = (width - fitted.width) // 2
    min_y = int(height * padding)
    max_y = max(min_y, height - fitted.height - int(height * padding))
    y = int((height - fitted.height) * anchor)
    y = min(max(y, min_y), max_y)

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
