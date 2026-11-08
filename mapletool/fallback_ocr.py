"""Windows.Media.Ocr fallback for crops the glyph matcher cannot fully read."""
import asyncio
import logging

import cv2
import numpy as np

log = logging.getLogger(__name__)

UPSCALE = 4  # 4x INTER_CUBIC measured 27/27 prices, name ratio 100 (3x nearest: 2/27, 95.6)
PAD = 8


class WindowsOcr:
    def __init__(self, engine, modules):
        self._engine = engine
        self._m = modules

    @classmethod
    def create(cls):
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.storage.streams import DataWriter
        except Exception:
            return None
        try:
            engine = None
            try:
                engine = OcrEngine.try_create_from_language(Language("en-US"))
            except Exception:
                engine = None
            if engine is None:
                engine = OcrEngine.try_create_from_user_profile_languages()
        except Exception:
            return None
        if engine is None:
            return None
        return cls(engine, (BitmapPixelFormat, SoftwareBitmap, DataWriter, OcrEngine))

    def _bitmap(self, img: np.ndarray):
        BitmapPixelFormat, SoftwareBitmap, DataWriter, _ = self._m
        h, w = img.shape
        writer = DataWriter()
        writer.write_bytes(img.tobytes())
        return SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.GRAY8, w, h)

    def read(self, gray: np.ndarray) -> str:
        """Text in the crop, or "" on odd input or any OCR failure (never raises)."""
        if not isinstance(gray, np.ndarray) or gray.ndim != 2 or gray.size == 0:
            return ""
        try:
            gray = np.ascontiguousarray(gray, dtype=np.uint8)
            img = cv2.resize(gray, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
            img = cv2.copyMakeBorder(img, PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=255)
            img = np.ascontiguousarray(img)
            if max(img.shape) > self._m[3].max_image_dimension:
                return ""
            bitmap = self._bitmap(img)

            async def run():
                return await self._engine.recognize_async(bitmap)

            result = asyncio.run(run())
            return " ".join(line.text for line in result.lines).strip()
        except Exception:
            log.warning("Windows OCR read failed", exc_info=True)
            return ""
