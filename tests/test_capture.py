import numpy as np
import pytest

from maplestory_classic_tool.capture import Grabber, pick_output

OUTPUTS = [((0, 0), (0, 0, 3840, 2160)),        # primary 4K
           ((0, 1), (3840, 0, 7680, 2160)),     # second monitor to the right
           ((1, 0), (-1920, 0, 0, 1080))]       # monitor on another adapter, left of primary


def test_pick_output_primary():
    assert pick_output(OUTPUTS, (0, 0, 3840, 2160)) == ((0, 0), (0, 0, 3840, 2160))


def test_pick_output_second_monitor_translates_region():
    # the user's windowed client: 2560x1600 at screen (4229, 381)
    assert pick_output(OUTPUTS, (4229, 381, 2560, 1600)) == ((0, 1), (389, 381, 2949, 1981))


def test_pick_output_negative_desktop_coords():
    assert pick_output(OUTPUTS, (-1500, 100, 1280, 720)) == ((1, 0), (420, 100, 1700, 820))


def test_pick_output_spanning_or_offscreen_none():
    assert pick_output(OUTPUTS, (3000, 0, 1920, 1080)) is None  # spans two monitors
    assert pick_output(OUTPUTS, (100, 2000, 800, 600)) is None  # off the bottom
    assert pick_output([], (0, 0, 10, 10)) is None


class FakeCam:
    def __init__(self, key, frames):
        self.key, self.frames, self.regions, self.released = key, frames, [], False

    def grab(self, region):
        self.regions.append(region)
        f = self.frames.pop(0) if self.frames else None
        if isinstance(f, Exception):
            raise f
        return f

    def release(self):
        self.released = True


class FakeDx:
    def __init__(self, frames=None, fail_create=()):
        self.cams, self.frames, self.fail_create = {}, frames or {}, set(fail_create)
        self.refreshes = 0

    def outputs(self):
        return OUTPUTS

    def refresh(self):
        self.refreshes += 1

    def create(self, key):
        if key in self.fail_create:
            raise RuntimeError("no duplication")
        self.cams[key] = FakeCam(key, self.frames.get(key, []))
        return self.cams[key]


def bgr(h, w, v):
    return np.full((h, w, 3), v, np.uint8)


def make(dx, mss_calls):
    def mss_grab(rect):
        mss_calls.append(rect)
        return np.full((rect[3], rect[2]), 77, np.uint8)
    return Grabber((1366, 768), dx=dx, mss_grab=mss_grab)


def test_grab_uses_output_camera_and_base():
    dx = FakeDx({(0, 1): [bgr(1600, 2560, 200)]})
    mss = []
    g = make(dx, mss)
    out = g.grab((4229, 381, 2560, 1600), (1280, 800))
    assert out.shape == (800, 1280) and int(out[0, 0]) == 200
    assert dx.cams[(0, 1)].regions == [(389, 381, 2949, 1981)]
    assert mss == []


def test_grab_spanning_falls_back_to_mss_then_back_to_dxcam():
    dx = FakeDx({(0, 0): [bgr(2160, 3840, 10)]})
    mss = []
    g = make(dx, mss)
    span = (3000, 0, 1920, 1080)
    out = g.grab(span, (1366, 768))
    assert mss == [span] and int(out[0, 0]) == 77
    out = g.grab((0, 0, 3840, 2160), (1366, 768))  # moved back onto the primary
    assert int(out[0, 0]) == 10 and len(mss) == 1


def test_grab_error_marks_output_failed_and_uses_mss():
    dx = FakeDx({(0, 0): [RuntimeError("lost"), bgr(2160, 3840, 10)]})
    mss = []
    g = make(dx, mss)
    rect = (0, 0, 3840, 2160)
    assert int(g.grab(rect, (1366, 768))[0, 0]) == 77
    assert int(g.grab(rect, (1366, 768))[0, 0]) == 77  # failed output not retried
    assert len(mss) == 2
    # another output still works
    dx.frames[(0, 1)] = [bgr(1600, 2560, 5)]
    assert int(g.grab((4229, 381, 2560, 1600), (1280, 800))[0, 0]) == 5


def test_create_failure_uses_mss():
    dx = FakeDx(fail_create=[(0, 0)])
    mss = []
    g = make(dx, mss)
    assert int(g.grab((0, 0, 3840, 2160), (1366, 768))[0, 0]) == 77


def test_unchanged_frame_reuses_last_at_any_base():
    dx = FakeDx({(0, 0): [bgr(2160, 3840, 50), None, None]})
    mss = []
    g = make(dx, mss)
    rect = (0, 0, 3840, 2160)
    a = g.grab(rect, (1366, 768))
    b = g.grab(rect, (1366, 768))
    assert b is a
    c = g.grab(rect, (1920, 1080))  # probing another base while the screen is static
    assert c.shape == (1080, 1920) and int(c[0, 0]) == 50
    assert mss == []


def test_unchanged_after_rect_change_uses_mss():
    dx = FakeDx({(0, 0): [bgr(2160, 3840, 50), None]})
    mss = []
    g = make(dx, mss)
    g.grab((0, 0, 3840, 2160), (1366, 768))
    out = g.grab((0, 0, 2732, 1536), (1366, 768))
    assert mss == [(0, 0, 2732, 1536)] and int(out[0, 0]) == 77


def test_grab_default_base_is_constructor_base():
    dx = FakeDx({(0, 0): [bgr(2160, 3840, 9)]})
    g = make(dx, [])
    assert g.grab((0, 0, 3840, 2160)).shape == (768, 1366)


def test_close_releases_cameras():
    dx = FakeDx({(0, 0): [bgr(2160, 3840, 9)], (0, 1): [bgr(1600, 2560, 9)]})
    g = make(dx, [])
    g.grab((0, 0, 3840, 2160), (1366, 768))
    g.grab((4229, 381, 2560, 1600), (1280, 800))
    g.close()
    assert all(c.released for c in dx.cams.values()) and len(dx.cams) == 2
    g.close()  # idempotent


def test_no_dxcam_backend_uses_mss():
    mss = []
    g = make(None, mss)
    assert int(g.grab((0, 0, 100, 100), (1366, 768))[0, 0]) == 77


@pytest.mark.live
def test_dxcam_outputs_enumerate():
    from maplestory_classic_tool.capture import DxcamBackend
    assert DxcamBackend().outputs()


def test_rebase_reuses_last_frame_without_capturing():
    dx = FakeDx({(0, 0): [bgr(2160, 3840, 60)]})
    mss = []
    g = make(dx, mss)
    rect = (0, 0, 3840, 2160)
    g.grab(rect, (1366, 768))
    out = g.rebase(rect, (1364, 768))
    assert out.shape == (768, 1364) and int(out[0, 0]) == 60
    assert len(dx.cams[(0, 0)].regions) == 1 and mss == []  # no new capture
    other = g.rebase((0, 0, 2732, 1536), (1366, 768))  # no frame for that rect yet: grabs
    assert other.shape == (768, 1366)



def test_failed_output_retried_after_30s_with_refreshed_outputs():
    from maplestory_classic_tool.capture import OUTPUT_RETRY_S
    assert OUTPUT_RETRY_S == 30.0
    t = {"now": 100.0}
    dx = FakeDx(fail_create=[(0, 0)])
    mss = []
    g = Grabber((1366, 768), dx=dx, mss_grab=lambda r: (mss.append(r), np.full((r[3], r[2]), 77, np.uint8))[1],
                clock=lambda: t["now"])
    rect = (0, 0, 3840, 2160)
    g.grab(rect, (1366, 768))
    t["now"] = 129.0
    g.grab(rect, (1366, 768))
    assert len(mss) == 2 and dx.refreshes == 0 and (0, 0) not in dx.cams  # not retried yet
    dx.fail_create.clear()
    dx.frames[(0, 0)] = [bgr(2160, 3840, 10)]
    t["now"] = 130.5
    out = g.grab(rect, (1366, 768))
    assert dx.refreshes == 1 and int(out[0, 0]) == 10 and len(mss) == 2


def test_grab_error_output_retried_after_30s():
    t = {"now": 0.0}
    dx = FakeDx({(0, 0): [RuntimeError("lost")]})
    g = Grabber((1366, 768), dx=dx, mss_grab=lambda r: np.full((r[3], r[2]), 77, np.uint8),
                clock=lambda: t["now"])
    rect = (0, 0, 3840, 2160)
    assert int(g.grab(rect, (1366, 768))[0, 0]) == 77
    dx.frames[(0, 0)] = [bgr(2160, 3840, 5)]
    t["now"] = 31.0
    assert int(g.grab(rect, (1366, 768))[0, 0]) == 5


# --- colour crops for item icons ------------------------------------------------

def test_crop_to_base_matches_resize_then_slice():
    import cv2

    from maplestory_classic_tool.layout import crop_to_base, to_base
    rng = np.random.default_rng(1)
    full = cv2.resize(rng.integers(0, 255, (80, 120, 3), np.uint8), (2560, 1600), interpolation=cv2.INTER_NEAREST)
    box = (400, 300, 35, 35)
    got = crop_to_base(full, (1228, 768), box)
    ref = to_base(full, (1228, 768))[300:335, 400:435]
    assert got.shape == (35, 35, 3) and got.dtype == np.uint8
    assert np.abs(got.astype(int) - ref.astype(int)).mean() < 6
    same = crop_to_base(ref.copy(), (35, 35), (0, 0, 35, 35))
    assert np.array_equal(same, ref)  # no resize at the base size
    assert crop_to_base(full, (1228, 768), (-1, 0, 35, 35)) is None
    assert crop_to_base(full, (1228, 768), (1200, 0, 35, 35)) is None
    bgra = np.dstack([full, np.full(full.shape[:2], 255, np.uint8)])
    assert crop_to_base(bgra, (1228, 768), box).shape == (35, 35, 3)


def test_grabber_crops_from_last_colour_frame():
    frame = bgr(1600, 2560, 0)
    frame[:, :, 2] = 200  # red
    dx = FakeDx({(0, 1): [frame]})
    g = make(dx, [])
    rect = (4229, 381, 2560, 1600)
    g.grab(rect, (1228, 768))
    a, off = g.crops(rect, (1228, 768), [(10, 10, 35, 35), (1220, 0, 35, 35)])
    assert a.shape == (35, 35, 3) and tuple(int(c) for c in a[5, 5]) == (0, 0, 200)
    assert off is None
    assert g.crops((0, 0, 10, 10), (1228, 768), [(0, 0, 5, 5)]) == [None]  # other rect: no frame


def test_grabber_crops_none_for_gray_only_backend():
    g = make(None, [])
    rect = (0, 0, 1366, 768)
    g.grab(rect)
    assert g.crops(rect, (1366, 768), [(0, 0, 5, 5)]) == [None]


def test_grabber_mss_colour_frame_gives_crops():
    def mss_grab(rect):
        out = np.zeros((rect[3], rect[2], 4), np.uint8)
        out[..., 1] = 150
        return out
    g = Grabber((1366, 768), dx=None, mss_grab=mss_grab)
    rect = (0, 0, 1366, 768)
    gray = g.grab(rect)
    assert gray.shape == (768, 1366)
    c, = g.crops(rect, (1366, 768), [(0, 0, 5, 5)])
    assert c.shape == (5, 5, 3) and int(c[0, 0, 1]) == 150
