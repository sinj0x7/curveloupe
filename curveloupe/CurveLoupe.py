


import os
import sys
import struct
import tempfile
import traceback
import zlib

# --------------------------------------------------------------------------
# Sibling module import (curve_math / presets).
#
# When Fusion executes this script __file__ is normally defined, but we fall
# back to scanning the standard Fusion Scripts folders for a "curveloupe"
# directory just in case.
# --------------------------------------------------------------------------


def _candidate_module_dirs():
    dirs = []
    try:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass

    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "Blackmagic Design", "DaVinci Resolve",
                                  "Support", "Fusion", "Scripts"))
    progdata = os.environ.get("PROGRAMDATA")
    if progdata:
        roots.append(os.path.join(progdata, "Blackmagic Design", "DaVinci Resolve",
                                  "Fusion", "Scripts"))
    home = os.path.expanduser("~")
    roots.extend([
        os.path.join(home, "Library", "Application Support", "Blackmagic Design",
                     "DaVinci Resolve", "Fusion", "Scripts"),
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts",
        os.path.join(home, ".local", "share", "DaVinciResolve", "Fusion", "Scripts"),
        "/opt/resolve/Fusion/Scripts",
    ])
    for root in roots:
        for sub in ("Comp", "Edit", "Tool", "Utility"):
            dirs.append(os.path.join(root, sub, "curveloupe"))
    return [d for d in dirs if d and os.path.isdir(d)]


_IMPORT_ERROR = None
for _d in _candidate_module_dirs():
    if _d not in sys.path:
        sys.path.insert(0, _d)
try:
    import curve_math
    import presets
except ImportError as _e:  # reported in the UI later
    _IMPORT_ERROR = str(_e)
    curve_math = None
    presets = None


# --------------------------------------------------------------------------
# Fusion / Resolve bootstrap.
#
# Inside Resolve's Scripts menu the globals `fu`/`fusion` and `bmd` are
# injected. When run externally (development), fall back to the bundled
# DaVinciResolveScript module.
# --------------------------------------------------------------------------


def _get_fusion_env():
    fu_obj = globals().get("fu") or globals().get("fusion")
    bmd_obj = globals().get("bmd")
    if fu_obj is not None and bmd_obj is not None:
        return fu_obj, bmd_obj

    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        if sys.platform.startswith("win"):
            mod_dir = os.path.join(os.environ.get("PROGRAMDATA", ""),
                                   "Blackmagic Design", "DaVinci Resolve",
                                   "Support", "Developer", "Scripting", "Modules")
        elif sys.platform == "darwin":
            mod_dir = ("/Library/Application Support/Blackmagic Design/"
                       "DaVinci Resolve/Developer/Scripting/Modules")
        else:
            mod_dir = "/opt/resolve/Developer/Scripting/Modules"
        if mod_dir not in sys.path:
            sys.path.append(mod_dir)
        import DaVinciResolveScript as dvr

    if fu_obj is None:
        resolve = dvr.scriptapp("Resolve")
        if resolve is None:
            raise RuntimeError("Could not connect to a running DaVinci Resolve. "
                               "Run this script from Workspace > Scripts inside Resolve.")
        fu_obj = resolve.Fusion()
    return fu_obj, (bmd_obj or dvr)


# --------------------------------------------------------------------------
# Helpers for tables coming back from the Fusion API (Lua tables arrive as
# Python dicts with float / string keys).
# --------------------------------------------------------------------------


def lua_get(tbl, idx):
    if tbl is None:
        return None
    if isinstance(tbl, dict):
        for key in (idx, float(idx), str(idx)):
            if key in tbl:
                return tbl[key]
        return None
    if isinstance(tbl, (list, tuple)):
        try:
            return tbl[idx - 1]
        except (IndexError, TypeError):
            return None
    return None


def lua_pair(tbl):
    x = lua_get(tbl, 1)
    y = lua_get(tbl, 2)
    if x is None or y is None:
        return None
    return float(x), float(y)


def event_pos(ev):
    """Extract (x, y) from a UIManager mouse event dictionary."""
    pos = None
    if isinstance(ev, dict):
        pos = ev.get("Pos")
        if pos is None:
            pos = ev.get("GlobalPos")
    p = lua_pair(pos)
    if p is not None:
        return p
    if isinstance(pos, dict):
        for kx, ky in (("X", "Y"), ("x", "y")):
            if kx in pos and ky in pos:
                return float(pos[kx]), float(pos[ky])
    return None


# --------------------------------------------------------------------------
# Canvas rasterizer: draws the grid + curve into an RGB buffer and encodes
# it as a PNG (pure stdlib), which is then shown as the icon of a flat
# UIManager button. UIManager has no native paint canvas, so this is the
# standard workaround -- and it stays fully inside BMD's toolkit.
# --------------------------------------------------------------------------

CANVAS_W = 640
CANVAS_H = 640

# Fusion-style dark charcoal + amber spline accent.
COL_BG = (24, 24, 24)
COL_BAND = (31, 31, 31)          # background band for the 0..1 value range
COL_GRID = (41, 41, 41)
COL_GRID_MID = (54, 54, 54)
COL_UNIT = (96, 96, 96)
COL_DIAG = (58, 58, 58)
COL_CURVE = (255, 166, 61)       # amber, matches Fusion's spline color
COL_HANDLE_LINE = (150, 150, 150)
COL_HANDLE = (247, 217, 165)
COL_ENDPOINT = (255, 166, 61)
COL_SELECTED = (255, 255, 255)
COL_KNOB_CORE = (30, 30, 30)

HIT_RADIUS = 16.0                # generous pixel hit radius -- no zooming needed


class CurveCanvas(object):
    PAD_L = 26
    PAD_R = 26
    PAD_T = 24
    PAD_B = 24
    Y_MIN = -0.75
    Y_MAX = 1.75

    def __init__(self):
        self.w = CANVAS_W
        self.h = CANVAS_H
        self.plot_w = self.w - self.PAD_L - self.PAD_R
        self.plot_h = self.h - self.PAD_T - self.PAD_B
        self._bg = self._build_background()

    # ---- coordinate mapping ------------------------------------------------

    def to_px(self, nx, ny):
        px = self.PAD_L + nx * self.plot_w
        py = self.PAD_T + (self.Y_MAX - ny) / (self.Y_MAX - self.Y_MIN) * self.plot_h
        return px, py

    def to_norm(self, px, py):
        nx = (px - self.PAD_L) / float(self.plot_w)
        ny = self.Y_MAX - (py - self.PAD_T) / float(self.plot_h) * (self.Y_MAX - self.Y_MIN)
        return nx, ny

    # ---- low level drawing -------------------------------------------------

    def _fill_rect(self, buf, x0, y0, x1, y1, color):
        x0 = max(0, int(x0))
        x1 = min(self.w - 1, int(x1))
        y0 = max(0, int(y0))
        y1 = min(self.h - 1, int(y1))
        row = bytes(color) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            i = (y * self.w + x0) * 3
            buf[i:i + len(row)] = row

    def _disc(self, buf, cx, cy, r, color):
        c = bytes(color)
        r2 = r * r
        for dy in range(-r, r + 1):
            y = int(cy) + dy
            if y < 0 or y >= self.h:
                continue
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy > r2:
                    continue
                x = int(cx) + dx
                if 0 <= x < self.w:
                    i = (y * self.w + x) * 3
                    buf[i:i + 3] = c

    def _square(self, buf, cx, cy, half, color):
        self._fill_rect(buf, cx - half, cy - half, cx + half, cy + half, color)

    def _ring(self, buf, cx, cy, r_in, r_out, color):
        c = bytes(color)
        lo2 = r_in * r_in
        hi2 = r_out * r_out
        for dy in range(-r_out, r_out + 1):
            y = int(cy) + dy
            if y < 0 or y >= self.h:
                continue
            for dx in range(-r_out, r_out + 1):
                d2 = dx * dx + dy * dy
                if d2 < lo2 or d2 > hi2:
                    continue
                x = int(cx) + dx
                if 0 <= x < self.w:
                    i = (y * self.w + x) * 3
                    buf[i:i + 3] = c

    def _line(self, buf, x0, y0, x1, y1, color, radius=0):
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for i in range(steps + 1):
            t = i / float(steps)
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            if radius <= 0:
                xi, yi = int(x), int(y)
                if 0 <= xi < self.w and 0 <= yi < self.h:
                    j = (yi * self.w + xi) * 3
                    buf[j:j + 3] = bytes(color)
            else:
                self._disc(buf, x, y, radius, color)

    def _dashed_line(self, buf, x0, y0, x1, y1, color, dash=6, gap=6):
        length = max(abs(x1 - x0), abs(y1 - y0))
        steps = int(length) + 1
        period = dash + gap
        for i in range(steps + 1):
            if (i % period) >= dash:
                continue
            t = i / float(steps)
            x = int(x0 + (x1 - x0) * t)
            y = int(y0 + (y1 - y0) * t)
            if 0 <= x < self.w and 0 <= y < self.h:
                j = (y * self.w + x) * 3
                buf[j:j + 3] = bytes(color)

    # ---- static background -------------------------------------------------

    def _build_background(self):
        buf = bytearray(bytes(COL_BG) * (self.w * self.h))

        # Slightly lighter band across the 0..1 value range.
        _, band_top = self.to_px(0.0, 1.0)
        _, band_bot = self.to_px(0.0, 0.0)
        self._fill_rect(buf, 0, band_top, self.w - 1, band_bot, COL_BAND)

        # Horizontal grid lines every 0.1 across the whole visible range.
        for i in range(-7, 18):
            v = i / 10.0
            _, py = self.to_px(0.0, v)
            if i in (0, 10):
                color = COL_UNIT
            elif i == 5:
                color = COL_GRID_MID
            else:
                color = COL_GRID
            self._line(buf, self.PAD_L, py, self.PAD_L + self.plot_w, py, color)

        # Vertical grid lines every 0.1 across x = 0..1.
        for i in range(11):
            nx = i / 10.0
            px, _ = self.to_px(nx, 0.0)
            if i in (0, 10):
                color = COL_UNIT
            elif i == 5:
                color = COL_GRID_MID
            else:
                color = COL_GRID
            self._line(buf, px, self.PAD_T, px, self.PAD_T + self.plot_h, color)

        # Dashed linear reference diagonal from (0,0) to (1,1).
        x0, y0 = self.to_px(0.0, 0.0)
        x1, y1 = self.to_px(1.0, 1.0)
        self._dashed_line(buf, x0, y0, x1, y1, COL_DIAG)
        return bytes(buf)

    # ---- frame rendering ----------------------------------------------------

    def render(self, p0, c1, c2, p3, selected):
        buf = bytearray(self._bg)

        # Curve.
        pts = curve_math.sample_curve(p0, c1, c2, p3, 260)
        prev = None
        for nx, ny in pts:
            px, py = self.to_px(nx, ny)
            if prev is not None:
                self._line(buf, prev[0], prev[1], px, py, COL_CURVE, radius=2)
            prev = (px, py)

        # Tangent handle lines.
        for a, b in ((p0, c1), (p3, c2)):
            ax, ay = self.to_px(a[0], a[1])
            bx, by = self.to_px(b[0], b[1])
            self._line(buf, ax, ay, bx, by, COL_HANDLE_LINE)

        # Knobs: index 0 = P0, 1 = C1, 2 = C2, 3 = P3.
        knobs = (p0, c1, c2, p3)
        for idx, (nx, ny) in enumerate(knobs):
            px, py = self.to_px(nx, ny)
            is_sel = (idx == selected)
            if idx in (0, 3):
                if is_sel:
                    self._square(buf, px, py, 10, COL_SELECTED)
                self._square(buf, px, py, 8, COL_ENDPOINT)
                self._square(buf, px, py, 3, COL_KNOB_CORE)
            else:
                if is_sel:
                    self._ring(buf, px, py, 9, 12, COL_SELECTED)
                self._disc(buf, px, py, 8, COL_HANDLE)
                self._disc(buf, px, py, 3, COL_KNOB_CORE)
        return self._encode_png(buf)

    def _encode_png(self, rgb):
        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data +
                    struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        stride = self.w * 3
        raw = bytearray()
        for y in range(self.h):
            raw.append(0)  # filter type: None
            raw += rgb[y * stride:(y + 1) * stride]
        return (b"\x89PNG\r\n\x1a\n" +
                chunk(b"IHDR", ihdr) +
                chunk(b"IDAT", zlib.compress(bytes(raw), 1)) +
                chunk(b"IEND", b""))


# --------------------------------------------------------------------------
# Main application.
# --------------------------------------------------------------------------

WIN_ID = "CLWin"

# Qt key codes.
KEY_LEFT = 16777234
KEY_UP = 16777235
KEY_RIGHT = 16777236
KEY_DOWN = 16777237
KEY_SHIFT = 16777248

NUDGE_SMALL = 0.01
NUDGE_BIG = 0.05

SPIN_IDS = ("CLP0X", "CLP0Y", "CLH1X", "CLH1Y", "CLH2X", "CLH2Y", "CLP3X", "CLP3Y")
POINT_NAMES = ("Start point", "Handle 1", "Handle 2", "End point")


class CurveLoupeApp(object):
    def __init__(self, fu_obj, bmd_obj):
        self.fu = fu_obj
        self.bmd = bmd_obj
        self.ui = fu_obj.UIManager
        self.disp = bmd_obj.UIDispatcher(self.ui)
        self.canvas = CurveCanvas()

        # Curve state (normalized space).
        self.p0y = 0.0
        self.p3y = 1.0
        self.c1 = [1.0 / 3.0, 1.0 / 3.0]
        self.c2 = [2.0 / 3.0, 2.0 / 3.0]
        self.selected = 1
        self.dragging = False
        self.shift_down = False
        self._updating_ui = False
        self._loading = False

        # Fusion connection state.
        self.anim_inputs = []      # list of dicts: ToolName, InputID, Label
        self.segment = None        # dict with ToolName, InputID, t0, v0, t1, v1, flat

        # Temp PNG bookkeeping (QIcon loads lazily, so keep a few frames
        # around before deleting).
        self._tmpdir = tempfile.gettempdir()
        self._frame = 0
        self._png_paths = []

        self.win = None
        self.itm = None

    # ---------------------------------------------------------------- UI ----

    def build_window(self):
        ui = self.ui

        def spin(spin_id, lo, hi, value, enabled=True):
            return ui.DoubleSpinBox({
                "ID": spin_id,
                "Minimum": lo,
                "Maximum": hi,
                "Value": value,
                "SingleStep": 0.01,
                "Decimals": 3,
                "Enabled": enabled,
                "Events": {"ValueChanged": True},
            })

        def point_row(label, x_id, y_id, x_val, y_val, x_enabled):
            return ui.HGroup({"Spacing": 6}, [
                ui.Label({"Text": label, "Weight": 0.6}),
                ui.Label({"Text": "X", "Weight": 0}),
                spin(x_id, 0.0, 1.0, x_val, x_enabled),
                ui.Label({"Text": "Y", "Weight": 0}),
                spin(y_id, -3.0, 3.0, y_val),
            ])

        preset_rows = []
        for r, row in enumerate(presets.PRESET_ROWS):
            buttons = []
            for c, (label, _c1, _c2) in enumerate(row):
                buttons.append(ui.Button({
                    "ID": "CLPreset_%d_%d" % (r, c),
                    "Text": label,
                }))
            preset_rows.append(ui.HGroup({"Spacing": 4}, buttons))

        self.win = self.disp.AddWindow({
            "ID": WIN_ID,
            "WindowTitle": "CurveLoupe",
            "Geometry": [100, 60, 1060, 740],
            "Events": {"Close": True, "KeyPress": True, "KeyRelease": True},
        }, [
            ui.HGroup({"Spacing": 10}, [
                # ---- left: canvas ----
                ui.VGroup({"Spacing": 4, "Weight": 0}, [
                    ui.Button({
                        "ID": "CLCanvas",
                        "Text": "",
                        "Flat": True,
                        "IconSize": [CANVAS_W, CANVAS_H],
                        "MinimumSize": [CANVAS_W, CANVAS_H],
                        "Events": {
                            "Clicked": True,
                            "MousePress": True,
                            "MouseMove": True,
                            "MouseRelease": True,
                        },
                    }),
                    ui.Label({
                        "ID": "CLReadout",
                        "Text": "",
                        "Alignment": {"AlignHCenter": True},
                    }),
                ]),
                # ---- right: controls ----
                ui.VGroup({"Spacing": 6, "Weight": 1}, [
                    ui.Label({"Text": "<b>Fusion connection</b>"}),
                    ui.HGroup({"Spacing": 6}, [
                        ui.ComboBox({"ID": "CLInput", "Weight": 1,
                                     "Events": {"CurrentIndexChanged": True}}),
                        ui.Button({"ID": "CLRefresh", "Text": "Refresh", "Weight": 0}),
                    ]),
                    ui.HGroup({"Spacing": 6}, [
                        ui.Label({"Text": "Segment", "Weight": 0}),
                        ui.SpinBox({"ID": "CLSeg", "Minimum": 1, "Maximum": 1,
                                    "Value": 1, "Weight": 0,
                                    "Events": {"ValueChanged": True}}),
                        ui.Label({"ID": "CLSegInfo", "Text": "-", "Weight": 1}),
                    ]),
                    ui.HGroup({"Spacing": 6}, [
                        ui.Button({"ID": "CLLoad", "Text": "Load from Selected"}),
                        ui.Button({"ID": "CLApply", "Text": "Apply to Fusion"}),
                    ]),
                    ui.Label({"ID": "CLStatus", "Text": "", "WordWrap": True,
                              "MinimumSize": [100, 44]}),
                    ui.VGap(6),
                    ui.Label({"Text": "<b>Points</b>  (drag on canvas, or edit numerically)"}),
                    point_row("Start point", "CLP0X", "CLP0Y", 0.0, 0.0, False),
                    point_row("Handle 1", "CLH1X", "CLH1Y", self.c1[0], self.c1[1], True),
                    point_row("Handle 2", "CLH2X", "CLH2Y", self.c2[0], self.c2[1], True),
                    point_row("End point", "CLP3X", "CLP3Y", 1.0, 1.0, False),
                    ui.Label({
                        "Text": "Arrow keys nudge the selected point by 0.01, "
                                "Shift+Arrow by 0.05. Click a point to select it.",
                        "WordWrap": True,
                    }),
                    ui.VGap(6),
                    ui.Label({"Text": "<b>Presets</b>"}),
                ] + preset_rows + [
                    ui.VGap(0, 1),
                    ui.Label({
                        "Text": '<span style="color:#7a7a74;">CurveLoupe v1.0</span>',
                        "Alignment": {"AlignHCenter": True},
                    }),
                ]),
            ]),
        ])
        self.itm = self.win.GetItems()
        self._wire_events()

    def _wire_events(self):
        on = self.win.On

        on[WIN_ID].Close = self._on_close
        on[WIN_ID].KeyPress = self._on_key_press
        on[WIN_ID].KeyRelease = self._on_key_release

        on.CLCanvas.MousePress = self._on_mouse_press
        on.CLCanvas.MouseMove = self._on_mouse_move
        on.CLCanvas.MouseRelease = self._on_mouse_release

        on.CLRefresh.Clicked = lambda ev: self.refresh_inputs()
        on.CLLoad.Clicked = lambda ev: self.load_from_selected()
        on.CLApply.Clicked = lambda ev: self.apply_to_fusion()
        on.CLInput.CurrentIndexChanged = self._on_input_changed
        on.CLSeg.ValueChanged = self._on_segment_changed

        for sid in SPIN_IDS:
            on[sid].ValueChanged = self._on_spin_changed

        for r, row in enumerate(presets.PRESET_ROWS):
            for c, (_label, c1, c2) in enumerate(row):
                on["CLPreset_%d_%d" % (r, c)].Clicked = self._make_preset_handler(c1, c2)

    def _make_preset_handler(self, c1, c2):
        def handler(ev):
            self.p0y = 0.0
            self.p3y = 1.0
            self.c1 = [c1[0], c1[1]]
            self.c2 = [c2[0], c2[1]]
            self.redraw()
            self.set_status("Preset loaded. Click 'Apply to Fusion' to write it back.",
                            "info")
        return handler

    # ------------------------------------------------------------- status ----

    def set_status(self, text, kind="info"):
        colors = {"info": "#b8b8b8", "ok": "#8fd18a", "error": "#ff7a6b"}
        self.itm["CLStatus"].Text = ('<span style="color:%s;">%s</span>'
                                     % (colors.get(kind, "#b8b8b8"), text))

    # ------------------------------------------------------------- redraw ----

    def points(self):
        return [(0.0, self.p0y), tuple(self.c1), tuple(self.c2), (1.0, self.p3y)]

    def redraw(self, sync_spins=True):
        p0, c1, c2, p3 = self.points()
        png = self.canvas.render(p0, c1, c2, p3, self.selected)

        self._frame += 1
        path = os.path.join(self._tmpdir, "curveloupe_%06d.png" % self._frame)
        try:
            with open(path, "wb") as f:
                f.write(png)
            self.itm["CLCanvas"].Icon = self.ui.Icon({"File": path})
        except Exception:
            traceback.print_exc()
            return
        self._png_paths.append(path)
        while len(self._png_paths) > 4:
            old = self._png_paths.pop(0)
            try:
                os.remove(old)
            except OSError:
                pass

        if sync_spins:
            self._sync_spins()
        self._sync_readout()

    def _sync_spins(self):
        self._updating_ui = True
        try:
            vals = (0.0, self.p0y, self.c1[0], self.c1[1],
                    self.c2[0], self.c2[1], 1.0, self.p3y)
            for sid, val in zip(SPIN_IDS, vals):
                self.itm[sid].Value = val
        finally:
            self._updating_ui = False

    def _sync_readout(self):
        if self.selected is None:
            self.itm["CLReadout"].Text = "x: time 0-1 of segment / y: value 0-1 of segment"
            return
        px, py = self.points()[self.selected]
        self.itm["CLReadout"].Text = ("%s   x = %.3f   y = %.3f"
                                      % (POINT_NAMES[self.selected], px, py))

    # ---------------------------------------------------------- mouse/keys ----

    def _hit_test(self, mx, my):
        pts = self.points()
        best = None
        best_d2 = HIT_RADIUS * HIT_RADIUS
        # Handles get priority over endpoints when overlapping.
        for idx in (1, 2, 0, 3):
            px, py = self.canvas.to_px(pts[idx][0], pts[idx][1])
            d2 = (px - mx) ** 2 + (py - my) ** 2
            if d2 <= best_d2:
                best = idx
                best_d2 = d2
        return best

    def _on_mouse_press(self, ev):
        pos = event_pos(ev)
        if pos is None:
            return
        hit = self._hit_test(pos[0], pos[1])
        if hit is not None:
            self.selected = hit
            self.dragging = True
            self._drag_to(pos[0], pos[1])
        else:
            self.redraw()

    def _on_mouse_move(self, ev):
        if not self.dragging:
            return
        pos = event_pos(ev)
        if pos is not None:
            self._drag_to(pos[0], pos[1])

    def _on_mouse_release(self, ev):
        self.dragging = False

    def _drag_to(self, mx, my):
        nx, ny = self.canvas.to_norm(mx, my)
        self._move_selected(nx, ny)
        self.redraw()

    def _move_selected(self, nx, ny):
        ny = curve_math.clamp(ny, self.canvas.Y_MIN, self.canvas.Y_MAX)
        nx = curve_math.clamp(nx, 0.0, 1.0)
        if self.selected == 0:
            self.p0y = ny
        elif self.selected == 3:
            self.p3y = ny
        elif self.selected == 1:
            self.c1 = [nx, ny]
        elif self.selected == 2:
            self.c2 = [nx, ny]

    def _on_key_press(self, ev):
        key = int(ev.get("Key", 0)) if isinstance(ev, dict) else 0
        if key == KEY_SHIFT:
            self.shift_down = True
            return
        if self.selected is None:
            return
        step = NUDGE_BIG if self.shift_down else NUDGE_SMALL
        dx = dy = 0.0
        if key == KEY_LEFT:
            dx = -step
        elif key == KEY_RIGHT:
            dx = step
        elif key == KEY_UP:
            dy = step
        elif key == KEY_DOWN:
            dy = -step
        else:
            return
        px, py = self.points()[self.selected]
        self._move_selected(px + dx, py + dy)
        self.redraw()

    def _on_key_release(self, ev):
        key = int(ev.get("Key", 0)) if isinstance(ev, dict) else 0
        if key == KEY_SHIFT:
            self.shift_down = False

    def _on_spin_changed(self, ev):
        if self._updating_ui:
            return
        self.p0y = float(self.itm["CLP0Y"].Value)
        self.p3y = float(self.itm["CLP3Y"].Value)
        self.c1 = [float(self.itm["CLH1X"].Value), float(self.itm["CLH1Y"].Value)]
        self.c2 = [float(self.itm["CLH2X"].Value), float(self.itm["CLH2Y"].Value)]
        self.redraw(sync_spins=False)

    # ------------------------------------------------------------- fusion ----

    def _get_comp(self):
        comp = self.fu.GetCurrentComp()
        if comp is None:
            self.set_status("No composition is open. Open the Fusion page first.", "error")
        return comp

    def _get_selected_tool(self, comp):
        tool = None
        try:
            tool = comp.ActiveTool
        except Exception:
            tool = None
        if tool is None:
            try:
                sel = comp.GetToolList(True)
                if sel:
                    tool = sel[sorted(sel.keys())[0]]
            except Exception:
                tool = None
        if tool is None:
            self.set_status("Select a tool with an animated (spline) control in Fusion "
                            "first, then click Refresh.", "error")
        return tool

    @staticmethod
    def _find_spline(inp):
        """Return the BezierSpline modifier feeding this input, or None."""
        try:
            out = inp.GetConnectedOutput()
            if out is None:
                return None
            mod = out.GetTool()
            if mod is None:
                return None
            attrs = mod.GetAttrs()
            reg_id = (attrs or {}).get("TOOLS_RegID", "")
            if reg_id == "BezierSpline":
                return mod
        except Exception:
            pass
        return None

    def refresh_inputs(self):
        self.anim_inputs = []
        combo = self.itm["CLInput"]
        combo.Clear()

        comp = self._get_comp()
        if comp is None:
            return False
        tool = self._get_selected_tool(comp)
        if tool is None:
            return False

        tool_name = tool.Name
        try:
            input_list = tool.GetInputList()
        except Exception:
            input_list = None
        if not input_list:
            self.set_status("Tool '%s' has no inputs." % tool_name, "error")
            return False

        for key in sorted(input_list.keys()):
            inp = input_list[key]
            spline = self._find_spline(inp)
            if spline is None:
                continue
            try:
                disp_name = inp.GetAttrs().get("INPS_Name", str(inp.ID))
                input_id = str(inp.ID)
            except Exception:
                continue
            self.anim_inputs.append({
                "ToolName": tool_name,
                "InputID": input_id,
                "Label": "%s . %s" % (tool_name, disp_name),
            })

        if not self.anim_inputs:
            self.set_status("Tool '%s' has no BezierSpline-animated controls. "
                            "Animate a control (right-click > Animate) and refresh."
                            % tool_name, "error")
            return False

        for entry in self.anim_inputs:
            combo.AddItem(entry["Label"])
        self.set_status("Found %d animated control(s) on '%s'."
                        % (len(self.anim_inputs), tool_name), "ok")
        return True

    def _resolve_spline(self, tool_name, input_id):
        """Re-find the spline for (tool, input) so stale references never crash."""
        comp = self._get_comp()
        if comp is None:
            return None, None
        tool = None
        try:
            tool = comp.FindTool(tool_name)
        except Exception:
            tool = None
        if tool is None:
            self.set_status("Tool '%s' no longer exists in the comp." % tool_name, "error")
            return None, None
        try:
            for key, inp in tool.GetInputList().items():
                if str(inp.ID) == input_id:
                    spline = self._find_spline(inp)
                    if spline is None:
                        break
                    return comp, spline
        except Exception:
            pass
        self.set_status("Control '%s' on '%s' is no longer animated with a BezierSpline."
                        % (input_id, tool_name), "error")
        return None, None

    def _read_keyframes(self, spline):
        """Return sorted [(time, subtable), ...] from a BezierSpline modifier."""
        kf = spline.GetKeyFrames()
        if not kf:
            return []
        times = sorted(float(t) for t in kf.keys())
        out = []
        for t in times:
            sub = None
            for key in (t, int(t)):
                if key in kf:
                    sub = kf[key]
                    break
            if sub is None:
                for key in kf.keys():
                    if abs(float(key) - t) < 1e-9:
                        sub = kf[key]
                        break
            out.append((t, sub))
        return out

    def _current_selection(self):
        idx = int(self.itm["CLInput"].CurrentIndex)
        if idx < 0 or idx >= len(self.anim_inputs):
            return None
        return self.anim_inputs[idx]

    def _on_input_changed(self, ev):
        if self._updating_ui or self._loading:
            return
        if self.anim_inputs:
            self.load_from_selected(refresh=False)

    def _on_segment_changed(self, ev):
        if self._updating_ui or self._loading:
            return
        if self.segment is not None or self.anim_inputs:
            self.load_from_selected(refresh=False)

    def load_from_selected(self, refresh=True):
        if self._loading:
            return
        self._loading = True
        try:
            self._load_from_selected(refresh)
        except Exception as e:
            traceback.print_exc()
            self.set_status("Load failed: %s" % e, "error")
        finally:
            self._loading = False

    def _load_from_selected(self, refresh=True):
        if refresh or not self.anim_inputs:
            if not self.refresh_inputs():
                return

        entry = self._current_selection()
        if entry is None:
            self.set_status("Pick an animated control from the dropdown first.", "error")
            return

        comp, spline = self._resolve_spline(entry["ToolName"], entry["InputID"])
        if spline is None:
            return

        keys = self._read_keyframes(spline)
        if len(keys) < 2:
            self.set_status("'%s' has fewer than 2 keyframes - nothing to shape. "
                            "Add at least two keyframes in Fusion." % entry["Label"],
                            "error")
            return

        n_segments = len(keys) - 1
        self._updating_ui = True
        try:
            self.itm["CLSeg"].Maximum = n_segments
            if int(self.itm["CLSeg"].Value) > n_segments:
                self.itm["CLSeg"].Value = 1
        finally:
            self._updating_ui = False
        seg_idx = int(self.itm["CLSeg"].Value) - 1
        seg_idx = max(0, min(seg_idx, n_segments - 1))

        t0, sub0 = keys[seg_idx]
        t1, sub1 = keys[seg_idx + 1]
        v0 = float(lua_get(sub0, 1))
        v1 = float(lua_get(sub1, 1))
        rh = lua_pair(sub0.get("RH")) if isinstance(sub0, dict) else None
        lh = lua_pair(sub1.get("LH")) if isinstance(sub1, dict) else None

        c1, c2, flat = curve_math.segment_to_normalized(t0, v0, t1, v1, rh, lh)
        self.p0y = 0.0
        self.p3y = 1.0
        self.c1 = [curve_math.clamp(c1[0], 0.0, 1.0),
                   curve_math.clamp(c1[1], self.canvas.Y_MIN, self.canvas.Y_MAX)]
        self.c2 = [curve_math.clamp(c2[0], 0.0, 1.0),
                   curve_math.clamp(c2[1], self.canvas.Y_MIN, self.canvas.Y_MAX)]
        self.segment = {
            "ToolName": entry["ToolName"],
            "InputID": entry["InputID"],
            "Label": entry["Label"],
            "t0": t0, "v0": v0, "t1": t1, "v1": v1,
            "flat": flat,
        }
        self.itm["CLSegInfo"].Text = ("of %d   frames %g - %g   values %g - %g"
                                      % (n_segments, t0, t1, v0, v1))
        self.redraw()
        note = " (flat segment: values are equal, Y edits become raw value offsets)" if flat else ""
        self.set_status("Loaded segment %d of '%s'.%s"
                        % (seg_idx + 1, entry["Label"], note), "ok")

    # ---- write back ----------------------------------------------------------

    def apply_to_fusion(self):
        try:
            self._apply_to_fusion()
        except Exception as e:
            traceback.print_exc()
            self.set_status("Apply failed: %s" % e, "error")

    def _apply_to_fusion(self):
        if self.segment is None:
            self.set_status("Nothing loaded yet - click 'Load from Selected' first "
                            "(select a tool with an animated control in Fusion).",
                            "error")
            return

        seg = self.segment
        comp, spline = self._resolve_spline(seg["ToolName"], seg["InputID"])
        if spline is None:
            return

        keys = self._read_keyframes(spline)
        times = [t for t, _ in keys]
        eps_t = 1e-6

        def find_time(target):
            for t in times:
                if abs(t - target) < eps_t:
                    return t
            return None

        t0 = find_time(seg["t0"])
        t1 = find_time(seg["t1"])
        if t0 is None or t1 is None:
            self.set_status("The loaded keyframes moved or were deleted - "
                            "click 'Load from Selected' again.", "error")
            return

        new_v0, new_v1, rh_abs, lh_abs = curve_math.normalized_to_segment(
            seg["t0"], seg["v0"], seg["t1"], seg["v1"],
            self.p0y, tuple(self.c1), tuple(self.c2), self.p3y)

        # Build the full keyframe table, patching our two keys.
        def build_table(relative):
            table = {}
            for t, sub in keys:
                val = float(lua_get(sub, 1))
                entry = {}
                lh = lua_pair(sub.get("LH")) if isinstance(sub, dict) else None
                rh = lua_pair(sub.get("RH")) if isinstance(sub, dict) else None
                flags = sub.get("Flags") if isinstance(sub, dict) else None

                if abs(t - t0) < eps_t:
                    val = new_v0
                    rh = rh_abs
                    flags = None    # a Linear/Step flag would override our handles
                elif abs(t - t1) < eps_t:
                    val = new_v1
                    lh = lh_abs
                    flags = None

                entry[1] = val
                if lh is not None:
                    entry["LH"] = ([lh[0] - t, lh[1] - val] if relative
                                   else [lh[0], lh[1]])
                if rh is not None:
                    entry["RH"] = ([rh[0] - t, rh[1] - val] if relative
                                   else [rh[0], rh[1]])
                if flags:
                    entry["Flags"] = dict(flags)
                table[t] = entry
            return table

        def verify():
            """Read back and check the right handle of the left key landed
            where we expect (in absolute coordinates)."""
            check = self._read_keyframes(spline)
            for t, sub in check:
                if abs(t - t0) < eps_t and isinstance(sub, dict):
                    rh = lua_pair(sub.get("RH"))
                    if rh is None:
                        return False
                    tol = max(1e-3, abs(seg["t1"] - seg["t0"]) * 0.01)
                    return abs(rh[0] - rh_abs[0]) < tol
            return False

        # GetKeyFrames() reports absolute handle positions, but modern
        # Resolve builds expect SetKeyFrames() handles as offsets relative to
        # their keyframe (older Fusion used absolute). Try relative first,
        # verify by reading back, and fall back to absolute.
        try:
            comp.StartUndo("CurveLoupe: shape curve")
        except Exception:
            pass
        ok = False
        try:
            spline.SetKeyFrames(build_table(relative=True), True)
            ok = verify()
            if not ok:
                spline.SetKeyFrames(build_table(relative=False), True)
                ok = verify()
        finally:
            try:
                comp.EndUndo(True)
            except Exception:
                pass

        if ok:
            self.set_status("Applied to '%s' (frames %g - %g). Check Fusion's "
                            "spline editor - it updates instantly."
                            % (seg["Label"], seg["t0"], seg["t1"]), "ok")
        else:
            self.set_status("Wrote keyframes but could not verify the handle "
                            "positions - please check the spline in Fusion.",
                            "error")

    # --------------------------------------------------------------- close ----

    def _on_close(self, ev):
        self.disp.ExitLoop()

    def _cleanup(self):
        for path in self._png_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    # ---------------------------------------------------------------- run ----

    def run(self):
        self.build_window()
        self.selected = 1
        self.redraw()

        # Try to pull the current selection straight away; failures just
        # leave a hint in the status line.
        self._loading = True
        try:
            self._load_from_selected(refresh=True)
        except Exception:
            self.set_status("Select a tool with an animated control in Fusion, "
                            "then click 'Load from Selected'.", "info")
        finally:
            self._loading = False

        self.win.Show()
        self.disp.RunLoop()
        self.win.Hide()
        self._cleanup()


# --------------------------------------------------------------------------


def main():
    try:
        fu_obj, bmd_obj = _get_fusion_env()
    except Exception as e:
        traceback.print_exc()
        print("CurveLoupe: could not connect to Fusion/Resolve: %s" % e)
        return

    if _IMPORT_ERROR is not None:
        # Show the problem in a minimal native window instead of dying silently.
        ui = fu_obj.UIManager
        disp = bmd_obj.UIDispatcher(ui)
        win = disp.AddWindow({
            "ID": "CLErrWin",
            "WindowTitle": "CurveLoupe - install problem",
            "Geometry": [200, 200, 560, 160],
            "Events": {"Close": True},
        }, [
            ui.VGroup({"Spacing": 8}, [
                ui.Label({
                    "Text": "CurveLoupe could not import its helper modules "
                            "(curve_math.py / presets.py):\n%s\n\n"
                            "Copy the WHOLE 'curveloupe' folder into Fusion's "
                            "Scripts:/Comp folder (see README.md)." % _IMPORT_ERROR,
                    "WordWrap": True,
                }),
            ]),
        ])
        win.On.CLErrWin.Close = lambda ev: disp.ExitLoop()
        win.Show()
        disp.RunLoop()
        win.Hide()
        return

    app = CurveLoupeApp(fu_obj, bmd_obj)
    app.run()


main()
