"""Entry point: offline image mode (--image) or live overlay."""
import argparse

import cv2

from mapletool.draw import CvRenderer
from mapletool.exp import ExpTracker, MesoLine, build_exp_box, exp_box_to_screen, exp_prims
from mapletool.fallback_ocr import WindowsOcr
from mapletool.layout import CONFIRM_MIN, BaseResolver, best_score, crop_to_base, to_base
from mapletool.live import build_exp_reader, build_meso_reader, build_scanner, run_live, selftest
from mapletool.sidelist import ShopListing, build_panel, panel_prims, panel_to_screen


def scan_any_base(scanner, gray_full, base_sizes):
    """(base, ScanResult): the cached base for this size, else the best-scoring candidate like live mode
    (anchor score over the 768 family, >= CONFIRM_MIN), else the first candidate with an anchor."""
    resolver = BaseResolver(base_sizes)
    client = (gray_full.shape[1], gray_full.shape[0])
    base, confirmed = resolver.current(client)
    tried = [base] if confirmed else []
    score = getattr(getattr(scanner, "locator", None), "score", None)
    if not confirmed and score is not None:
        best, s = best_score({b: score(to_base(gray_full, b)) for b in resolver.family(client)})
        if best is not None and s >= CONFIRM_MIN:
            tried.append(best)
    tried += [b for b in resolver.candidates(client) if b not in tried]
    first = None
    for base in tried:
        scanner.reset()
        result = scanner.scan(to_base(gray_full, base))
        if first is None:
            first = (base, result)
        if result.anchor is not None:
            return base, result
    return first


def draw_panel(img, panel) -> tuple:
    """Draw a screen-pixel sidelist.Panel onto a BGR image with the same primitives as the live overlay
    (text measured in Segoe UI, growing away from the shop). Returns the drawn (x, y, w, h)."""
    r = CvRenderer(img)
    prims, x, w = panel_prims(panel, r.measure)
    r.render(prims)
    return x, panel.y, w, panel.h


def draw_exp(img, box) -> None:
    """Draw a screen-pixel exp.ExpBox onto a BGR image (same primitives as the live overlay)."""
    r = CvRenderer(img)
    r.render(exp_prims(box, r.measure))


DEMO_RATE = 1_240_000  # --exp-demo: EXP/hour and session gain shown instead of the single-frame "—"
DEMO_GAIN = 312_450
DEMO_MESO_RATE = 452_000  # --exp-demo: mesos/hour and session meso gain
DEMO_MESO_GAIN = 45_300


class _DemoTracker(ExpTracker):
    """A tracker with a fixed rate and session gain (the time to level uses the real reading)."""

    def rate(self, now):
        return float(DEMO_RATE) if self.exp is not None else None

    @property
    def gained(self):
        return DEMO_GAIN

    @gained.setter
    def gained(self, value):
        pass


def read_exp(reader, img, base, gray, demo: bool):
    """(reading, ExpTracker) for one still image: the parsed values with rate "—" (a single frame), or
    with --exp-demo a fixed rate and session gain so the full box can be previewed."""
    reading = reader.read(gray, lambda box: crop_to_base(img, base, box))
    tracker = _DemoTracker() if demo else ExpTracker()
    if reading is not None and reading.exp is not None:
        tracker.add(reading.exp, reading.pct, now=0.0)
    return reading, tracker


def run_image(args) -> int:
    scanner, settings = build_scanner(args.catalog)
    layout = scanner.layout

    img = cv2.imread(args.image, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read image: {args.image}")
    base, result = scan_any_base(scanner, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), settings.base_sizes)

    for v in result.verdicts:
        li = v.listing
        print(f"row={li.row} status={v.status} name={li.name} qty={li.qty} price={li.price} "
              f"npc_total={v.npc_total} profit={v.profit}")

    shot = img.copy()  # the EXP and meso reads must not see the side list drawn below
    if result.anchor is not None:
        listing = ShopListing(min_seen=1)  # a still image is a single frame
        icons = {v.listing.row: crop_to_base(img, base, layout.icon_rect(result.anchor, v.listing.row))
                 for v in result.verdicts if layout.icon_box is not None}
        listing.observe(result.shop_id, result.verdicts, 0.0, {r: c for r, c in icons.items() if c is not None})
        panel = build_panel(listing, result.anchor, layout, args.show_all or settings.show_all, frame_w=base[0],
                            frame_h=base[1])
        print(f"base={base[0]}x{base[1]} panel: {panel.title}")
        screen = panel_to_screen(panel, (0, 0), img.shape[1] / base[0])
        print(f"panel_rect={screen.x},{screen.y},{screen.w},{screen.h}")
        print("drawn_rect=%d,%d,%d,%d" % draw_panel(img, screen))
    else:
        print(f"base={base[0]}x{base[1]} no shop window found")
    gray_base = to_base(cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY), base)
    reader = build_exp_reader(scanner.fallback or WindowsOcr.create())
    reading, tracker = read_exp(reader, shot, base, gray_base, args.exp_demo)
    if reading is None:
        print("exp: status bar not found")
    else:
        print(f"exp: bar_anchor={reading.anchor[0]},{reading.anchor[1]} exp={reading.exp} pct={reading.pct}")
        m = build_meso_reader().read(gray_base)
        print(f"mesos: visible={m.visible} mesos={m.mesos}")
        meso = MesoLine(m.visible, DEMO_MESO_RATE if args.exp_demo and m.visible else None,
                        DEMO_MESO_GAIN if args.exp_demo and m.visible else 0)
        box = build_exp_box(reading.anchor, reader.layout, tracker, 0.0, frame_w=base[0], meso=meso)
        print("exp_box: " + " | ".join("".join(t for t, _ in line) for line in box.lines))
        lx, ly, lw, lh = reader.layout.level_box
        level = crop_to_base(img, base, (reading.anchor[0] + lx, reading.anchor[1] + ly, lw, lh))
        draw_exp(img, exp_box_to_screen(box, (0, 0), img.shape[1] / base[0], level))
    if args.out:
        cv2.imwrite(args.out, img)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mapletool")
    p.add_argument("--image")
    p.add_argument("--out")
    p.add_argument("--show-all", action="store_true")
    p.add_argument("--exp-demo", action="store_true", help="--image: preview the EXP box with a fake rate")
    p.add_argument("--catalog")
    p.add_argument("--gui", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.image:
        return run_image(args)
    if args.gui:
        from mapletool.panel import main as gui_main
        return gui_main()
    return run_live()


if __name__ == "__main__":
    raise SystemExit(main())
