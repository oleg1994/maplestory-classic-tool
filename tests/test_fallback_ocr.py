import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from rapidfuzz import fuzz

from maplestory_classic_tool.catalog import fold_il
from maplestory_classic_tool.detect import ShopLocator
from maplestory_classic_tool.fallback_ocr import WindowsOcr
from maplestory_classic_tool.layout import load_layout, to_base
from maplestory_classic_tool.shop import parse_price

ocr = WindowsOcr.create()
pytestmark = pytest.mark.skipif(ocr is None, reason="Windows OCR unavailable")

ASSETS = Path(__file__).parent.parent / "maplestory_classic_tool" / "assets"
SHOPS = Path(__file__).parent / "fixtures" / "shops"
LAYOUT = load_layout(ASSETS / "layout.json")
LOCATOR = ShopLocator(cv2.imread(str(ASSETS / "anchor.png"), cv2.IMREAD_GRAYSCALE))


def rows(fixture):
    gray = to_base(cv2.imread(str(SHOPS / f"{fixture}.png"), cv2.IMREAD_GRAYSCALE), LAYOUT.base_size)
    LOCATOR.reset()
    anchor = LOCATOR.locate(gray)
    labels = json.loads((SHOPS / f"{fixture}.json").read_text(encoding="utf-8"))["rows"]
    for i, lab in enumerate(labels):
        if isinstance(lab, dict):
            ox, oy = LAYOUT.row_origin(anchor, i)
            def crop(box):
                x, y, w, h = box
                return gray[oy + y:oy + y + h, ox + x:ox + x + w]
            yield i, lab, crop(LAYOUT.name_box), crop(LAYOUT.price_box)


def test_reads_fixture_name_box():
    _, lab, name, _ = next(rows("shop1"))
    read = ocr.read(name)
    assert fuzz.ratio(fold_il(read), fold_il(lab["name"])) >= 90, read


def test_reads_fixture_price_box():
    sample = [r for f in ("shop1", "shop2", "shop4") for r in rows(f)][:5]
    assert len(sample) == 5
    ok = sum(parse_price(ocr.read(p)) == (lab["qty"], lab["price"]) for _, lab, _, p in sample)
    assert ok >= 4, ok


def test_blank_crop_returns_empty():
    assert ocr.read(np.full((14, 158), 255, np.uint8)) == ""


def test_empty_crop_returns_empty():
    assert ocr.read(np.zeros((0, 10), np.uint8)) == ""


def test_tiny_crop_does_not_raise():
    assert isinstance(ocr.read(np.full((1, 1), 255, np.uint8)), str)


def test_all_black_crop_does_not_raise():
    assert isinstance(ocr.read(np.zeros((14, 158), np.uint8)), str)


def test_three_channel_crop_returns_empty():
    assert ocr.read(np.full((14, 158, 3), 255, np.uint8)) == ""
