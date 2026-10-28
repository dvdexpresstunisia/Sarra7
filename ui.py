from __future__ import annotations

import importlib.util
import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping JARVIS from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter, QCheckBox,
    QGraphicsScene, QGraphicsView,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None

from core.avatar_paths import avatar_config_path

# Avatar 3D Sarra (Three.js via QtWebEngine). Optionnel : toute failure ici
# désactive la 3D et conserve l'avatar 2D, sans jamais empêcher le démarrage.
try:
    from core.avatar_bridge import AvatarBridge
    HAVE_SARRA = True
except Exception as _e:  # noqa: BLE001 - reported, never fatal
    AvatarBridge = None  # type: ignore
    HAVE_SARRA = False
    print(f"[Sarra] pont 3D indisponible ({_e}) — repli avatar 2D.")

# --- Avatar Sarra : utiliser la vue 3D VRM quand QtWebEngine est disponible. ---
# Le fichier principal est assets/avatar/sarra.vrm ; AvatarBridge conserve le
# repli 2D si QtWebEngine ou le chargement WebGL n'est pas disponible.
SARRA_3D = True


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"
# Floating-avatar window geometry (size + desktop position). Local state,
# never committed: restored on the next start so the avatar stays where the
# user put it.
AVATAR_WINDOW_FILE = avatar_config_path("avatar_window.json")
AVATAR_WINDOW_DEFAULT_FILE = (
    Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "config" / "avatar_window.json"
)

_AVATAR_WIN_MIN = 400
_AVATAR_WIN_DEFAULT = (520, 620)
_FIRST_STARTUP_AVATAR_SCALE = 0.85


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        config = json.loads(API_FILE.read_text(encoding="utf-8"))
        return config if isinstance(config, dict) else {}
    except Exception:
        return {}


# ── Core directives (official system prompt) ────────────────────────────────
# `core/prompt.txt` is the file main.py loads for the Live session. The root
# `core directives.txt` is only a human-readable mirror kept in sync so the two
# can never disagree. Both editors in the settings panel write through these
# helpers, so whatever is saved there is exactly what the assistant receives.
CORE_DIRECTIVES_FILE   = BASE_DIR / "core" / "prompt.txt"
CORE_DIRECTIVES_BACKUP = BASE_DIR / "core" / "prompt.txt.bak"
CORE_DIRECTIVES_MIRROR = BASE_DIR / "core directives.txt"
CORE_DIRECTIVES_TOKENS = ("{assistant_name}", "{platform}")


def _read_core_directives() -> str:
    """Return the official directives, or an empty string when unreadable."""
    try:
        return CORE_DIRECTIVES_FILE.read_text(encoding="utf-8")
    except Exception:
        return ""


def _missing_core_tokens(text: str) -> list[str]:
    return [tok for tok in CORE_DIRECTIVES_TOKENS if tok not in (text or "")]


def _atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text through a temp file so a crash cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _normalise_core_directives(text: str) -> str:
    """One newline convention for both display and disk comparisons."""
    normalised = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return normalised.rstrip("\n") + "\n"


def _write_core_directives(text: str) -> str:
    """Persist edited directives as the official prompt.

    Returns the normalised text. Raises ValueError (message shown in the
    panel) when the content is empty or has lost a required runtime token.
    The previous prompt is copied to prompt.txt.bak before being replaced.
    """
    normalised = _normalise_core_directives(text)
    if not normalised.strip():
        raise ValueError("les directives sont vides")
    missing = _missing_core_tokens(normalised)
    if missing:
        raise ValueError("gardez les jetons " + ", ".join(missing))

    old = _read_core_directives()
    if old and old != normalised:
        _atomic_write_text(CORE_DIRECTIVES_BACKUP, old)

    _atomic_write_text(CORE_DIRECTIVES_FILE, normalised)
    try:
        _atomic_write_text(CORE_DIRECTIVES_MIRROR, normalised)
    except Exception as e:  # pragma: no cover — the official file is already saved
        print(f"[Directives] mirror not updated: {e}")
    return normalised


def _default_avatar_window_geometry() -> QRect:
    """Centered 520×620 avatar window, clamped to the primary screen."""
    screen = QApplication.primaryScreen()
    if screen is None:
        w, h = _AVATAR_WIN_DEFAULT
        return QRect(0, 0, w, h)
    avail = screen.availableGeometry()
    w = min(_AVATAR_WIN_DEFAULT[0], avail.width())
    h = min(_AVATAR_WIN_DEFAULT[1], avail.height())
    return QRect(
        avail.x() + (avail.width() - w) // 2,
        avail.y() + (avail.height() - h) // 2,
        w, h,
    )


def _first_startup_avatar_geometry(geometry: QRect) -> QRect:
    """Reduce the initial setup avatar to 85% while keeping it centered."""
    scaled = QRect(geometry)
    scaled.setSize(QSize(
        round(geometry.width() * _FIRST_STARTUP_AVATAR_SCALE),
        round(geometry.height() * _FIRST_STARTUP_AVATAR_SCALE),
    ))
    scaled.moveCenter(geometry.center())
    return scaled


def _center_avatar_rect_on_screen(rect: QRect, avail: QRect) -> QRect:
    """Move `rect` just enough for its centre to sit on `avail`."""
    centre = rect.center()
    cx = min(max(centre.x(), avail.left()), avail.right())
    cy = min(max(centre.y(), avail.top()), avail.bottom())
    return rect.translated(cx - centre.x(), cy - centre.y())


def _load_avatar_window_geometry() -> QRect:
    """Read the saved avatar-only geometry, or a centered default.

    The user's exact placement is preserved, including a window that hangs
    past a desktop edge, as long as the avatar's centre is still on a screen.
    A window left on a disconnected monitor is brought back just enough for
    the avatar (and the drag surface around it) to be reachable again.
    """
    data = None
    for path in dict.fromkeys((AVATAR_WINDOW_FILE, AVATAR_WINDOW_DEFAULT_FILE)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                break
        except (OSError, ValueError):
            continue

    try:
        if not isinstance(data, dict):
            return _default_avatar_window_geometry()
        x = int(data["x"])
        y = int(data["y"])
        w = int(data["width"])
        h = int(data["height"])
    except Exception:
        return _default_avatar_window_geometry()

    if w < _AVATAR_WIN_MIN or h < _AVATAR_WIN_MIN:
        return _default_avatar_window_geometry()

    rect = QRect(x, y, w, h)
    # Exact restore whenever the avatar's centre is on a monitor, even if the
    # transparent edges of the window extend past the desktop boundary.
    for screen in QApplication.screens():
        if screen.availableGeometry().contains(rect.center()):
            return rect

    # The centre is off-screen (monitor unplugged, resolution changed). Pick
    # the screen showing the largest part of the window and pull the centre
    # back onto it; then the avatar can be grabbed and moved again.
    best_avail = None
    best_area = 0
    for screen in QApplication.screens():
        avail = screen.availableGeometry()
        visible = avail.intersected(rect)
        area = visible.width() * visible.height()
        if area > best_area:
            best_area = area
            best_avail = avail
    if best_avail is not None and best_area >= 80 * 80:
        return _center_avatar_rect_on_screen(rect, best_avail)

    return _default_avatar_window_geometry()


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "MARK LV"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#00000000"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]

# The application starts in the light theme when no preference has been saved.
# Keep the existing palette as the dark theme so existing custom colours and
# accent previews continue to work as before.
THEMES = ("light", "dark")
DEFAULT_THEME = "light"
_LIGHT_PALETTE = {
    "BG": "#f4f8fb",
    "PANEL": "#ffffff",
    "PANEL2": "#edf4f8",
    "BORDER": "#bfd2dc",
    "BORDER_B": "#6f9caf",
    "BORDER_A": "#9dbdca",
    "PRI": "#087c9e",
    "PRI_DIM": "#24677b",
    "PRI_GHO": "#d9f0f6",
    "TEXT": "#123746",
    "TEXT_DIM": "#66818c",
    "TEXT_MED": "#376878",
    "WHITE": "#102b36",
    "DARK": "#e7f0f4",
    "BAR_BG": "#dceaf0",
}


def apply_ui_theme(theme: str) -> bool:
    """Apply the named base theme to the shared palette."""
    theme = (theme or DEFAULT_THEME).strip().lower()
    if theme not in THEMES:
        theme = DEFAULT_THEME
    values = _LIGHT_PALETTE if theme == "light" else _PALETTE_DEFAULTS
    for key in _HUE_LINKED:
        setattr(C, key, values[key])
    return True


def current_theme() -> str:
    """Return the current theme based on the panel background colour."""
    return "light" if C.PANEL == _LIGHT_PALETTE["PANEL"] else "dark"


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_palette      = _LIGHT_PALETTE if current_theme() == "light" else _PALETTE_DEFAULTS
    base_h            = _hsv(base_palette["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in base_palette.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        # Le canvas contient uniquement l'avatar et les éléments holographiques.
        # Ne pas le déclarer opaque : le HUD situé derrière doit rester visible
        # dans les zones non peintes et dans les pixels alpha du fond.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i][:3]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))

        if self._avatar is not None and self.hud_style == "face":
            self._avatar.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
        #    came from a few big strokes. Speed is the state: idle drifts,
        #    thinking hurries, speaking runs.
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        # 9. The name, sized from the string rather than from the radius alone:
        #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Le canvas doit laisser voir le bureau/la fenêtre derrière l'avatar.
        # C.BG dépend du thème (le thème clair est blanc) et ne doit donc pas
        # être utilisé pour effacer le canvas : il produirait un rectangle
        # opaque malgré WA_TranslucentBackground.
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        if self._avatar is not None and self.hud_style == "face":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.355, _band_h / (self._avatar.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - self._avatar.SPAN * _r_head) / 2.0 + _r_head

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            self._avatar.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))

        # reactor core — the other centrepiece, and the fallback if the head
        # could not be built. There is no third path: the old face.png branch
        # was unreachable (no such file ships) and the bare orb it fell through
        # to is what this replaces.
        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)

        # status text
        sy = _sy_status
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # JARVIS's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None, require_new_key: bool = False):
        super().__init__(parent)
        config = _read_full_config()
        stored_key = config.get("gemini_api_key", "")
        self._stored_key = stored_key.strip() if isinstance(stored_key, str) else ""
        self._require_new_key = require_new_key
        self._initializing = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
            }}
        """)

        detected = {
            "Windows": "windows",
            "Darwin": "mac",
            "Linux": "linux",
        }.get(_OS)
        self._sel_os = detected

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        identity = QFrame()
        identity.setFixedWidth(245)
        identity.setStyleSheet(
            f"background: #00131d; border: none; "
            f"border-right: 1px solid {C.BORDER};"
        )
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(25, 30, 25, 25)
        identity_layout.setSpacing(10)

        emblem = QLabel("◈")
        emblem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emblem.setFixedSize(52, 52)
        emblem.setFont(QFont("Segoe UI", 26, QFont.Weight.Light))
        emblem.setStyleSheet(
            f"color: {C.PRI}; background: #002432; "
            f"border: 1px solid {C.PRI_DIM}; border-radius: 26px;"
        )
        identity_layout.addWidget(emblem, alignment=Qt.AlignmentFlag.AlignLeft)

        name = QLabel("SARRA")
        name.setFont(QFont("Segoe UI", 25, QFont.Weight.Bold))
        name.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 3px;")
        identity_layout.addWidget(name)

        subtitle = QLabel("PERSONAL AI ASSISTANT")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        subtitle.setStyleSheet(f"color: {C.PRI}; letter-spacing: 1px;")
        identity_layout.addWidget(subtitle)
        identity_layout.addSpacing(15)

        self._identity_status = QLabel(
            "●  CLÉ À REMPLACER"
            if require_new_key else
            "●  CONFIGURATION ENREGISTRÉE"
            if self._stored_key else
            "●  CONFIGURATION REQUISE"
        )
        self._identity_status.setWordWrap(True)
        self._identity_status.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        self._identity_status.setStyleSheet(
            f"color: {C.ACC2}; background: #171707; "
            f"border: 1px solid #554b19; border-radius: 4px; padding: 8px;"
        )
        identity_layout.addWidget(self._identity_status)
        identity_layout.addStretch(1)

        identity_note = QLabel(
            "Votre avatar Sarra utilise le modèle VRM déjà installé. "
            "Il reste dans sa fenêtre habituelle pendant cette configuration."
        )
        identity_note.setWordWrap(True)
        identity_note.setFont(QFont("Segoe UI", 9))
        identity_note.setStyleSheet(f"color: {C.TEXT_MED}; line-height: 1.4;")
        identity_layout.addWidget(identity_note)
        identity_layout.addSpacing(10)

        signature = QLabel("SARRA  /  INITIAL SETUP")
        signature.setFont(QFont("Consolas", 7))
        signature.setStyleSheet(f"color: {C.TEXT_DIM};")
        identity_layout.addWidget(signature)
        layout.addWidget(identity)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 24)
        content_layout.setSpacing(10)
        header = QLabel(
            "CONFIGURATION GEMINI"
            if self._stored_key else
            "PREMIÈRE INITIALISATION"
        )
        header.setFont(QFont("Segoe UI", 16, QFont.Weight.DemiBold))
        header.setStyleSheet(f"color: {C.WHITE};")
        content_layout.addWidget(header)
        intro = QLabel(
            "Configurez Gemini et vérifiez les paramètres de votre système."
        )
        intro.setWordWrap(True)
        intro.setFont(QFont("Segoe UI", 9))
        intro.setStyleSheet(f"color: {C.TEXT_MED};")
        content_layout.addWidget(intro)
        content_layout.addSpacing(4)

        self._steps: dict[str, QLabel] = {}

        def add_step(number: str, title: str, detail: str, status: str):
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background: {C.PANEL}; "
                f"border: 1px solid {C.BORDER}; border-radius: 5px; }}"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            card_layout.setSpacing(3)
            line = QHBoxLayout()
            line.setSpacing(8)
            number_label = QLabel(number)
            number_label.setFixedWidth(22)
            number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number_label.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            number_label.setStyleSheet(
                f"color: {C.PRI}; background: {C.PRI_GHO}; "
                "border-radius: 11px; padding: 3px;"
            )
            title_label = QLabel(title)
            title_label.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            title_label.setStyleSheet(f"color: {C.WHITE};")
            status_label = QLabel(status)
            status_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            status_label.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
            status_label.setStyleSheet(f"color: {C.TEXT_DIM};")
            line.addWidget(number_label)
            line.addWidget(title_label, stretch=1)
            line.addWidget(status_label)
            card_layout.addLayout(line)
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setFont(QFont("Segoe UI", 8))
            detail_label.setStyleSheet(f"color: {C.TEXT_MED};")
            card_layout.addWidget(detail_label)
            content_layout.addWidget(card)
            self._steps[number] = status_label
            return card_layout, detail_label

        key_card, self._key_detail = add_step(
            "01", "Clé API Gemini",
            "La clé reste masquée et est conservée dans la configuration locale.",
            (
                "À REMPLACER" if require_new_key
                else "ENREGISTRÉE" if self._stored_key
                else "À CONFIGURER"
            ),
        )
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText(
            "Clé enregistrée — laisser vide pour la conserver"
            if self._stored_key and not require_new_key
            else "Saisissez une nouvelle clé API Gemini"
            if require_new_key
            else "Saisissez votre clé API Gemini"
        )
        self._key_input.setFont(QFont("Segoe UI", 9))
        self._key_input.setFixedHeight(34)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 5px 9px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        key_card.addWidget(self._key_input)
        self._key_error = QLabel("")
        self._key_error.setWordWrap(True)
        self._key_error.setFont(QFont("Segoe UI", 8))
        self._key_error.setStyleSheet(f"color: {C.RED};")
        key_card.addWidget(self._key_error)
        self._key_input.textChanged.connect(self._on_key_changed)

        _components_card, self._components_detail = add_step(
            "02", "Vérification des composants",
            "Les composants requis seront contrôlés avant l’enregistrement.",
            "EN ATTENTE",
        )

        os_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}
        detected_status = (
            f"Détecté automatiquement : {os_name[detected]}"
            if detected else "Système détecté non pris en charge."
        )
        os_card, self._os_detail = add_step(
            "03", "Système d’exploitation",
            detected_status,
            "DÉTECTÉ" if detected else "NON PRIS EN CHARGE",
        )
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [
            ("windows", "Windows"),
            ("mac", "macOS"),
            ("linux", "Linux"),
        ]:
            btn = QPushButton(label)
            btn.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            self._os_btns[key] = btn
        os_row = QHBoxLayout()
        os_row.setSpacing(6)
        for button in self._os_btns.values():
            os_row.addWidget(button)
        os_card.addLayout(os_row)
        if detected:
            self._sel(detected)
        else:
            for button in self._os_btns.values():
                button.setEnabled(False)

        _init_card, _init_detail = add_step(
            "04", "Initialisation de Sarra",
            "Enregistre la configuration, puis ouvre l’interface principale.",
            "EN ATTENTE",
        )
        self._init_detail = _init_detail

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setFont(QFont("Segoe UI", 8))
        self._error_label.setStyleSheet(f"color: {C.RED};")
        content_layout.addWidget(self._error_label)

        self._init_btn = QPushButton("INITIALISER SARRA")
        self._init_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._init_btn.setFixedHeight(42)
        self._init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._init_btn.setStyleSheet(f"""
            QPushButton {{
                background: #003244; color: {C.WHITE};
                border: 1px solid {C.PRI}; border-radius: 5px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background: #00475a; border-color: {C.WHITE};
            }}
            QPushButton:disabled {{
                color: {C.TEXT_DIM}; background: {C.PANEL2};
                border-color: {C.BORDER};
            }}
        """)
        self._init_btn.clicked.connect(self._submit)
        content_layout.addWidget(self._init_btn)
        if require_new_key:
            footer_text = "La clé enregistrée a été refusée. Une nouvelle clé est requise."
        elif self._stored_key:
            footer_text = (
                "La clé modifiée sera utilisée lors de la prochaine connexion Gemini."
            )
        else:
            footer_text = (
                "La connexion à Gemini sera établie au démarrage de la session."
            )
        self._footer = QLabel(footer_text)
        self._footer.setWordWrap(True)
        self._footer.setFont(QFont("Segoe UI", 7))
        self._footer.setStyleSheet(f"color: {C.TEXT_DIM};")
        content_layout.addWidget(self._footer)
        layout.addWidget(content, stretch=1)
        self._pulse_on = True
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(900)
        self._pulse_timer.timeout.connect(self._pulse_identity)
        self._pulse_timer.start()

    def _pulse_identity(self) -> None:
        self._pulse_on = not self._pulse_on
        color = C.ACC2 if self._pulse_on else "#8b7820"
        self._identity_status.setStyleSheet(
            f"color: {color}; background: #171707; "
            "border: 1px solid #554b19; border-radius: 4px; padding: 8px;"
        )

    def _set_step(self, number: str, status: str, color: str | None = None) -> None:
        label = self._steps[number]
        label.setText(status)
        label.setStyleSheet(f"color: {color or C.TEXT_DIM};")

    def _on_key_changed(self, value: str) -> None:
        self._key_error.clear()
        self._error_label.clear()
        configured = bool(value.strip()) or (
            bool(self._stored_key) and not self._require_new_key
        )
        self._set_step(
            "01",
            "PRÊTE" if configured else "À CONFIGURER",
            C.GREEN if configured else C.TEXT_DIM,
        )

    def _sel(self, key: str):
        labels = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}
        if key not in labels:
            return
        self._sel_os = key
        self._os_detail.setText(
            f"Sélection choisie : {labels[key]}."
            if key != {
                "Windows": "windows",
                "Darwin": "mac",
                "Linux": "linux",
            }.get(_OS)
            else f"Détecté automatiquement : {labels[key]}."
        )
        self._set_step("03", "SÉLECTIONNÉE", C.GREEN)
        pal = {
            "windows": (C.PRI, "#001a22"),
            "mac": (C.ACC2, "#1a1400"),
            "linux": (C.GREEN, "#001a0d"),
        }
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 4px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PANEL2}; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 4px;
                    }}
                    QPushButton:hover {{
                        color: {C.WHITE}; border: 1px solid {C.BORDER_B};
                    }}
                    QPushButton:disabled {{
                        color: {C.TEXT_DIM}; border-color: {C.BORDER};
                    }}
                """)

    def _check_components(self) -> tuple[bool, str]:
        try:
            gemini_available = importlib.util.find_spec("google.genai") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            gemini_available = False
        if not gemini_available:
            return False, "Le composant Gemini est absent. Réinstallez les dépendances."
        avatar_note = (
            "Composants Gemini disponibles. Le VRM conserve son chargement habituel."
            if HAVE_SARRA else
            "Composants Gemini disponibles. Le repli avatar 2D reste disponible."
        )
        return True, avatar_note

    def _set_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._init_btn.setEnabled(True)
        self._init_btn.setText("RÉESSAYER")
        self._initializing = False

    def require_new_key(self) -> None:
        self._require_new_key = True
        self._initializing = False
        self._key_input.clear()
        self._key_input.setPlaceholderText("Saisissez une nouvelle clé API Gemini")
        self._key_detail.setText("La clé enregistrée a été refusée par Gemini.")
        self._key_error.clear()
        self._set_step("01", "À REMPLACER", C.ACC2)
        self._set_step("04", "EN ATTENTE", C.TEXT_DIM)
        self._identity_status.setText("●  CLÉ À REMPLACER")
        self._init_btn.setEnabled(True)
        self._init_btn.setText("INITIALISER SARRA")
        self._footer.setText(
            "La clé enregistrée a été refusée. Une nouvelle clé est requise."
        )

    def report_save_error(self, message: str) -> None:
        self._set_step("04", "ÉCHEC", C.RED)
        self._init_detail.setText(message)
        self._set_error(message)

    def complete_initialization(self) -> None:
        self._pulse_timer.stop()
        self._set_step("04", "RÉUSSIE", C.GREEN)
        self._init_detail.setText("Bienvenue chez Sarra. Ouverture de l’interface…")
        self._identity_status.setText("●  SARRA EST PRÊTE")
        self._identity_status.setStyleSheet(
            f"color: {C.GREEN}; background: #001a10; "
            f"border: 1px solid {C.GREEN_D}; border-radius: 4px; padding: 8px;"
        )

    def _submit(self):
        if self._initializing:
            return
        self._error_label.clear()
        key = self._key_input.text().strip()
        if not key and not self._require_new_key:
            key = self._stored_key
        if not key:
            self._set_step("01", "ÉCHEC", C.RED)
            self._key_error.setText("Saisissez une clé API Gemini pour continuer.")
            self._key_input.setFocus()
            return
        if self._sel_os not in {"windows", "mac", "linux"}:
            self._set_step("03", "ÉCHEC", C.RED)
            self._set_error("Sélectionnez un système pris en charge.")
            return

        self._initializing = True
        self._init_btn.setEnabled(False)
        self._init_btn.setText("INITIALISATION…")
        self._set_step("01", "PRÊTE", C.GREEN)
        self._set_step("02", "VÉRIFICATION…", C.ACC2)
        components_ready, detail = self._check_components()
        self._components_detail.setText(detail)
        if not components_ready:
            self._set_step("02", "ÉCHEC", C.RED)
            self._set_error(detail)
            return
        self._set_step("02", "RÉUSSIE", C.GREEN)
        self._set_step("03", "RÉUSSIE", C.GREEN)
        self._set_step("04", "EN COURS", C.ACC2)
        self._init_detail.setText("Enregistrement de la configuration locale…")
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — name, user name, UI colour, voice and core directives."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    directives_saved = pyqtSignal()          # official prompt changed on disk
    _OW, _OH = 400, 744

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self._directives_initial = _read_core_directives()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: {C.PANEL2}; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it
        self.on_directives  = None   # callable(text) — ouvre l'éditeur ; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        # ── Core directives — edited and saved in the panel itself ───────────
        # This is the exact text main.py feeds to the Live session. The small
        # GRAND button opens the same content in a roomier window; both paths
        # write core/prompt.txt through _write_core_directives().
        lay.addSpacing(4)
        dir_hdr = QHBoxLayout()
        dir_hdr.addWidget(_lbl("CORE DIRECTIVES  —  OFFICIEL", 8,
                               color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        dir_hdr.addStretch()
        dir_open_btn = QPushButton("⤢  GRAND")
        dir_open_btn.setFixedSize(76, 20)
        dir_open_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        dir_open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dir_open_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        dir_open_btn.setToolTip("Ouvrir les directives dans une grande fenêtre")
        dir_open_btn.clicked.connect(self._open_directives)
        dir_hdr.addWidget(dir_open_btn)
        lay.addLayout(dir_hdr)

        self._directives_edit = QTextEdit()
        self._directives_edit.setFont(QFont("Courier New", 8))
        self._directives_edit.setFixedHeight(124)
        self._directives_edit.setStyleSheet(f"""
            QTextEdit {{ background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 5px; }}
            QTextEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._directives_edit.setPlainText(self._directives_initial)
        lay.addWidget(self._directives_edit)

        self._directives_status = QLabel("")
        self._directives_status.setFont(QFont("Courier New", 7))
        self._directives_status.setWordWrap(True)
        self._set_directives_status(
            "core/prompt.txt — prompt officiel (copie racine synchronisée à l'enregistrement)."
            if self._directives_initial
            else "core/prompt.txt illisible — vérifiez le fichier.",
            error=not bool(self._directives_initial))
        lay.addWidget(self._directives_status)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _open_directives(self):
        try:
            if self.on_directives:
                # Carry unsaved panel edits into the large window instead of
                # silently reloading the old file under the user's cursor.
                self.on_directives(self._directives_edit.toPlainText())
        except Exception:
            pass

    def _set_directives_status(self, text: str, error: bool = False) -> None:
        self._directives_status.setText(text)
        self._directives_status.setStyleSheet(
            f"color: {C.RED if error else C.TEXT_DIM}; background: transparent;")

    def set_directives(self, text: str) -> None:
        """Adopt text saved by the large editor, without marking it dirty."""
        self._directives_edit.blockSignals(True)
        self._directives_edit.setPlainText(text)
        self._directives_edit.blockSignals(False)
        self._directives_initial = _normalise_core_directives(text)
        self._set_directives_status("✓ Directives officielles mises à jour.")

    def reload_directives(self) -> None:
        self.set_directives(_read_core_directives())

    def _save(self):
        # The directives field is part of APPLY CHANGES on purpose: saving here
        # is what promotes the edited text to the official system prompt.
        text = _normalise_core_directives(self._directives_edit.toPlainText())
        directives_changed = text != _normalise_core_directives(self._directives_initial)
        if directives_changed:
            try:
                text = _write_core_directives(text)
            except Exception as e:
                self._set_directives_status(f"✕ Directives non enregistrées : {e}",
                                            error=True)
                return
            self._directives_edit.setPlainText(text)
            self._directives_initial = text
            self._set_directives_status("✓ Directives officielles enregistrées.")

        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        # Signal order matters when voice and directives change together: the
        # voice handler requests a fresh session (keep_context=False), which
        # also re-reads the prompt. Emitting the prompt signal first means the
        # stronger voice request is the one left standing.
        if directives_changed:
            self.directives_saved.emit()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class DirectivesOverlay(QWidget):
    """Éditeur des directives système (core/prompt.txt).

    Fenêtre d'outil : lecture/écriture du fichier réellement chargé par main.py
    comme prompt système. SAUVER garde un .bak et publie les directives
    officielles ; le panneau de configuration et la copie racine sont alignés.
    """

    saved = pyqtSignal(str)
    _OW, _OH = 560, 620

    def __init__(self, parent=None, text: str | None = None):
        super().__init__(parent)
        self._fichier = CORE_DIRECTIVES_FILE
        self._backup = CORE_DIRECTIVES_BACKUP
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            DirectivesOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("📜  CORE DIRECTIVES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hdr)
        lay.addWidget(QLabel("Directives officielles de l'assistant — appliquées à la prochaine session Live."))
        lay.itemAt(lay.count() - 1).widget().setFont(QFont("Courier New", 8))
        lay.itemAt(lay.count() - 1).widget().setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;")
        lay.itemAt(lay.count() - 1).widget().setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._texte = QTextEdit()
        self._texte.setFont(QFont("Courier New", 9))
        self._texte.setStyleSheet(f"""
            QTextEdit {{ background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 6px; }}
        """)
        if text is not None:
            self._texte.setPlainText(text)
        else:
            try:
                self._texte.setPlainText(
                    self._fichier.read_text(encoding="utf-8"))
            except Exception as e:
                self._texte.setPlainText(f"Illisible : {e}")
        lay.addWidget(self._texte, 1)

        self._status = QLabel("")
        self._status.setFont(QFont("Courier New", 8))
        self._status.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(self._status)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        save_btn = QPushButton("▸  SAUVER")
        save_btn.setFixedHeight(32)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._sauver)
        btn_row.addWidget(save_btn)
        rest_btn = QPushButton("RESTAURER .bak")
        rest_btn.setFixedHeight(32)
        rest_btn.setFont(QFont("Courier New", 8))
        rest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rest_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        rest_btn.clicked.connect(self._restaurer)
        btn_row.addWidget(rest_btn)
        close_btn = QPushButton("FERMER")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

    def _sauver(self) -> None:
        try:
            texte = _write_core_directives(self._texte.toPlainText())
        except Exception as e:
            self._status.setText(f"Échec : {e}")
            return
        self._texte.setPlainText(texte)
        self._status.setText(
            "✓ Enregistré — directives officielles, actives à la prochaine session Live.")
        self.saved.emit(texte)

    def _restaurer(self) -> None:
        try:
            if not self._backup.is_file():
                self._status.setText("Pas de .bak — rien à restaurer.")
                return
            self._texte.setPlainText(self._backup.read_text(encoding="utf-8"))
            self._status.setText("Contenu .bak rechargé — SAUVER pour appliquer.")
        except Exception as e:
            self._status.setText(f"Échec : {e}")


class LipsyncOverlay(QWidget):
    """Page de calibration labiale — aperçu des 15 visèmes + gains persistés.

    Fenêtre d'outil (déplaçable, au-dessus de l'avatar). Cliquer un visème le
    tient à l'intensité choisie ; les gains s'appliquent en direct et SAUVER
    les écrit dans config/avatar_lipsync.json (rechargés au démarrage).
    Fermer sans sauver restaure les gains précédemment sauvés.
    """

    VISEMES_15 = [
        (0, "sil"), (1, "PP"), (2, "FF"), (3, "TH"), (4, "DD"),
        (5, "K"), (6, "CH"), (7, "SS"), (8, "nn"), (9, "RR"),
        (10, "AA"), (11, "E"), (12, "I"), (13, "O"), (14, "OU"),
    ]
    HUMEURS_9 = [
        ("neutre", "Neutre"), ("ecoute", "Écoute"),
        ("sourire", "Sourire"), ("grand_sourire", "Grand sourire"),
        ("joie", "Joie"), ("triste", "Triste"),
        ("colere", "Colère"), ("surpris", "Surpris"),
        ("reflechi", "Réfléchi"),
    ]
    _OW = 460

    def __init__(self, sarra=None, parent=None):
        super().__init__(parent)
        self._sarra = sarra
        try:
            from memory.config_manager import get_lipsync_gains
            g = get_lipsync_gains()
        except Exception:
            g = {"jaw_gain": 1.0, "articulation_gain": 1.0}
        self._saved = {
            "jaw_gain": g.get("jaw_gain", 1.0),
            "articulation_gain": g.get("articulation_gain", 1.0),
            "fluidite": g.get("fluidite", 1.0),
            "force_fermetures": g.get("force_fermetures", 1.0),
            "amplitude": g.get("amplitude", 1.0),
            "sync_ms": g.get("sync_ms", 0.0),
            "seuil_silence": g.get("seuil_silence", 0.02),
            "visemes": dict(g.get("visemes", {}) or {}),
            "humeurs": dict(g.get("humeurs", {}) or {}),
            "autocalib": dict(g.get("autocalib", {}) or {}),
            "neutre": dict(g.get("neutre", {}) or {}),
            "plages": {k: list(v) for k, v in
                       (g.get("plages", {}) or {}).items()},
        }
        # Intensités par humeur (1.0 par défaut) + sélection courante.
        self._humeurs = {e: float(v) for e, v in self._saved["humeurs"].items()
                         if e in dict(self.HUMEURS_9)}
        self._sel_humeur = "sourire"
        # Trims de travail {vid: {comp: val}} — sliders + aperçu direct.
        self._trims = {vid: dict(t) for vid, t in self._saved["visemes"].items()}
        # Neutre personnalisé du comparateur + plages des sliders.
        self._neutre = dict(self._saved.get("neutre", {}) or {})
        self._plages = {k: list(v) for k, v in
                        self._saved.get("plages", {}).items()}
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            LipsyncOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("👄  CALIBRATION LABIALE", 12, True))
        lay.addWidget(_lbl("Clique un visème : tenu à l'intensité choisie. "
                           " Modèle 3D courant.", 7, color=C.TEXT_DIM))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        rub_row = QHBoxLayout(); rub_row.setSpacing(6)
        rub_row.addWidget(_lbl("RUBRIQUE", 8, color=C.TEXT_DIM))
        self._pages_combo = QComboBox()
        for txt in ("👄 Visèmes + réglage", "🎚 Gains",
                    "☺ Humeurs", "🧪 Tests", "👑 Master"):
            self._pages_combo.addItem(txt)
        self._pages_combo.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._pages_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pages_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 4px 6px;
            }}
            QComboBox QAbstractItemView {{ background: {C.PANEL}; color: {C.TEXT}; }}
        """)
        rub_row.addWidget(self._pages_combo, 1)
        lay.addLayout(rub_row)

        self._pages_stack = QStackedWidget()
        lay.addWidget(self._pages_stack, 1)
        self._pages = {}

        def _page(nom):
            w = QWidget()
            lay_page = QVBoxLayout(w)
            lay_page.setContentsMargins(0, 4, 0, 4)
            lay_page.setSpacing(6)
            self._pages_stack.addWidget(w)
            self._pages[nom] = lay_page
            return lay_page

        lay_visemes = _page("visemes")
        lay_gains = _page("gains")
        lay_hum = _page("humeurs")
        lay_tests = _page("tests")
        lay_master = _page("master")
        self._pages_combo.currentIndexChanged.connect(
            self._pages_stack.setCurrentIndex)

        grid = QGridLayout(); grid.setSpacing(4)
        self._vis_btns = []
        self._sel_viseme = 10
        for pos, (vid, nom) in enumerate(self.VISEMES_15):
            b = QPushButton(f"{vid}·{nom}")
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background: {C.PANEL2}; color: {C.TEXT};
                    border: 1px solid {C.BORDER}; border-radius: 3px; padding: 6px 0; }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
            """)
            b.clicked.connect(lambda _=False, v=vid: self._choisir_viseme(v))
            grid.addWidget(b, pos // 5, pos % 5)
            self._vis_btns.append(b)
        lay_visemes.addLayout(grid)

        lay_visemes.addWidget(_lbl("INTENSITÉ APERÇU", 8, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))
        int_row = QHBoxLayout()
        self._int_slider = QSlider(Qt.Orientation.Horizontal)
        self._int_slider.setRange(10, 100)
        self._int_slider.setValue(90)
        self._int_slider.valueChanged.connect(
            lambda v: self._int_lbl.setText(f"{v}%"))
        int_row.addWidget(self._int_slider, 1)
        self._int_lbl = _lbl("90%", 8, True, C.PRI)
        self._int_lbl.setFixedWidth(44)
        int_row.addWidget(self._int_lbl)
        lay_visemes.addLayout(int_row)

        seq_row = QHBoxLayout(); seq_row.setSpacing(8)
        relacher_btn = QPushButton("RELÂCHER")
        relacher_btn.setFixedHeight(30)
        relacher_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        relacher_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        relacher_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        relacher_btn.clicked.connect(self._relacher)
        seq_row.addWidget(relacher_btn)
        seq_btn = QPushButton("▶  SÉQUENCE TEST")
        seq_btn.setFixedHeight(30)
        seq_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        seq_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        seq_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        seq_btn.clicked.connect(self._sequence)
        seq_row.addWidget(seq_btn)
        lay_tests.addLayout(seq_row)

        self._jaw_slider, self._jaw_lbl = self._gain_row(
            lay_gains, "GAIN MÂCHOIRE — ouverture", self._saved["jaw_gain"],
            lambda v: self._bridge_do("jaw", v), plage="jaw_gain")
        self._art_slider, self._art_lbl = self._gain_row(
            lay_gains, "GAIN ARTICULATION — force des formes",
            self._saved["articulation_gain"],
            lambda v: self._bridge_do("art", v), plage="articulation_gain")
        self._flu_slider, self._flu_lbl = self._gain_row(
            lay_gains, "FLUIDITÉ — vitesse des transitions",
            self._saved["fluidite"], lambda v: self._bridge_do("flu", v),
            plage="fluidite")
        self._fer_slider, self._fer_lbl = self._gain_row(
            lay_gains, "FORCE FERMETURES — PP, FF, SS appuient",
            self._saved["force_fermetures"],
            lambda v: self._bridge_do("fer", v), plage="force_fermetures")
        self._amp_slider, self._amp_lbl = self._gain_row(
            lay_gains, "AMPLITUDE BOUCHE — taille globale des mouvements",
            self._saved["amplitude"], lambda v: self._bridge_do("amp", v),
            plage="amplitude")
        self._syn_slider, self._syn_lbl = self._gain_row(
            lay_gains, "SYNC A/V — ms (négatif = bouche plus tôt)",
            self._saved["sync_ms"], lambda v: self._bridge_do("sync", v),
            unite="ms", div=1, plage="sync_ms")
        self._seu_slider, self._seu_lbl = self._gain_row(
            lay_gains, "SEUIL SILENCE — en dessous, la bouche se ferme",
            self._saved["seuil_silence"],
            lambda v: self._bridge_do("seuil", v), plage="seuil_silence")

        self._trim_titre = QLabel("")
        self._trim_titre.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._trim_titre.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay_visemes.addWidget(self._trim_titre)
        self._trim_sliders = {}
        for comp, titre in (("open", "OUVERTURE — mâchoire"),
                            ("close", "FERMETURE — lèvres closes"),
                            ("stretch", "ÉTIREMENT — sourire horizontal"),
                            ("round", "ARRONDI — moue / OU")):
            s, _sl = self._gain_row(
                lay_visemes, titre, 1.0,
                lambda v, c=comp: self._regler_trim(c, v),
                plage=f"trim_{comp}")
            self._trim_sliders[comp] = s
        self._charger_trims_viseme()

        lay_tests.addWidget(_lbl("TEST PAR MOT — tape un mot, regarde la bouche",
                           8, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))
        mot_row = QHBoxLayout(); mot_row.setSpacing(6)
        self._mot_input = QLineEdit()
        self._mot_input.setPlaceholderText("ex.  maman")
        self._mot_input.setFont(QFont("Courier New", 10))
        self._mot_input.setFixedHeight(30)
        self._mot_input.setStyleSheet(
            f"QLineEdit {{ background: {C.PANEL2}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}")
        self._mot_input.returnPressed.connect(self._jouer_mot)
        mot_row.addWidget(self._mot_input, 1)
        jouer_btn = QPushButton("▶  JOUER")
        jouer_btn.setFixedHeight(30)
        jouer_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        jouer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        jouer_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 0 12px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        jouer_btn.clicked.connect(self._jouer_mot)
        mot_row.addWidget(jouer_btn)
        lay_tests.addLayout(mot_row)

        self._cmp_btn = QPushButton("⚖  COMPARER : réglages neutres")
        self._cmp_btn.setCheckable(True)
        self._cmp_btn.setFixedHeight(28)
        self._cmp_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._cmp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cmp_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            QPushButton:checked {{ background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI}; }}
        """)
        self._cmp_btn.toggled.connect(self._comparer)
        lay_tests.addWidget(self._cmp_btn)

        lay_tests.addWidget(_lbl("AUTO — PARAMÈTRES (persistés avec SAUVER)",
                           8, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))
        self._auto_params = dict(
            self._saved.get("autocalib", {}) or {})
        _defauts_auto = {"sonde": 0.9, "attente_ms": 600.0, "pp_fuite": 0.04,
                         "aa_bas": 0.15, "aa_haut": 0.50, "i_bas": 0.04,
                         "i_haut": 0.20, "jaw_plus": 1.3, "jaw_moins": 0.8,
                         "trim_fort": 1.5}
        for cle, defaut in _defauts_auto.items():
            self._auto_params.setdefault(cle, defaut)
        self._auto_sliders = {}
        for cle, titre, lo, hi, unite, div in (
                ("sonde", " Intensité sondes", 50, 100, "%", 100),
                ("attente_ms", "Attente mesure", 300, 1500, "ms", 1),
                ("pp_fuite", "Fuite PP tolérable", 0, 20, "%", 100),
                ("aa_bas", "AA trop fermé sous", 5, 40, "%", 100),
                ("aa_haut", "AA trop ouvert au-delà", 30, 80, "%", 100),
                ("i_bas", "I trop fermé sous", 0, 20, "%", 100),
                ("i_haut", "I trop ouvert au-delà", 10, 40, "%", 100),
                ("jaw_plus", "Mâchoire si AA fermé", 100, 200, "%", 100),
                ("jaw_moins", "Mâchoire si AA béant", 20, 100, "%", 100),
                ("trim_fort", "Renfort trims", 100, 200, "%", 100)):
            s, _sl = self._gain_row(
                lay_tests, titre, self._auto_params[cle],
                lambda v, c=cle: self._auto_params.__setitem__(c, v),
                lo=lo, hi=hi, unite=unite, div=div)
            self._auto_sliders[cle] = s

        lay_hum.addWidget(_lbl("HUMEURS — tient l'expression choisie",
                           8, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))
        self._hum_titre = QLabel("")
        self._hum_titre.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._hum_titre.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay_hum.addWidget(self._hum_titre)
        hum_int_row = QHBoxLayout()
        hum_int_row.addWidget(_lbl("INTENSITÉ", 8, color=C.TEXT_DIM))
        self._hum_int_slider = QSlider(Qt.Orientation.Horizontal)
        self._hum_int_slider.setRange(10, 100)
        self._hum_int_slider.setValue(100)
        self._hum_int_lbl = _lbl("100%", 8, True, C.PRI)
        self._hum_int_lbl.setFixedWidth(44)
        self._hum_int_slider.valueChanged.connect(self._regler_humeur)
        hum_int_row.addWidget(self._hum_int_slider, 1)
        hum_int_row.addWidget(self._hum_int_lbl)
        lay_hum.addLayout(hum_int_row)
        hum_grid = QGridLayout(); hum_grid.setSpacing(4)
        self._hum_btns = []
        self._sel_humeur = "sourire"
        for pos, (emo, txt) in enumerate(self.HUMEURS_9):
            b = QPushButton(txt)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background: {C.PANEL2}; color: {C.TEXT};
                    border: 1px solid {C.BORDER}; border-radius: 3px; padding: 6px 0; }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
            """)
            b.clicked.connect(lambda _=False, e=emo: self._choisir_humeur(e))
            hum_grid.addWidget(b, pos // 3, pos % 3)
            self._hum_btns.append(b)
        lay_hum.addLayout(hum_grid)
        self._hum_titre.setText(
            f"INTENSITÉ — {self._nom_humeur(self._sel_humeur)}")
        self._hum_int_slider.blockSignals(True)
        self._hum_int_slider.setValue(
            int(round(self._humeurs.get(self._sel_humeur, 1.0) * 100)))
        self._hum_int_slider.blockSignals(False)

        self._auto_btn = QPushButton("✨  AUTO-CALIBRER")
        self._auto_btn.setFixedHeight(30)
        self._auto_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        self._auto_btn.clicked.connect(self._auto_calibrer)
        lay_tests.addWidget(self._auto_btn)
        self._calib_en_cours = False

        self._usine_btn = QPushButton("↩  REPRENDRE SAUVEGARDÉS")
        self._usine_btn.setFixedHeight(28)
        self._usine_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._usine_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._usine_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        self._usine_btn.setToolTip("Remet les curseurs sur les dernières valeurs sauvées")
        self._usine_btn.clicked.connect(self._reprendre_sauvegardes)
        lay_tests.addWidget(self._usine_btn)

        # ── Page Master : neutre personnalisé + limites des plages ──
        lay_master.addWidget(_lbl("👑  NEUTRE PERSONNALISÉ — référence du comparateur",
                           8, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))
        self._neutre_lbl = _lbl("", 8, color=C.TEXT_MED,
                                align=Qt.AlignmentFlag.AlignLeft)
        lay_master.addWidget(self._neutre_lbl)
        neu_row = QHBoxLayout(); neu_row.setSpacing(8)
        cap_btn = QPushButton("📸  CAPTURER L'ÉTAT ACTUEL")
        cap_btn.setFixedHeight(30)
        cap_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        cap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cap_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        cap_btn.clicked.connect(self._capturer_neutre)
        neu_row.addWidget(cap_btn)
        usi_btn = QPushButton("↩ NEUTRE USINE (1.0)")
        usi_btn.setFixedHeight(30)
        usi_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        usi_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        usi_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        usi_btn.clicked.connect(self._neutre_usine)
        neu_row.addWidget(usi_btn)
        lay_master.addLayout(neu_row)
        self._maj_neutre_lbl()

        lay_master.addWidget(_lbl("LIMITES — plage de chaque réglage",
                           8, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))
        lim_row = QHBoxLayout(); lim_row.setSpacing(6)
        self._lim_combo = QComboBox()
        self._lim_noms = [
            ("jaw_gain", "Mâchoire"), ("articulation_gain", "Articulation"),
            ("fluidite", "Fluidité"), ("force_fermetures", "Fermetures"),
            ("amplitude", "Amplitude"), ("sync_ms", "Sync A/V"),
            ("seuil_silence", "Seuil silence"),
            ("trim_open", "Trim ouverture"), ("trim_close", "Trim fermeture"),
            ("trim_stretch", "Trim étirement"), ("trim_round", "Trim arrondi"),
        ]
        for _cle, _txt in self._lim_noms:
            self._lim_combo.addItem(_txt, _cle)
        self._lim_combo.setFont(QFont("Courier New", 8))
        self._lim_combo.setStyleSheet(f"""
            QComboBox {{ background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px; }}
            QComboBox QAbstractItemView {{ background: {C.PANEL}; color: {C.TEXT}; }}
        """)
        self._lim_combo.currentIndexChanged.connect(self._charger_limites)
        lim_row.addWidget(self._lim_combo, 1)
        self._lim_min = QLineEdit()
        self._lim_min.setPlaceholderText("min")
        self._lim_min.setFont(QFont("Courier New", 8))
        self._lim_min.setFixedWidth(64)
        self._lim_min.setStyleSheet(
            f"QLineEdit {{ background: {C.PANEL2}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px; }}")
        lim_row.addWidget(self._lim_min)
        self._lim_max = QLineEdit()
        self._lim_max.setPlaceholderText("max")
        self._lim_max.setFont(QFont("Courier New", 8))
        self._lim_max.setFixedWidth(64)
        self._lim_max.setStyleSheet(
            f"QLineEdit {{ background: {C.PANEL2}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px; }}")
        lim_row.addWidget(self._lim_max)
        lim_ok = QPushButton("OK")
        lim_ok.setFixedHeight(28)
        lim_ok.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lim_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        lim_ok.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 0 12px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        lim_ok.clicked.connect(self._appliquer_limites)
        lim_row.addWidget(lim_ok)
        lay_master.addLayout(lim_row)
        self._lim_status = _lbl("", 8, color=C.TEXT_DIM,
                                align=Qt.AlignmentFlag.AlignLeft)
        lay_master.addWidget(self._lim_status)
        self._charger_limites()

        self._status = _lbl("", 8, color=C.TEXT_DIM)
        lay.addWidget(self._status)

        for lay_page in self._pages.values():
            lay_page.addStretch(1)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        save_btn = QPushButton("▸  SAUVER")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._sauver)
        btn_row.addWidget(save_btn)
        close_btn = QPushButton("FERMER")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._fermer)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        if not self._pont_actif():
            self._status.setText("Aperçu indisponible (avatar 3D coupé) — "
                                 "réglages modifiables et sauvés quand même.")
            for w in (self._vis_btns + self._hum_btns + [self._int_slider, seq_btn,
                                        relacher_btn, self._mot_input,
                                        jouer_btn, self._cmp_btn,
                                        self._auto_btn, self._usine_btn]):
                w.setEnabled(False)

    def _plage(self, cle: str) -> tuple:
        """[lo, hi] d'un réglage (défauts sûrs si absent)."""
        try:
            lo, hi = self._plages.get(cle, [0, 100])
            return int(lo), int(hi)
        except (TypeError, ValueError):
            return 0, 100

    def _gain_row(self, lay, titre, valeur, on_change, lo=20, hi=200,
                    unite="%", div=100, plage=None):
        if plage is not None:
            lo, hi = self._plage(plage)
        titre_lbl = QLabel(titre)
        titre_lbl.setFont(QFont("Courier New", 8))
        titre_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(titre_lbl)
        row = QHBoxLayout()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(int(round(valeur * div)))
        lbl = QLabel(f"{int(round(valeur * div))}{unite}")
        lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lbl.setFixedWidth(64)
        slider.valueChanged.connect(
            lambda v: self._on_gain(v, lbl, on_change, unite, div))
        row.addWidget(slider, 1)
        row.addWidget(lbl)
        lay.addLayout(row)
        return slider, lbl

    def _pont_actif(self) -> bool:
        try:
            return self._sarra is not None and self._sarra.disponible
        except Exception:
            return False

    def _on_gain(self, v: int, lbl: QLabel, on_change, unite="%",
                   div=100) -> None:
        lbl.setText(f"{v}{unite}")
        try:
            on_change(v / div)
        except Exception:
            pass

    def _bridge_do(self, quoi: str, v: float) -> None:
        if not self._pont_actif():
            return
        try:
            if quoi == "jaw":
                self._sarra.set_jaw_gain(v)
            elif quoi == "art":
                self._sarra.set_articulation_gain(v)
            elif quoi == "flu":
                self._sarra.set_fluidite(v)
            elif quoi == "fer":
                self._sarra.set_force_fermetures(v)
            elif quoi == "amp":
                self._sarra.set_amplitude(v)
            elif quoi == "sync":
                self._sarra.set_sync_offset(v)
            else:
                self._sarra.set_seuil_silence(v)
        except Exception:
            pass

    def _choisir_viseme(self, vid: int) -> None:
        """Sélectionne un visème : titre + sliders chargés, puis aperçu tenu."""
        self._sel_viseme = vid
        self._charger_trims_viseme()
        self._apercu(vid)

    def _nom_viseme(self, vid: int) -> str:
        for v, nom in self.VISEMES_15:
            if v == vid:
                return nom
        return "?"

    def _charger_trims_viseme(self) -> None:
        t = self._trims.get(str(self._sel_viseme), {})
        self._trim_titre.setText(
            f"RÉGLAGE FIN — visème {self._sel_viseme}·"
            f"{self._nom_viseme(self._sel_viseme)}")
        for comp, slider in self._trim_sliders.items():
            slider.blockSignals(True)
            slider.setValue(int(round(t.get(comp, 1.0) * 100)))
            slider.blockSignals(False)

    def _regler_trim(self, comp: str, valeur: float) -> None:
        vid = str(self._sel_viseme)
        t = self._trims.setdefault(vid, {})
        if valeur == 1.0:
            t.pop(comp, None)
            if not t:
                self._trims.pop(vid, None)
        else:
            t[comp] = round(max(-0.5, min(2.0, valeur)), 3)
        try:
            if self._pont_actif():
                self._sarra.set_trim_viseme(self._sel_viseme, comp,
                                            t.get(comp, 1.0))
                self._apercu(self._sel_viseme)
        except Exception:
            pass

    def _appliquer_trims(self, trims: dict) -> None:
        """Pousse un jeu complet de trims (reset d'abord)."""
        try:
            if not self._pont_actif():
                return
            self._sarra.reset_trims()
            for vid, t in (trims or {}).items():
                for comp, val in (t or {}).items():
                    self._sarra.set_trim_viseme(int(vid), comp, val)
        except Exception:
            pass

    def _apercu(self, vid: int) -> None:
        if not self._pont_actif():
            return
        try:
            self._sarra.apercu_viseme(vid, self._int_slider.value() / 100.0)
        except Exception:
            pass

    def _relacher(self) -> None:
        try:
            if self._pont_actif():
                self._sarra.relacher_visemes()
        except Exception:
            pass

    def _sequence(self) -> None:
        try:
            if self._pont_actif():
                self._sarra.jouer_sequence_test()
        except Exception:
            pass

    def _maj_neutre_lbl(self) -> None:
        try:
            n = self._neutre
            self._neutre_lbl.setText(
                f"Neutre actuel : mâchoire {n['jaw_gain']:.2f} · "
                f"articulation {n['articulation_gain']:.2f} · "
                f"sync {n['sync_ms']:.0f} ms · seuil {n['seuil_silence']:.3f}")
        except Exception:
            pass

    def _capturer_neutre(self) -> None:
        jaw, art, flu, fer, amp, syn, seu = self._gains_courants()
        self._neutre = {"jaw_gain": jaw, "articulation_gain": art,
                        "fluidite": flu, "force_fermetures": fer,
                        "amplitude": amp, "sync_ms": syn,
                        "seuil_silence": seu}
        self._maj_neutre_lbl()
        self._status.setText("📸 Neutre capturé — SAUVER pour garder.")

    def _neutre_usine(self) -> None:
        self._neutre = {"jaw_gain": 1.0, "articulation_gain": 1.0,
                        "fluidite": 1.0, "force_fermetures": 1.0,
                        "amplitude": 1.0, "sync_ms": 0.0,
                        "seuil_silence": 0.02}
        self._maj_neutre_lbl()
        self._status.setText("↩ Neutre usine — SAUVER pour garder.")

    def _cle_limite(self) -> str:
        try:
            return self._lim_combo.currentData() or "jaw_gain"
        except Exception:
            return "jaw_gain"

    def _charger_limites(self) -> None:
        try:
            lo, hi = self._plage(self._cle_limite())
            self._lim_min.blockSignals(True)
            self._lim_max.blockSignals(True)
            self._lim_min.setText(str(lo))
            self._lim_max.setText(str(hi))
            self._lim_min.blockSignals(False)
            self._lim_max.blockSignals(False)
        except Exception:
            pass

    def _appliquer_limites(self) -> None:
        from memory.config_manager import _PLAGES_ABSOLUES
        cle = self._cle_limite()
        try:
            lo = int(self._lim_min.text().strip())
            hi = int(self._lim_max.text().strip())
        except (TypeError, ValueError):
            self._lim_status.setText("Min/max entiers attendus.")
            return
        abs_lo, abs_hi = _PLAGES_ABSOLUES.get(cle, (0, 100))
        lo = max(abs_lo, min(abs_hi, lo))
        hi = max(abs_lo, min(abs_hi, hi))
        if lo >= hi:
            self._lim_status.setText("Il faut min < max "
                                     f"(bornes {abs_lo}…{abs_hi}).")
            return
        self._plages[cle] = [lo, hi]
        self._reconstruire_plage(cle)
        self._lim_status.setText(f"✓ {cle} : {lo}…{hi} — SAUVER pour garder.")

    def _reconstruire_plage(self, cle: str) -> None:
        """Re-applique la plage d'un réglage à ses sliders (sans perdre tout)."""
        try:
            lo, hi = self._plage(cle)
            if cle == "sync_ms":
                for slider in (self._syn_slider,):
                    slider.setRange(lo, hi)
            elif cle == "seuil_silence":
                for slider in (self._seu_slider,):
                    slider.setRange(lo, hi)
            elif cle.startswith("trim_"):
                comp = cle[5:]
                if comp in self._trim_sliders:
                    self._trim_sliders[comp].setRange(lo, hi)
            else:
                corresp = {"jaw_gain": self._jaw_slider,
                           "articulation_gain": self._art_slider,
                           "fluidite": self._flu_slider,
                           "force_fermetures": self._fer_slider,
                           "amplitude": self._amp_slider}
                if cle in corresp:
                    corresp[cle].setRange(lo, hi)
        except Exception:
            pass

    def _jouer_mot(self) -> None:
        try:
            mot = self._mot_input.text().strip()
            if mot and self._pont_actif():
                self._status.setText(f"▶  « {mot[:24]} »")
                self._sarra.jouer_mot(mot)
        except Exception:
            pass

    def _reprendre_sauvegardes(self) -> None:
        """Remet curseurs et direct sur les dernières valeurs sauvées."""
        try:
            s = self._saved
            for slider, lbl, val, unite in (
                    (self._jaw_slider, self._jaw_lbl,
                     int(round(s["jaw_gain"] * 100)), "%"),
                    (self._art_slider, self._art_lbl,
                     int(round(s["articulation_gain"] * 100)), "%"),
                    (self._flu_slider, self._flu_lbl,
                     int(round(s["fluidite"] * 100)), "%"),
                    (self._fer_slider, self._fer_lbl,
                     int(round(s["force_fermetures"] * 100)), "%"),
                    (self._amp_slider, self._amp_lbl,
                     int(round(s["amplitude"] * 100)), "%"),
                    (self._syn_slider, self._syn_lbl,
                     int(round(s["sync_ms"])), "ms"),
                    (self._seu_slider, self._seu_lbl,
                     int(round(s["seuil_silence"] * 100)), "%")):
                slider.setValue(val)
                lbl.setText(f"{val}{unite}")
            self._trims = {vid: dict(t) for vid, t in
                           s.get("visemes", {}).items()}
            self._humeurs = dict(s.get("humeurs", {}))
            self._charger_trims_viseme()
            self._hum_titre.setText(
                f"INTENSITÉ — {self._nom_humeur(self._sel_humeur)}")
            self._hum_int_slider.blockSignals(True)
            self._hum_int_slider.setValue(
                int(round(self._humeurs.get(self._sel_humeur, 1.0) * 100)))
            self._hum_int_slider.blockSignals(False)
            self._appliquer_trims(self._trims)
            self._status.setText("↩ Valeurs sauvées reprises.")
        except Exception as e:
            self._status.setText(f"Échec : {e}")

    def _humeur(self, emotion: str) -> None:
        self._choisir_humeur(emotion)

    def _nom_humeur(self, emo: str) -> str:
        for e, txt in self.HUMEURS_9:
            if e == emo:
                return txt
        return emo

    def _choisir_humeur(self, emotion: str) -> None:
        """Sélectionne une humeur : slider calé sur son intensité + aperçu."""
        self._sel_humeur = emotion
        self._hum_titre.setText(f"INTENSITÉ — {self._nom_humeur(emotion)}")
        self._hum_int_slider.blockSignals(True)
        self._hum_int_slider.setValue(
            int(round(self._humeurs.get(emotion, 1.0) * 100)))
        self._hum_int_lbl.setText(
            f"{int(round(self._humeurs.get(emotion, 1.0) * 100))}%")
        self._hum_int_slider.blockSignals(False)
        try:
            if self._pont_actif():
                self._sarra.set_emotion(
                    emotion, self._humeurs.get(emotion, 1.0))
                self._status.setText(f"☺  Humeur : {emotion}")
        except Exception:
            pass

    def _regler_humeur(self, v: int) -> None:
        self._hum_int_lbl.setText(f"{v}%")
        k = max(0.1, min(1.0, v / 100.0))
        self._humeurs[self._sel_humeur] = round(k, 3)
        try:
            if self._pont_actif():
                self._sarra.set_emotion(self._sel_humeur, k)
        except Exception:
            pass

    # ── auto-calibration ─────────────────────────────────────────────────
    # Boucle fermée : fond témoin → formes contrastées → lecture pixels →
    # ajustements bornés → vérification → résumé. Ne sauve jamais seul :
    # SAUVER reste le geste de confirmation (fermer sans sauver restaure).
    _CALIB_SONDES = ((10, "AA"), (1, "PP"), (12, "I"), (7, "S"))

    def _auto_calibrer(self) -> None:
        if not self._pont_actif() or self._calib_en_cours:
            return
        self._calib_en_cours = True
        self._auto_btn.setEnabled(False)
        self._status.setText("✨ Calibration… fond témoin.")
        # Part des valeurs ACTUELLES (sliders + trims) : on affine, on ne
        # rase pas. Seul le fond témoin est imposé pour la mesure.
        try:
            self._sarra.fond_calibration(True)
        except Exception:
            pass
        self._calib_mesures = {}
        self._calib_etape(0)

    def _calib_etape(self, i: int) -> None:
        if not self._calib_en_cours or not self.isVisible():
            self._calib_stop()
            return
        if i >= len(self._CALIB_SONDES):
            self._calib_verifie(0)
            return
        vid, nom = self._CALIB_SONDES[i]
        self._status.setText(f"✨ Mesure {nom} ({i + 1}/{len(self._CALIB_SONDES)})…")
        try:
            self._sarra.apercu_viseme(vid, self._auto_params["sonde"])
        except Exception:
            pass
        QTimer.singleShot(int(self._auto_params["attente_ms"]),
                          lambda: self._calib_lit(i))

    def _calib_lit(self, i: int) -> None:
        if not self._calib_en_cours or not self.isVisible():
            self._calib_stop()
            return
        vid, nom = self._CALIB_SONDES[i]
        try:
            self._sarra.demander_mesure(lambda res: self._calib_recoit(i, res))
        except Exception:
            self._calib_stop()

    def _calib_recoit(self, i: int, res) -> None:
        if not self._calib_en_cours or not self.isVisible():
            self._calib_stop()
            return
        vid, nom = self._CALIB_SONDES[i]
        if not isinstance(res, dict) or not res.get("ok"):
            self._status.setText("✨ Mesure impossible (cadrage ?) — abandon.")
            self._calib_stop()
            return
        self._calib_mesures[nom] = res
        QTimer.singleShot(120, lambda: self._calib_etape(i + 1))

    def _calib_verifie(self, passe: int) -> None:
        # Passe 1 : ajuste EN RELATIF aux valeurs actuelles (sliders + trims),
        # borné aux plages ; passe 2 : re-mesure AA+PP.
        m = self._calib_mesures
        P = self._auto_params
        if passe == 0:
            ajust = []

            def _trim_relatif(vid: str, comp: str, facteur: float,
                              plancher: float = -0.5) -> float:
                t = self._trims.setdefault(vid, {})
                nv = max(plancher, min(2.0, t.get(comp, 1.0) * facteur))
                t[comp] = round(nv, 3)
                if t[comp] == 1.0:
                    t.pop(comp, None)
                    if not t:
                        self._trims.pop(vid, None)
                try:
                    self._sarra.set_trim_viseme(int(vid), comp,
                                                t.get(comp, 1.0))
                except Exception:
                    pass
                return t.get(comp, 1.0)

            def _jaw_relatif(facteur: float) -> int:
                lo, hi = self._jaw_slider.minimum(), self._jaw_slider.maximum()
                nv = max(lo, min(hi, int(round(
                    self._jaw_slider.value() * facteur))))
                self._jaw_slider.setValue(nv)
                return nv
            try:
                if m.get("PP", {}).get("ouverture", 0) > P["pp_fuite"]:
                    nv = _trim_relatif("1", "close", P["trim_fort"])
                    ajust.append(f"PP.close×{nv:.2f}")
                ouv_aa = m.get("AA", {}).get("ouverture", 0)
                if ouv_aa < P["aa_bas"]:
                    nv = _jaw_relatif(P["jaw_plus"])
                    ajust.append(f"mâchoire {nv}%")
                elif ouv_aa > P["aa_haut"]:
                    nv = _jaw_relatif(P["jaw_moins"])
                    ajust.append(f"mâchoire {nv}%")
                ouv_i = m.get("I", {}).get("ouverture", 0)
                if ouv_i < P["i_bas"]:
                    nv = _trim_relatif("12", "open", P["trim_fort"])
                    ajust.append(f"I.open×{nv:.2f}")
                elif ouv_i > P["i_haut"]:
                    nv = _trim_relatif("12", "open", 1.0 / P["trim_fort"])
                    ajust.append(f"I.open×{nv:.2f}")
                if (m.get("I", {}).get("ok") and m.get("AA", {}).get("ok")
                        and m["I"].get("largeur", 0)
                        <= m["AA"].get("largeur", 0) * 1.05):
                    nv = _trim_relatif("12", "stretch", P["trim_fort"])
                    ajust.append(f"I.stretch×{nv:.2f}")
            except Exception:
                pass
            self._calib_resume = ajust
            self._status.setText("✨ Vérification…")
            self._calib_mesures = {}
            self._charger_trims_viseme()
            QTimer.singleShot(150, lambda: self._calib_verifie(1))
            return
        # Passe 2 : AA puis PP, puis bilan.
        self._status.setText("✨ Bilan…")
        try:
            self._sarra.apercu_viseme(10, self._auto_params["sonde"])
        except Exception:
            pass
        QTimer.singleShot(int(self._auto_params["attente_ms"]),
                          self._calib_bilan)

    def _calib_bilan(self) -> None:
        if not self._calib_en_cours:
            return
        try:
            self._sarra.demander_mesure(self._calib_bilan2)
        except Exception:
            self._calib_stop()

    def _calib_bilan2(self, res) -> None:
        ajust = getattr(self, "_calib_resume", [])
        try:
            self._sarra.relacher_visemes()
        except Exception:
            pass
        self._calib_stop()
        self._charger_trims_viseme()
        if isinstance(res, dict) and res.get("ok"):
            detail = ", ".join(ajust) if ajust else "déjà calibré"
            self._status.setText(
                f"✓ Auto-calibré ({detail}) — AA={res.get('ouverture', 0):.2f}. "
                f"SAUVER pour garder.")
        else:
            self._status.setText("✓ Mesure finale illisible — reprends à la main.")

    def _calib_stop(self) -> None:
        self._calib_en_cours = False
        try:
            self._auto_btn.setEnabled(True)
            if self._pont_actif():
                self._sarra.fond_calibration(False)
                self._sarra.relacher_visemes()
        except Exception:
            pass

    def _comparer(self, neutre: bool) -> None:
        # Comparateur : neutre personnalisé (Master) ou sliders courants.
        try:
            if not self._pont_actif():
                return
            if neutre:
                n = self._neutre
                for setter, val in ((self._sarra.set_jaw_gain, n["jaw_gain"]),
                                    (self._sarra.set_articulation_gain, n["articulation_gain"]),
                                    (self._sarra.set_fluidite, n["fluidite"]),
                                    (self._sarra.set_force_fermetures, n["force_fermetures"]),
                                    (self._sarra.set_amplitude, n["amplitude"]),
                                    (self._sarra.set_sync_offset, n["sync_ms"]),
                                    (self._sarra.set_seuil_silence, n["seuil_silence"])):
                    setter(val)
                self._sarra.reset_trims()
                self._status.setText("⚖  Neutre perso — compare, puis décoche.")
            else:
                jaw, art, flu, fer, amp, syn, seu = self._gains_courants()
                self._sarra.set_jaw_gain(jaw)
                self._sarra.set_articulation_gain(art)
                self._sarra.set_fluidite(flu)
                self._sarra.set_force_fermetures(fer)
                self._sarra.set_amplitude(amp)
                self._sarra.set_sync_offset(syn)
                self._sarra.set_seuil_silence(seu)
                self._appliquer_trims(self._trims)
                self._status.setText("⚖  Tes réglages — rejoue pour comparer.")
        except Exception:
            pass

    def _gains_courants(self) -> tuple:
        return (self._jaw_slider.value() / 100.0,
                self._art_slider.value() / 100.0,
                self._flu_slider.value() / 100.0,
                self._fer_slider.value() / 100.0,
                self._amp_slider.value() / 100.0,
                float(self._syn_slider.value()),
                self._seu_slider.value() / 100.0)

    def _sauver(self) -> None:
        jaw, art, flu, fer, amp, syn, seu = self._gains_courants()
        try:
            from memory.config_manager import save_lipsync_gains
            data = save_lipsync_gains(jaw, art, fluidite=flu,
                                      force_fermetures=fer, amplitude=amp,
                                      sync_ms=syn, seuil_silence=seu,
                                      visemes=self._trims,
                                      humeurs=self._humeurs,
                                      autocalib=self._auto_params,
                                      neutre=self._neutre,
                                      plages=self._plages)
            self._saved = {"jaw_gain": data["jaw_gain"],
                           "articulation_gain": data["articulation_gain"],
                           "fluidite": data["fluidite"],
                           "force_fermetures": data["force_fermetures"],
                           "amplitude": data["amplitude"],
                           "sync_ms": data["sync_ms"],
                           "seuil_silence": data["seuil_silence"],
                           "visemes": dict(data.get("visemes", {})),
                           "humeurs": dict(data.get("humeurs", {})),
                           "autocalib": dict(data.get("autocalib", {})),
                           "neutre": dict(data.get("neutre", {})),
                           "plages": {k: list(v) for k, v in
                                      data.get("plages", {}).items()}}
            self._status.setText("✓ Sauvegardé — appliqué à chaque démarrage.")
        except Exception as e:
            self._status.setText(f"Échec sauvegarde : {e}")

    def _fermer(self) -> None:
        # Fermer sans sauver = on restaure les gains + trims sauvés.
        # Coupe aussi une auto-calibration en cours (fond témoin retiré).
        self._calib_en_cours = False
        try:
            if self._pont_actif():
                self._sarra.set_jaw_gain(self._saved["jaw_gain"])
                self._sarra.set_articulation_gain(self._saved["articulation_gain"])
                self._sarra.set_fluidite(self._saved["fluidite"])
                self._sarra.set_force_fermetures(self._saved["force_fermetures"])
                self._sarra.set_amplitude(self._saved["amplitude"])
                self._sarra.set_sync_offset(self._saved["sync_ms"])
                self._sarra.set_seuil_silence(self._saved["seuil_silence"])
                self._appliquer_trims(self._saved.get("visemes", {}))
                self._sarra.fond_calibration(False)
                self._sarra.relacher_visemes()
        except Exception:
            pass
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.GREEN_D};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: {C.PRI_GHO}; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(str, bool)
    _OW = 430

    def __init__(self, confirmation_id: str, title: str, detail: str, parent=None):
        super().__init__(parent)
        self._confirmation_id = confirmation_id
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(self._confirmation_id, True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(self._confirmation_id, False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: {C.PANEL2}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: {C.PANEL2}; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: {C.PANEL2}; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Courier New", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: {C.PANEL2}; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: {C.PANEL2}; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Courier New", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _video_open_sig  = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _wake_btns_sig   = pyqtSignal()          # wake state resolved off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig  = pyqtSignal(bool)
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str, str)   # (id, title, detail) — action-specific gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    _ai_test_sig    = pyqtSignal(str)

    def __init__(self, face_path: str):
        super().__init__()
        self._closed = False
        self._face_path = face_path
        self._avatar_only = False
        self._avatar_drag_pos = None
        self._avatar_system_move_active = False
        self._normal_window_geometry = None
        # Geometry persistence: only active once the floating window has been
        # restored/shown, so the initial full-UI resize is never written.
        self._avatar_geometry_ready = False
        self._avatar_geometry_save_timer = QTimer(self)
        self._avatar_geometry_save_timer.setSingleShot(True)
        self._avatar_geometry_save_timer.setInterval(300)
        self._avatar_geometry_save_timer.timeout.connect(
            self._save_avatar_window_geometry)
        # Application-level filter: the WebEngine render surface can sit above
        # the transparent drag widget, so handle the drag from the event source
        # itself instead of relying on that widget being on top.
        self._app_event_filter_installed = False
        _app = QApplication.instance()
        if _app is not None:
            _app.installEventFilter(self)
            self._app_event_filter_installed = True

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _theme = str(_cfg.get("theme", DEFAULT_THEME)).strip().lower()
        apply_ui_theme(_theme if _theme in THEMES else DEFAULT_THEME)
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_application_close = None  # callable: () -> None — persist session memory
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_directives_change = None  # callable: () -> None — reload official prompt
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        self._lipsync_overlay = None
        self._directives_overlay = None
        self._ai_panel: QWidget | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._header = self._build_header()
        root.addWidget(self._header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Avatar 3D Sarra : créé à côté du HUD 2D (visuel seul, audio inchangé).
        # En cas d'échec, self._sarra reste None et le HUD 2D est conservé.
        self._sarra = None
        self._avatar_drag_handle = None
        if HAVE_SARRA and SARRA_3D:
            try:
                self._sarra = AvatarBridge(parent=self)
                # Le bouton micro HTML de la page avatar passe par ce signal.
                self._sarra.mic_toggle.connect(self._toggle_mute)
                if self._sarra.widget is not None:
                    # Surface de déplacement : tiers central du visage (la zone
                    # nez/bouche attrape le déplacement). Ctrl+glisser et la
                    # molette traversent vers la page (orbite / zoom conservés).
                    self._install_avatar_drag_handle(self._sarra.widget)
            except Exception:
                self._sarra = None
                self._avatar_drag_handle = None
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # ── Video surface ────────────────────────────────────────────────
        # Built exactly like the camera page above and stacked beside it,
        # because it is the same idea: something takes the centre of the HUD
        # for a while and then gives it back. Sharing the stack rather than
        # inventing a second mechanism means the avatar, the camera and a video
        # can never be on screen at once.
        self._video_split = False        # is the sound a separate stream?
        self._video_auto_muted = False   # did JARVIS close the mic, or the user?
        # A plain flag rather than reading the widget. video_is_playing() is
        # called from plugin threads, and reading a widget's state from one is
        # not something to rely on; an attribute is.
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel("▶  VIDEO")
        self._video_title.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        # Muted is the default and the button says so, because a soundtrack
        # talking over JARVIS is the one way this feature could make the
        # assistant worse rather than better.
        self._video_mute_btn = _vid_btn("🔇  SOUND OFF")
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn("✕  CLOSE")
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        _vid_v.addLayout(_vid_hdr)

        if HAVE_VIDEO:
            # A GRAPHICS ITEM, NOT A QVideoWidget.
            #
            # QVideoWidget gets a native window of its own, and a native window
            # is painted by the compositor above every ordinary widget that
            # shares the screen with it — so the settings drawer opened
            # *underneath* the video and raise_() could not help, because the
            # two are not in the same painting order at all. A video item drawn
            # into a QGraphicsView goes through Qt's own painter like any other
            # widget, and anything laid over it stays over it.
            self._video_scene = QGraphicsScene(self)
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)
            self._video_widget = QGraphicsView(self._video_scene)
            self._video_widget.setStyleSheet("background: #000; border: none;")
            self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
            self._video_widget.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            _vid_v.addWidget(self._video_widget, stretch=1)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            # Keep the picture filling the view as the window is resized.
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)

            # A SECOND player, for sound that arrives separately.
            #
            # YouTube no longer serves a single stream carrying both picture and
            # sound — not for new uploads and not for old ones; checked against
            # three videos including the oldest on the site, and every one of
            # them offered zero combined formats. Picture and sound are two
            # URLs, so they are two players, started together and nudged back
            # into line by the timer below.
            #
            # A local file or a direct video URL still uses the first player
            # alone: one stream, one player, nothing to synchronise.
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            self._video_scene = None
            self._video_item = None
            self._video_widget = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None
            _miss = QLabel("Video playback is not available in this Qt install.")
            _miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _miss.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            _vid_v.addWidget(_miss, stretch=1)

        # Stack: 0 = Sarra 3D, 1 = animated HUD, 2 = live camera, 3 = video
        # --- MODIFICATION SARRA : la vue 3D passe en tête du stack ---
        self._hud_cam_stack = QStackedWidget()
        # The WebEngine page and Three.js canvas are transparent; do not let
        # the native stack paint an opaque black surface behind them.
        self._hud_cam_stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._hud_cam_stack.setAutoFillBackground(False)
        self._hud_cam_stack.setStyleSheet("background: transparent; border: none;")
        if self._sarra3d_actif():
            self._hud_cam_stack.addWidget(self._sarra.widget)  # index 0 = Sarra 3D
            self._sarra.widget.setMinimumSize(400, 400)
            self._sarra.widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            print("[Sarra] ✅ Widget 3D ajouté au stack (index 0)")
        else:
            print("[Sarra] ⚠️ Widget 3D non disponible, repli sur HUD 2D")
        # --- FIN MODIFICATION SARRA ---
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)

        # --- MODIFICATION SARRA : page initiale = l'avatar (index 0 dans les 2 modes) ---
        # Mode 3D : index 0 = vue WebEngine. Mode 2D : la vue 3D n'est pas dans
        # le stack (hud=0, caméra=1, vidéo=2), donc l'index 0 = HUD 2D.
        # L'ancien `else: setCurrentIndex(1)` affichait la page caméra vide.
        self._hud_cam_stack.setCurrentIndex(0)
        # --- FIN MODIFICATION SARRA ---
        # The 2D fallback has no WebEngine surface: parent the same drag
        # handle to the stack so the floating HUD stays draggable too.
        if self._avatar_drag_handle is None:
            self._install_avatar_drag_handle(self._hud_cam_stack)

        # --- Bouton micro posé sur l'avatar --------------------------------
        # Enfant direct du stack (au-dessus de la page WebEngine / HUD 2D) :
        # il reste cliquable dans la fenêtre avatar flottante, où le bouton
        # micro du pied de page est masqué.
        self._avatar_mic_btn: QPushButton | None = None
        self._hud_cam_stack.currentChanged.connect(self._on_hud_page_changed)
        self._create_avatar_mic_btn()

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        self._footer = self._build_footer()
        root.addWidget(self._footer)

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._ctrl_drawer = self._build_controls_drawer()
        self._ai_panel = self._build_ai_panel()
        self._warm_wake_state()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._ai_test_sig.connect(self._show_ai_test_result)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._require_key_update = False
        self._ready = self._check_config()
        self._first_setup = not self._ready
        self._setup_return_to_avatar_only = not self._ready

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        sc_intr.setAutoRepeat(False)
        sc_intr.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_intr.activated.connect(self._do_interrupt)
        self._sc_avatar_mode = QShortcut(QKeySequence("Ctrl+Shift+K"), self)
        self._sc_avatar_mode.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_avatar_mode.activated.connect(self._toggle_avatar_only)
        # Calibration labiale accessible même en avatar flottant (le panneau
        # est une fenêtre d'outil indépendante de l'interface complète).
        self._sc_lipsync = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        self._sc_lipsync.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._sc_lipsync.activated.connect(self._open_lipsync)

        # Show first-run setup beside the floating avatar, rather than as a
        # child overlay that can be painted underneath the WebEngine surface.
        self._set_avatar_only(True)
        if not self._ready:
            self._show_setup()

    def _toggle_avatar_only(self) -> None:
        self._set_avatar_only(not self._avatar_only)

    def _set_avatar_only(self, enabled: bool) -> None:
        """Switch between the floating avatar and the complete application UI."""
        if enabled == self._avatar_only and self.isVisible():
            return
        if not enabled and self._avatar_only:
            # Remember where the user left the floating avatar before the
            # window is resized back to the full interface.
            self._save_avatar_window_geometry()

        if enabled:
            self._normal_window_geometry = self.geometry()
            self._avatar_only = True
            self._avatar_geometry_ready = False
            self._avatar_geometry_save_timer.stop()
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            # In avatar-only mode the native window itself must also be
            # translucent; otherwise Qt paints the unused parts of the
            # WebEngine surface black before the page can show its alpha.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAutoFillBackground(False)
            self.setStyleSheet("background: transparent;")
            # Le widget central et le splitter peignent sinon le fond du thème
            # (opaque en thème clair) par-dessus la fenêtre translucide et
            # masquent la transparence de la vue WebEngine / Three.js.
            try:
                cw = self.centralWidget()
                if cw is not None:
                    cw.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                    cw.setAutoFillBackground(False)
                    cw.setStyleSheet("background: transparent; border: none;")
                self._center_split.setAutoFillBackground(False)
                self._center_split.setStyleSheet(
                    "QSplitter { background: transparent; border: none; }"
                    "QSplitter::handle { background: transparent; height: 0px; }"
                )
                self._hud_cam_stack.setAutoFillBackground(False)
                self._hud_cam_stack.setStyleSheet("background: transparent; border: none;")
            except Exception:
                pass
            self._header.hide()
            self._footer.hide()
            self._left_panel.hide()
            self._right_panel.hide()
            self._content_panel.hide()
            self._quiz_panel.hide()
            self._quick_drawer.hide()
            self._ctrl_drawer.hide()
            if self._ai_panel is not None:
                self._ai_panel.hide()
            self._hud_cam_stack.setMinimumSize(0, 0)
            self.setMinimumSize(_AVATAR_WIN_MIN, _AVATAR_WIN_MIN)
            # Restore the exact size and desktop position from the previous
            # run instead of always reopening at 520×620.
            avatar_geo = _load_avatar_window_geometry()
            if not self._ready:
                avatar_geo = _first_startup_avatar_geometry(avatar_geo)
            self.setGeometry(avatar_geo)
            self._center_split.setSizes([max(1, avatar_geo.width()), 0, 0])
            self._center_split.setHandleWidth(0)
            if self._avatar_drag_handle is not None:
                self._position_avatar_drag_handle()
                self._avatar_drag_handle.raise_()
                self._avatar_drag_handle.show()
            if self._sarra3d_actif():
                self._sarra.set_avatar_only_mode(True)
            self.show()
            self.raise_()
            self.activateWindow()
            self._avatar_geometry_ready = True
            self._position_avatar_mic_btn()
            if self._avatar_drag_handle is not None:
                self._position_avatar_drag_handle()
                self._avatar_drag_handle.raise_()
            QTimer.singleShot(0, self._position_avatar_mic_btn)
            QTimer.singleShot(0, self._position_avatar_drag_handle)
            # Persist what the window manager actually applied (DPI scaling,
            # screen clamping) after it has settled, not a transient geometry
            # from the transition out of the complete UI.
            QTimer.singleShot(250, self._save_avatar_window_geometry)
        else:
            self._avatar_only = False
            self._avatar_geometry_ready = False
            self._avatar_geometry_save_timer.stop()
            self._avatar_drag_pos = None
            if self._avatar_drag_handle is not None:
                self._avatar_drag_handle.hide()
            if self._sarra3d_actif():
                self._sarra.set_avatar_only_mode(False)
            self.setWindowFlags(Qt.WindowType.Window)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setAutoFillBackground(True)
            self.setStyleSheet("")
            # Restaure les fonds opaques du thème en mode interface complète.
            try:
                cw = self.centralWidget()
                if cw is not None:
                    cw.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
                    cw.setAutoFillBackground(True)
                    cw.setStyleSheet(f"background: {C.BG};")
                self._center_split.setAutoFillBackground(True)
                self._center_split.setStyleSheet(f"""
                    QSplitter::handle {{
                        background: {C.BORDER};
                        height: 4px;
                    }}
                    QSplitter::handle:hover {{
                        background: {C.PRI_DIM};
                    }}
                """)
                self._hud_cam_stack.setAutoFillBackground(False)
                self._hud_cam_stack.setStyleSheet("background: transparent; border: none;")
            except Exception:
                pass
            self.setMinimumSize(_MIN_W, _MIN_H)
            self._header.show()
            self._footer.show()
            self._left_panel.show()
            self._right_panel.show()
            self._content_panel.show()
            self._quiz_panel.show()
            self._center_split.setHandleWidth(4)
            if self._normal_window_geometry is not None:
                self.setGeometry(self._normal_window_geometry)
            else:
                self.resize(_DEFAULT_W, _DEFAULT_H)
            self.show()
            self.raise_()
            self.activateWindow()
            self._position_avatar_mic_btn()
            QTimer.singleShot(0, self._position_avatar_mic_btn)

    def _schedule_avatar_geometry_save(self) -> None:
        """Debounce writes while the user drags or resizes the avatar."""
        if (getattr(self, "_avatar_only", False)
                and getattr(self, "_avatar_geometry_ready", False)
                and getattr(self, "_avatar_geometry_save_timer", None) is not None):
            self._avatar_geometry_save_timer.start()

    def _clamp_avatar_window_geometry(self, geo: QRect) -> QRect:
        """Keep the avatar's centre on a screen, not the whole transparent box.

        The window is mostly transparent; letting its edges overhang the
        desktop lets the avatar itself reach every screen edge while keeping
        the centred drag surface reachable.
        """
        screen = QApplication.screenAt(geo.center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return geo
        return _center_avatar_rect_on_screen(geo, screen.availableGeometry())

    def _save_avatar_window_geometry(self) -> None:
        """Persist the floating avatar's size and desktop position."""
        if not getattr(self, "_avatar_only", False):
            return
        if not getattr(self, "_avatar_geometry_ready", False):
            return
        if self.isMinimized() or self.isFullScreen():
            return
        geo = self.geometry()
        if geo.width() < _AVATAR_WIN_MIN or geo.height() < _AVATAR_WIN_MIN:
            return
        data = {
            "x": geo.x(),
            "y": geo.y(),
            "width": geo.width(),
            "height": geo.height(),
        }
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = AVATAR_WINDOW_FILE.with_name(AVATAR_WINDOW_FILE.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(AVATAR_WINDOW_FILE)
        except Exception as exc:
            print(f"[UI] Géométrie avatar non enregistrée : {exc}")

    def _install_avatar_drag_handle(self, host: QWidget) -> None:
        """Transparent drag surface over the centre third of the avatar."""
        handle = QWidget(host)
        handle.setCursor(Qt.CursorShape.SizeAllCursor)
        # Fond transparent : le bureau reste visible derrière l'avatar.
        handle.setStyleSheet("background: transparent; border: none;")
        handle.hide()
        self._avatar_drag_handle = handle
        self._position_avatar_drag_handle()

    def _position_avatar_drag_handle(self) -> None:
        """Tiers central du visage : cellule centrale d'une grille 3×3."""
        handle = getattr(self, "_avatar_drag_handle", None)
        if handle is None:
            return
        host = handle.parentWidget()
        if host is None:
            return
        w, h = host.width(), host.height()
        handle.setGeometry(w // 3, h // 3, w // 3, h // 3)

    # --- Bouton micro de l'avatar ------------------------------------------
    def _create_avatar_mic_btn(self) -> None:
        """Micro on/off posé au-dessus de l'avatar.

        En 3D, le bouton est un élément HTML de index.html (seule façon
        fiable de le peindre au-dessus du QWebEngineView) et passe par
        QWebChannel. En repli 2D, c'est un enfant direct du stack, pas de la
        vue WebEngine : raise_() le place au-dessus du HUD. Le clic emprunte
        exactement le même chemin que le bouton du footer.
        """
        if getattr(self, "_avatar_mic_btn", None) is not None:
            return
        if self._sarra3d_actif():
            # En 3D, le bouton est un élément HTML de index.html : c'est la
            # seule façon fiable de le peindre au-dessus du QWebEngineView.
            self._avatar_mic_btn = None
            return
        parent = self._hud_cam_stack
        btn = QPushButton("🎙", parent)
        btn.setFixedSize(52, 52)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(QFont("Segoe UI Emoji", 20))
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAccessibleName("Micro")
        btn.clicked.connect(self._toggle_mute)
        self._avatar_mic_btn = btn
        self._style_avatar_mic_btn()
        self._position_avatar_mic_btn()
        btn.show()

    def _on_hud_page_changed(self, index: int) -> None:
        """Le bouton micro ne s'affiche que sur la page avatar (index 0)."""
        btn = getattr(self, "_avatar_mic_btn", None)
        if btn is None:
            return
        btn.setVisible(index == 0)
        if index == 0:
            self._position_avatar_mic_btn()

    def _position_avatar_mic_btn(self) -> None:
        btn = getattr(self, "_avatar_mic_btn", None)
        if btn is None:
            return
        parent = btn.parentWidget()
        if parent is None:
            return
        marge = 16
        btn.move(max(0, (parent.width() - btn.width()) // 2),
                 max(0, parent.height() - btn.height() - marge))
        btn.raise_()

    def _style_avatar_mic_btn(self) -> None:
        # En 3D le bouton est dans la page HTML : on lui pousse l'état.
        if self._sarra3d_actif():
            self._sarra.set_mic_muted(self._muted)
        btn = getattr(self, "_avatar_mic_btn", None)
        if btn is None:
            return
        if self._muted:
            couleur, fond = C.MUTED_C, "rgba(28, 0, 10, 235)"
            btn.setText("🔇")
            btn.setToolTip("Micro coupé — cliquer pour activer")
        else:
            couleur, fond = C.GREEN, "rgba(0, 24, 12, 235)"
            btn.setText("🎙")
            btn.setToolTip("Micro actif — cliquer pour couper")
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {fond}; color: {couleur};
                border: 2px solid {couleur}; border-radius: 26px;
                padding: 0;
            }}
            QPushButton:hover {{
                background: rgba(0, 46, 56, 245);
                border-color: {C.WHITE};
            }}
        """)

    def _avatar_drag_zone_contains(self, global_pos: QPoint) -> bool:
        """True inside the centred third of the floating avatar."""
        geo = self.frameGeometry()
        zone = QRect(
            geo.x() + geo.width() // 3,
            geo.y() + geo.height() // 3,
            max(1, geo.width() // 3),
            max(1, geo.height() // 3),
        )
        return zone.contains(global_pos)

    def _position_setup_overlay(self, overlay: QWidget) -> None:
        """Place setup beside the avatar (or main window) without covering it."""
        overlay.adjustSize()
        width, height = overlay.width(), overlay.height()
        anchor = self.frameGeometry()
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 16
        right_x = anchor.right() + margin
        left_x = anchor.left() - width - margin
        if right_x + width <= available.right():
            x = right_x
        elif left_x >= available.left():
            x = left_x
        else:
            x = min(max(anchor.right() + margin, available.left()),
                    available.right() - width)
        y = min(max(anchor.center().y() - height // 2, available.top()),
                available.bottom() - height)
        overlay.setGeometry(x, y, width, height)

    def eventFilter(self, watched, event):
        """Fenêtre avatar sans bordure : glisser gauche = déplacer partout.

        Ctrl+gauche et molette traversent vers la page Three.js (orbite /
        zoom conservés) ; seul le glisser gauche simple dans la zone centrale
        déplace la fenêtre. Le filtre est installé sur l'application car la
        surface de rendu WebEngine peut recouvrir le widget transparent.
        """
        if not getattr(self, "_avatar_only", False):
            return super().eventFilter(watched, event)
        # Ignore events from other top-level widgets (lipsync tool, drawers…).
        if isinstance(watched, QWidget) and watched.window() is not self:
            return super().eventFilter(watched, event)

        et = event.type()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                return False
            if not self._avatar_drag_zone_contains(event.globalPosition().toPoint()):
                return False
            self._avatar_system_move_active = False
            # Prefer the native move loop on Windows: it keeps working even
            # when the WebEngine child swallows MouseMove events. The manual
            # path below stays as a fallback for platforms without it.
            win = self.windowHandle()
            if _OS == "Windows" and win is not None and hasattr(win, "startSystemMove"):
                try:
                    if win.startSystemMove():
                        self._avatar_drag_pos = None
                        self._avatar_system_move_active = True
                        return True
                except Exception:
                    pass
            self._avatar_drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            return True

        if et == QEvent.Type.MouseMove and self._avatar_drag_pos is not None:
            if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
                self._avatar_drag_pos = None
                return False
            target = event.globalPosition().toPoint() - self._avatar_drag_pos
            target = self._clamp_avatar_window_geometry(
                QRect(target, self.size())).topLeft()
            self.move(target)
            return True

        if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._avatar_drag_pos is not None or self._avatar_system_move_active:
                self._avatar_drag_pos = None
                self._avatar_system_move_active = False
                self._save_avatar_window_geometry()
                return True
        return super().eventFilter(watched, event)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    def _sarra3d_actif(self) -> bool:
        """True si la vue 3D est voulue (SARRA_3D) ET disponible.

        Point de passage unique pour tout l'affichage : choix 2D => False
        partout, les index du stack retombent sur hud=0, caméra=1, vidéo=2.
        """
        try:
            return (bool(SARRA_3D) and getattr(self, "_sarra", None) is not None
                    and self._sarra.widget is not None)
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(
                2 if self._sarra3d_actif() else 1
            )
        else:
            self._hud_cam_stack.setCurrentIndex(
                0 if self._sarra3d_actif() else 0
            )
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(self, source: str, title: str, muted: bool,
                       audio_source: str = "") -> None:
        if not HAVE_VIDEO or not self._video_player:
            self.write_log("SYS: Video playback is not available in this Qt "
                           "install.")
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        self._video_title.setText(f"▶  {(title or 'VIDEO')[:44].upper()}")
        self._video_split = bool(audio_source)
        self._set_video_muted(bool(muted))

        url = (QUrl.fromLocalFile(source) if Path(source).exists()
               else QUrl(source))
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(
            3 if self._sarra3d_actif() else 2
        )
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _fit_video(self, *_a) -> None:
        """Size the picture to the panel, keeping its shape."""
        if not (self._video_item and self._video_widget):
            return
        try:
            native = self._video_item.nativeSize()
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(),
                        view.height() / native.height())
            w, h = native.width() * scale, native.height() * scale
            self._video_item.setSize(QSizeF(w, h))
            self._video_scene.setSceneRect(0, 0, w, h)
            self._video_widget.centerOn(self._video_item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._hud_cam_stack.setCurrentIndex(
            0 if self._sarra3d_actif() else 0
        )
        self._sync_mic_for_video()      # gives the microphone back

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        JARVIS subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if JARVIS closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(self._video_on and self._video_sound_out
                        and not self._video_sound_out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close "
                                  "it, or press F4 to talk. Typing still works.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText("🔇  SOUND OFF" if muted else "🔊  SOUND ON")

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self.write_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def moveEvent(self, event):
        super().moveEvent(event)
        if (getattr(self, "_avatar_only", False)
                and getattr(self, "_avatar_geometry_ready", False)):
            geo = self.geometry()
            clamped = self._clamp_avatar_window_geometry(geo)
            if clamped != geo:
                self.setGeometry(clamped)
            self._schedule_avatar_geometry_save()

    def closeEvent(self, event):
        self._closed = True
        if callable(self.on_application_close):
            try:
                self.on_application_close()
            except Exception as exc:
                print(f"[Memory] ⚠️ Application-close persistence failed: {exc}")
        self._save_avatar_window_geometry()
        self._avatar_drag_pos = None
        self._avatar_system_move_active = False
        if getattr(self, "_app_event_filter_installed", False):
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._app_event_filter_installed = False
        super().closeEvent(event)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_avatar_geometry_save()
        cw = self.centralWidget()
        self._position_avatar_mic_btn()
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # CustomizeOverlay est une fenêtre d'outil : positionnée à l'ouverture,
        # jamais re-ancrée (un panneau déplacé par l'utilisateur le reste).
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Drawers are Tool windows now: positioned on open, never yanked on
        # parent resize (a user-dragged panel stays where it was put).
        if (getattr(self, "_avatar_drag_handle", None) is not None
                and self._avatar_drag_handle.isVisible()):
            self._position_avatar_drag_handle()
            self._avatar_drag_handle.raise_()
        self._fit_video()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setStyleSheet(f"background: {C.DARK}; border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(APP_VERSION, C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Paramètres et contrôles")
        self._drawer_btn.setAccessibleName("Ouvrir les paramètres")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.setToolTip("Paramètres — configuration et personnalisation")
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)

        # A SECOND drawer, and the split is by how often a thing is touched.
        #
        # One panel held twelve buttons: the ones you press once when you set
        # the assistant up sitting next to the ones you flick on and off every
        # day. Separating them by that — setup behind ⚙, everyday behind 🎛 —
        # is what makes each list short enough to read at a glance.
        self._ctrl_btn = QPushButton("🎛")
        self._ctrl_btn.setFixedSize(26, 26)
        self._ctrl_btn.setFont(QFont("Courier New", 11))
        self._ctrl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ctrl_btn.setToolTip("Controls — the everyday switches")
        self._ctrl_btn.setStyleSheet(self._drawer_btn.styleSheet())
        self._ctrl_btn.setCheckable(True)
        self._ctrl_btn.clicked.connect(self._toggle_controls)
        lay.addSpacing(4)
        lay.addWidget(self._ctrl_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(QFont("Courier New", 17, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(self._title_lbl)
        _sub_text = ("A Friendly Assistant"
                     if _disp in ("JARVIS", "J.A.R.V.I.S")
                     else "Personal AI Assistant")
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Courier New", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("PROTOCOL\n" + APP_PROTOCOL,   C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  INTERRUPT  [SPACE]")
        self._interrupt_btn.setFixedHeight(34)
        self._interrupt_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: #140008; color: {C.MUTED_C};
                border: 1px solid {C.MUTED_C}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: #200010; border: 1px solid #ff6688;
            }}
            QPushButton:pressed {{
                background: #300018;
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _faire_fenetre_outil(self, w: QWidget, titre: str,
                             on_close=None) -> QWidget:
        """Convertit un panneau flottant en vraie fenêtre d'outil.

        Avant : enfant du widget central — la surface native WebEngine de
        l'avatar 3D peignait par-dessus, et le panneau n'était pas déplaçable.
        Après : fenêtre Tool (hors barre des tâches, toujours au-dessus de la
        fenêtre parente) avec barre de titre native → déplaçable et fermable.
        Positionnée à l'ouverture, jamais re-ancrée ensuite : un panneau
        déplacé par l'utilisateur le reste. La croix native repasse par
        `on_close` pour garder le bouton d'en-tête synchronisé.
        """
        w.setParent(self)
        w.setWindowFlags(Qt.WindowType.Tool)
        w.setWindowTitle(titre)
        if on_close is not None:
            # Fonction dédiée (pas un lambda à corps tuple : le retour tuple
            # fait planter le dispatcher d'événements — vérifié en offscreen).
            def _fermer(ev, _f=on_close):
                _f()
                ev.accept()
            w.closeEvent = _fermer
        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        self._BTN_PRI, self._BTN_DIM = _BTN_STYLE_PRI, _BTN_STYLE_DIM
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()
        self._faire_fenetre_outil(w, "Paramètres Sarra", self._close_setup)

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ PARAMÈTRES")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        gemini_key_btn = QPushButton("🔑  GEMINI API KEY")
        gemini_key_btn.setFixedHeight(30)
        gemini_key_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        gemini_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gemini_key_btn.setStyleSheet(_BTN_STYLE_PRI)
        gemini_key_btn.setToolTip("Configurer ou remplacer la clé Gemini")
        gemini_key_btn.clicked.connect(self._open_gemini_setup)
        lay.addWidget(gemini_key_btn)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(5)
        theme_lbl = QLabel("THEME")
        theme_lbl.setFont(QFont("Courier New", 7))
        theme_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        theme_row.addWidget(theme_lbl)
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("CLAIR", "light")
        self._theme_combo.addItem("SOMBRE", "dark")
        self._theme_combo.setFont(QFont("Courier New", 7))
        self._theme_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 5px;
            }}
            QComboBox:hover {{ border-color: {C.BORDER_B}; }}
            QComboBox QAbstractItemView {{ background: {C.PANEL}; color: {C.TEXT}; }}
        """)
        saved_theme = str(_read_full_config().get("theme", DEFAULT_THEME)).lower()
        self._theme_combo.setCurrentIndex(1 if saved_theme == "dark" else 0)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self._theme_combo, 1)
        lay.addLayout(theme_row)

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        ai_btn = QPushButton("🧠  IA — PROVIDERS & MOTEURS")
        ai_btn.setFixedHeight(30)
        ai_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        ai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ai_btn.setStyleSheet(_BTN_STYLE_PRI)
        ai_btn.setToolTip("Configurer les providers et moteurs de raisonnement IA")
        ai_btn.clicked.connect(self._toggle_ai_panel)
        lay.addWidget(ai_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        lipsync_btn = QPushButton("👄  LIPSYNC — CALIBRATION LABIALE")
        lipsync_btn.setFixedHeight(30)
        lipsync_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lipsync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lipsync_btn.setStyleSheet(_BTN_STYLE_PRI)
        lipsync_btn.clicked.connect(self._open_lipsync)
        lay.addWidget(lipsync_btn)

        w.adjustSize()
        return w

    def _on_theme_changed(self, index: int) -> None:
        theme = self._theme_combo.itemData(index) or DEFAULT_THEME
        old = current_palette()
        apply_ui_theme(str(theme))
        # Re-apply the user's accent on top of the new base palette.
        saved_color = str(_read_full_config().get("ui_color", "")).strip()
        if saved_color:
            apply_ui_accent(saved_color)
        retheme_all_widgets(old, current_palette())
        try:
            data = _read_full_config()
            data["theme"] = str(theme)
            API_FILE.parent.mkdir(parents=True, exist_ok=True)
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Theme set — {str(theme).upper()}")
        except Exception as e:
            self._log.append_log(f"ERR: Theme save failed — {e}")

    def _build_ai_panel(self) -> QWidget:
        """Floating native panel for reasoning-provider configuration."""
        w = QWidget(self.centralWidget())
        w.setObjectName("AiPanel")
        w.setStyleSheet(f"""
            QWidget#AiPanel {{
                background: {C.DARK}; border: 1px solid {C.PRI};
                border-radius: 0 0 6px 6px;
            }}
            QLabel {{ background: transparent; color: {C.TEXT_MED}; }}
            QComboBox, QTextEdit {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px;
            }}
            QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; }}
        """)
        w.hide()
        self._faire_fenetre_outil(w, "IA — Providers & moteurs", self._close_ai_panel)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QHBoxLayout()
        title = QLabel("◈ CONFIGURATION DES IA")
        title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close = QPushButton("✕")
        close.setFixedSize(22, 22)
        close.setStyleSheet(self._BTN_DIM)
        close.clicked.connect(self._close_ai_panel)
        hdr.addWidget(close)
        lay.addLayout(hdr)

        voice = QLabel("VOIX (GEMINI) — lecture seule : Gemini — TTS/STT uniquement")
        voice.setWordWrap(True)
        voice.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        lay.addWidget(voice)
        self._ai_active_lbl = QLabel("Raisonnement actif : — | Voix : Gemini")
        self._ai_active_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lay.addWidget(self._ai_active_lbl)

        self._ai_provider_combo = QComboBox()
        self._ai_provider_combo.setFont(QFont("Courier New", 8))
        lay.addWidget(self._ai_provider_combo)

        def field(placeholder: str, password: bool = False):
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFont(QFont("Courier New", 8))
            if password:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            lay.addWidget(edit)
            return edit

        self._ai_key = field("CLÉ API — jamais affichée dans les logs", True)
        key_row = QHBoxLayout()
        key_btn = QPushButton("👁")
        free_btn = QPushButton("Obtenir une clé gratuite")
        for button in (key_btn, free_btn):
            button.setFont(QFont("Courier New", 7))
            button.setStyleSheet(self._BTN_DIM)
            key_row.addWidget(button)
        key_btn.clicked.connect(lambda: self._ai_key.setEchoMode(QLineEdit.EchoMode.Normal if self._ai_key.echoMode() == QLineEdit.EchoMode.Password else QLineEdit.EchoMode.Password))
        free_btn.clicked.connect(self._open_ai_key_url)
        lay.addLayout(key_row)
        self._ai_base_url = field("BASE URL")
        self._ai_model = field("MODÈLE")
        self._ai_temperature = field("TEMPÉRATURE (0 à 0.1)")
        self._ai_timeout = field("TIMEOUT (secondes)")
        self._ai_strict = QCheckBox("Mode rigoureux — deux passes")
        self._ai_strict.setChecked(True)
        self._ai_strict.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(self._ai_strict)

        row = QHBoxLayout()
        select = QPushButton("ENREGISTRER")
        refresh = QPushButton("MODÈLES")
        test = QPushButton("TESTER LA CONNEXION")
        for button in (select, refresh, test):
            button.setFixedHeight(26)
            button.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(self._BTN_DIM)
            row.addWidget(button)
        select.clicked.connect(self._select_ai_provider)
        refresh.clicked.connect(self._refresh_ai_panel)
        test.clicked.connect(self._test_ai_provider)
        lay.addLayout(row)

        self._ai_status = QTextEdit()
        self._ai_status.setReadOnly(True)
        self._ai_status.setFont(QFont("Courier New", 7))
        self._ai_status.setFixedHeight(130)
        lay.addWidget(self._ai_status)
        w.adjustSize()
        return w

    def _reasoning_status(self) -> dict:
        from core.reasoning_router import status
        return status()

    def _refresh_ai_panel(self):
        try:
            data = self._reasoning_status()
            active = data.get("active") or "aucun"
            self._ai_active_lbl.setText(f"Raisonnement : {active} | Voix : Gemini")
            self._ai_provider_combo.clear()
            lines = []
            for provider in data.get("providers", []):
                name = provider.get("name", "")
                label = provider.get("label", name)
                configured = "clé OK" if provider.get("configured") else "clé absente"
                marker = " ★" if provider.get("active") else ""
                self._ai_provider_combo.addItem(f"{label} ({name}) — {configured}{marker}", name)
                lines.append(f"{name}: {configured}{' — actif' if provider.get('active') else ''}")
            current = next((p for p in data.get("providers", []) if p.get("active")), None)
            if current:
                from core.reasoning_router import get_settings
                settings = get_settings(str(current["name"]))
                self._ai_base_url.setText(settings["base_url"])
                self._ai_model.setText(settings["model"])
                self._ai_temperature.setText(str(settings["temperature"]))
                self._ai_timeout.setText(str(settings["timeout"]))
                self._ai_strict.setChecked(settings["strict_mode"])
                self._ai_key.clear()
            self._ai_status.setPlainText("\n".join(lines) or "Aucun provider configuré.")
        except Exception as exc:
            self._ai_status.setPlainText(f"Erreur de lecture des providers : {exc}")

    def _toggle_ai_panel(self):
        if self._ai_panel.isVisible():
            self._close_ai_panel()
            return
        self._close_setup()
        self._close_controls()
        self._refresh_ai_panel()
        self._position_ai_panel()
        self._ai_panel.show()
        self._ai_panel.raise_()

    def _close_ai_panel(self):
        if self._ai_panel:
            self._ai_panel.hide()

    def _position_ai_panel(self):
        self._place_drawer(self._ai_panel, 12)

    def _select_ai_provider(self):
        name = self._ai_provider_combo.currentData()
        if not name:
            return
        try:
            from core.reasoning_router import save_settings
            save_settings(str(name), {
                "api_key": self._ai_key.text(),
                "base_url": self._ai_base_url.text(),
                "model": self._ai_model.text(),
                "temperature": float(self._ai_temperature.text() or 0),
                "timeout": int(self._ai_timeout.text() or 45),
                "strict_mode": self._ai_strict.isChecked(),
            })
            self._refresh_ai_panel()
            self._log.append_log(f"SYS: Raisonnement configuré : {name}")
        except Exception as exc:
            self._ai_status.setPlainText(f"Activation impossible : {exc}")

    def _test_ai_provider(self):
        name = self._ai_provider_combo.currentData()
        if not name:
            return
        self._ai_status.setPlainText(f"Test de {name} en cours…")
        def work():
            try:
                from core.reasoning_router import list_models, save_settings, _call
                save_settings(str(name), {
                    "api_key": self._ai_key.text(),
                    "base_url": self._ai_base_url.text(),
                    "model": self._ai_model.text(),
                    "temperature": float(self._ai_temperature.text() or 0),
                    "timeout": int(self._ai_timeout.text() or 45),
                    "strict_mode": self._ai_strict.isChecked(),
                })
                models = list_models(str(name), self._ai_key.text().strip(), self._ai_base_url.text().strip(), 20.0)
                result = _call(str(name), "Réponds avec un objet JSON facts/inferences/unknowns/confidence.", 20.0)
                message = f"{name}: connexion OK — {len(models)} modèles, réponse validée : {result[:160]}"
            except Exception as exc:
                message = f"{name}: échec — {exc}"
            self._ai_test_sig.emit(message)
        threading.Thread(target=work, daemon=True, name="ai-provider-test").start()

    def _open_ai_key_url(self):
        try:
            from core.reasoning_router import provider_spec
            name = self._ai_provider_combo.currentData()
            url = provider_spec(str(name)).get("free_key_url", "")
            if url:
                webbrowser.open(url)
        except Exception as exc:
            self._ai_status.setPlainText(f"Lien de clé indisponible : {exc}")

    def _show_ai_test_result(self, message: str):
        if self._ai_panel and self._ai_panel.isVisible():
            self._ai_status.setPlainText(message)

    def _build_controls_drawer(self) -> QWidget:
        """The switches that get flicked every day, in their own panel."""
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(self._quick_drawer.styleSheet())
        w.hide()
        self._faire_fenetre_outil(w, "Controls", self._close_controls)

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        def _row(button, style=None, height=26):
            button.setFixedHeight(height)
            button.setFont(QFont("Courier New", 7))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if style:
                button.setStyleSheet(style)
            lay.addWidget(button)
            return button

        fs_btn = _row(QPushButton("⛶  FULLSCREEN  [F11]"), self._BTN_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)

        self._brief_btn = _row(QPushButton())
        self._brief_btn.clicked.connect(self._toggle_brief)

        self._wake_btn = _row(QPushButton())
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        self._wake_sleep_btn = _row(QPushButton())
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        # Neutral until the real state lands from the background warm-up.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(self._BTN_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = _row(QPushButton())
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        self._refresh_talk_btns()

        self._hud_btn = _row(QPushButton())
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        self._refresh_hud_btn()

        # --- MODIFICATION HABILLAGE : option visible du vêtement PNG ---
        self._garment_btn = _row(QPushButton())
        self._garment_btn.clicked.connect(self._choisir_habillage)
        self._refresh_garment_btn()
        # --- FIN MODIFICATION HABILLAGE ---

        # --- MODIFICATION PORTRAIT : bouton photo photoréaliste ---
        self._photo_btn = _row(QPushButton())
        self._photo_btn.clicked.connect(self._choisir_portrait)
        self._refresh_photo_btn()
        # --- FIN MODIFICATION PORTRAIT ---

        w.adjustSize()
        return w

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """
        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._close_ai_panel()
            self._close_controls()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _open_gemini_setup(self):
        self._close_setup()
        self._show_setup()

    def _toggle_controls(self, checked: bool):
        if checked:
            self._close_ai_panel()
            self._close_setup()
            self._refresh_wake_btns()   # cheap now: the state was warmed at boot
            self._position_ctrl_drawer()
            self._ctrl_drawer.show()
            self._ctrl_drawer.raise_()
        else:
            self._ctrl_drawer.hide()

    def _close_setup(self):
        if hasattr(self, "_quick_drawer"):
            self._quick_drawer.hide()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        if hasattr(self, "_ctrl_btn"):
            self._ctrl_btn.setChecked(False)

    def _place_drawer(self, drawer, left: int):
        # Fenêtres d'outils = coordonnées écran (globales), calées à l'ouverture
        # sous le bouton d'en-tête correspondant, sans jamais sortir de l'écran.
        _W = 430 if drawer is getattr(self, "_ai_panel", None) else 220
        drawer.setFixedWidth(_W)
        drawer.adjustSize()
        scr = QApplication.primaryScreen().availableGeometry()
        h = min(drawer.sizeHint().height() or 200, max(200, scr.height() - 40))
        pt = self.centralWidget().mapToGlobal(QPoint(left, 54))
        x = min(max(pt.x(), scr.left()), max(scr.left(), scr.right() - _W))
        y = min(max(pt.y(), scr.top()), max(scr.top(), scr.bottom() - h))
        drawer.setGeometry(x, y, _W, h)

    def _centrer_fenetre_outil(self, w: QWidget, ow: int, oh: int) -> None:
        """Centrage d'une fenêtre d'outil sur la fenêtre principale (coordonnées
        écran), sans jamais dépasser l'écran disponible."""
        scr = QApplication.primaryScreen().availableGeometry()
        ow = min(ow, scr.width() - 20)
        oh = min(oh, scr.height() - 40)
        c = self.frameGeometry().center()
        w.setGeometry(
            min(max(c.x() - ow // 2, scr.left()), scr.right() - ow),
            min(max(c.y() - oh // 2, scr.top()), scr.bottom() - oh),
            ow, oh,
        )

    def _position_quick_drawer(self):
        if hasattr(self, "_quick_drawer"):
            self._place_drawer(self._quick_drawer, 12)

    def _position_ctrl_drawer(self):
        # Under its own header button rather than under the other one's.
        if hasattr(self, "_ctrl_drawer"):
            left = 12
            try:
                left = max(12, self._ctrl_btn.mapTo(self.centralWidget(),
                                                    QPoint(0, 0)).x() - 8)
            except Exception:
                pass
            self._place_drawer(self._ctrl_drawer, left)

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        if self._avatar_only:
            return
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        if self._avatar_only:
            return
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if self._avatar_only:
            return
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("By FatihMakes", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.GREEN_D};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: {C.PRI_GHO}; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.GREEN_D};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.GREEN_D};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        face = get_hud_style() == "face"
        # Neither state is "off", so both read as active — this is a choice
        # between two things, not a switch with a disabled side.
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText("🧑  HUD: ANIMATED FACE" if face
                              else "◉  HUD: REACTOR CORE")
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(
            "An animated head that speaks your words and shows what JARVIS is "
            "doing. Tap to switch to the reactor core."
            if face else
            "A reactor core that turns with the state and moves with your voice. "
            "Tap to switch to the animated head.")

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style
        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face." if want == "face"
            else "SYS: HUD switched to the reactor core.")

    # --- MODIFICATION HABILLAGE : option visible du vêtement PNG ---
    def _habillage_porte(self) -> bool:
        """True si un habillage est (ou sera au prochain rendu) affiché."""
        try:
            av = getattr(self.hud, "_avatar", None)
            if av is not None and av.has_garment:
                return True
        except Exception:
            pass
        try:
            from memory.config_manager import get_garment_path
            choix = get_garment_path()
            if choix:
                return True
            if choix is None and (BASE_DIR / "assets" / "avatar" / "garment.png").is_file():
                return True
        except Exception:
            pass
        return False

    def _refresh_garment_btn(self):
        """Libellé du bouton habillage : choisir un PNG, ou retirer celui porté."""
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        porte = self._habillage_porte()
        self._garment_btn.setText("👔  HABILLAGE: RETIRER" if porte
                                  else "👔  HABILLAGE: CHOISIR PNG")
        self._garment_btn.setStyleSheet(style)
        self._garment_btn.setToolTip(
            "Un vêtement est affiché. Cliquez pour le retirer."
            if porte else
            "Habillez le visage avec une image PNG à fond transparent "
            "(haut-centre de l'image = base du cou).")

    def _choisir_habillage(self):
        """Un clic = choisir un PNG si déshabillé, retirer le vêtement si porté."""
        from memory.config_manager import save_garment_path
        try:
            av = getattr(self.hud, "_avatar", None)
        except Exception:
            av = None
        if self._habillage_porte():
            # Retrait explicite et mémorisé ("") : l'auto-chargement ne
            # ramènera pas le fichier par défaut au prochain rendu.
            try:
                if av is not None:
                    av.clear_garment()
            except Exception:
                pass
            try:
                save_garment_path("")
            except Exception:
                pass
            self._refresh_garment_btn()
            try:
                self.hud.update()
                self._log.append_log("SYS: Habillage retiré. Re-cliquez pour choisir un PNG.")
            except Exception:
                pass
            return
        # Choix d'un PNG (annuler = ne rien changer).
        try:
            chemin, _ = QFileDialog.getOpenFileName(
                self, "Choisir un habillage PNG",
                str(BASE_DIR / "assets" / "avatar"), "Images PNG (*.png)")
        except Exception:
            return
        if not chemin:
            return
        try:
            ok = av.set_garment(chemin) if av is not None else False
        except Exception:
            ok = False
        if ok:
            try:
                save_garment_path(chemin)
            except Exception:
                pass
            try:
                self._log.append_log(
                    f"SYS: Habillage chargé : {Path(chemin).name}. Re-cliquez pour le retirer.")
            except Exception:
                pass
        else:
            try:
                self._log.append_log("SYS: PNG illisible, habillage inchangé.")
            except Exception:
                pass
        self._refresh_garment_btn()
        try:
            self.hud.update()
        except Exception:
            pass
    # --- FIN MODIFICATION HABILLAGE ---

    # --- MODIFICATION PORTRAIT : option visible du portrait photoréaliste ---
    def _portrait_affiche(self) -> bool:
        """True si un portrait est (ou sera au prochain rendu) affiché."""
        try:
            av = getattr(self.hud, "_avatar", None)
            if av is not None and av.has_photo:
                return True
        except Exception:
            pass
        try:
            from memory.config_manager import get_photo_path
            choix = get_photo_path()
            if choix:
                return True
            if choix is None and (BASE_DIR / "assets" / "avatar" / "face.png").is_file():
                return True
        except Exception:
            pass
        return False

    def _refresh_photo_btn(self):
        """Libellé du bouton portrait : choisir un PNG, ou revenir au procédural."""
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        affiche = self._portrait_affiche()
        self._photo_btn.setText("🖼  PORTRAIT: PROCÉDURAL" if affiche
                                else "🖼  PORTRAIT: PHOTO PNG")
        self._photo_btn.setStyleSheet(style)
        self._photo_btn.setToolTip(
            "Un portrait photo est affiché (bouche animée par le lip-sync). "
            "Cliquez pour revenir à la tête holographique."
            if affiche else
            "Affichez un portrait photoréaliste (PNG) à la place de la tête "
            "holographique. La bouche reste animée par le lip-sync.")

    def _choisir_portrait(self):
        """Un clic = choisir un portrait si procédural, revenir au procédural si photo."""
        from memory.config_manager import save_photo_path
        try:
            av = getattr(self.hud, "_avatar", None)
        except Exception:
            av = None
        if self._portrait_affiche():
            # Retrait explicite et mémorisé ("") : l'auto-chargement ne
            # ramènera pas face.png au prochain rendu.
            try:
                if av is not None:
                    av.clear_photo()
            except Exception:
                pass
            try:
                save_photo_path("")
            except Exception:
                pass
            self._refresh_photo_btn()
            try:
                self.hud.update()
                self._log.append_log("SYS: Portrait retiré, tête procédurale. Re-cliquez pour choisir un PNG.")
            except Exception:
                pass
            return
        try:
            chemin, _ = QFileDialog.getOpenFileName(
                self, "Choisir un portrait PNG",
                str(BASE_DIR / "assets" / "avatar"), "Images PNG (*.png)")
        except Exception:
            return
        if not chemin:
            return
        try:
            ok = av.set_photo(chemin) if av is not None else False
        except Exception:
            ok = False
        if ok:
            try:
                save_photo_path(chemin)
            except Exception:
                pass
            try:
                self._log.append_log(
                    f"SYS: Portrait affiché : {Path(chemin).name} (bouche animée). "
                    "Si la bouche est décalée, ajustez via set_photo_geometry().")
            except Exception:
                pass
        else:
            try:
                self._log.append_log("SYS: PNG illisible, portrait inchangé.")
            except Exception:
                pass
        self._refresh_photo_btn()
        try:
            self.hud.update()
        except Exception:
            pass
    # --- FIN MODIFICATION PORTRAIT ---

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.GREEN_D};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: {C.PRI_GHO}; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_directives(self, text: str | None = None):
        """Éditeur des directives système, en fenêtre d'outil déplaçable.

        `text` carries the settings panel's current (possibly unsaved) content
        so expanding to the large window never discards an edit.
        """
        if getattr(self, "_directives_overlay", None):
            try:
                self._directives_overlay.hide()
            except Exception:
                pass
        ov = DirectivesOverlay(parent=self.centralWidget(), text=text)
        ov.saved.connect(self._on_directives_saved_text)
        self._faire_fenetre_outil(ov, "Core Directives", ov.hide)
        self._centrer_fenetre_outil(ov, DirectivesOverlay._OW,
                                    DirectivesOverlay._OH)
        ov.show()
        ov.raise_()
        self._directives_overlay = ov

    def _open_lipsync(self):
        if getattr(self, "_lipsync_overlay", None):
            try:
                self._lipsync_overlay.hide()
            except Exception:
                pass
        sarra = self._sarra if self._sarra3d_actif() else None
        ov = LipsyncOverlay(sarra=sarra, parent=self.centralWidget())
        # Aligne le direct sur le fichier : après un redémarrage le pont a les
        # neutres alors que le fichier peut porter une calibration.
        try:
            if sarra is not None and sarra.disponible:
                g = ov._saved
                sarra.set_jaw_gain(g["jaw_gain"])
                sarra.set_articulation_gain(g["articulation_gain"])
                sarra.set_fluidite(g["fluidite"])
                sarra.set_force_fermetures(g["force_fermetures"])
                sarra.set_amplitude(g["amplitude"])
                sarra.set_sync_offset(g["sync_ms"])
                sarra.set_seuil_silence(g["seuil_silence"])
                sarra.reset_trims()
                for vid, t in (g.get("visemes", {}) or {}).items():
                    for comp, val in (t or {}).items():
                        sarra.set_trim_viseme(int(vid), comp, val)
        except Exception:
            pass
        # Fenêtre d'outil déplaçable (au-dessus de l'avatar) ; la croix
        # restaure les gains sauvés, comme FERMER.
        self._faire_fenetre_outil(ov, "Lipsync — calibration labiale",
                                  ov._fermer)
        ov.adjustSize()
        self._centrer_fenetre_outil(ov, ov.width(), ov.height())
        ov.show()
        ov.raise_()
        self._lipsync_overlay = ov

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        # Fenêtre d'outil déplaçable (au-dessus de l'avatar), centrée sur
        # l'appli à l'ouverture ; la croix native annule comme CANCEL.
        self._faire_fenetre_outil(ov, "Customise Assistant", ov._cancel)
        self._centrer_fenetre_outil(ov, ow, oh)
        ov.on_preview = self._preview_ui_color
        ov.on_directives = self._open_directives
        ov.saved.connect(self._apply_name_update)
        ov.directives_saved.connect(self._on_directives_saved)
        ov.show()
        ov.raise_()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _on_directives_saved_text(self, text: str) -> None:
        """A save from the large editor: mirror it into the open panel."""
        if self._customize_overlay:
            try:
                self._customize_overlay.set_directives(text)
            except Exception:
                pass
        self._on_directives_saved()

    def _on_directives_saved(self) -> None:
        """The official prompt changed on disk.

        main.py re-reads core/prompt.txt in _build_config(), so the running
        Live session has to be rebuilt once for the new instructions to take
        effect. The rebuild starts a fresh conversation: resuming could restore
        the old system instruction, which would make the save look ignored.
        """
        self._log.append_log(
            "SYS: Core directives enregistrées — prompt officiel mis à jour.")
        if self.on_directives_change:
            try:
                self.on_directives_change()
            except Exception as e:
                self._log.append_log(f"ERR: Directives reload failed — {e}")

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, confirmation_id: str, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(confirmation_id, title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, confirmation_id: str, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted), confirmation_id)
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        if muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted." + (f" {note}" if note else ""))
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active." + (f" {note}" if note else ""))

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)
        self._style_avatar_mic_btn()

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            key = d.get("gemini_api_key", "")
            return (
                isinstance(key, str)
                and bool(key.strip())
                and d.get("os_system") in {"windows", "mac", "linux"}
            )
        except Exception:
            return False

    def _show_setup(self):
        if self._overlay is not None and self._overlay.isVisible():
            if self._require_key_update:
                self._overlay.require_new_key()
                self._require_key_update = False
            self._overlay.raise_()
            self._overlay.activateWindow()
            return
        if self._avatar_only and not self._setup_return_to_avatar_only:
            self._setup_return_to_avatar_only = True
            self._set_avatar_only(False)
        ov = SetupOverlay(self, require_new_key=self._require_key_update)
        self._require_key_update = False
        ov.setFixedSize(820, 600)
        ov.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        ov.setWindowTitle("Sarra — Première initialisation")
        self._position_setup_overlay(ov)
        ov.done.connect(self._on_setup_done)
        ov.show()
        ov.raise_()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        key = str(key or "").strip()
        if not key:
            if self._overlay is not None:
                self._overlay.report_save_error(
                    "La clé API Gemini est requise pour continuer."
                )
            return
        if os_name not in {"windows", "mac", "linux"}:
            if self._overlay is not None:
                self._overlay.report_save_error(
                    "Le système sélectionné n’est pas pris en charge."
                )
            return

        data = _read_full_config()
        data.update({"gemini_api_key": key, "os_system": os_name})
        data.setdefault("voice_name", "Aoede")
        data.setdefault("assistant_name", "Sarra")
        data.setdefault("user_name", "")
        data.setdefault("ui_color", "#00d4ff")
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            API_FILE.write_text(
                json.dumps(data, indent=4),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as error:
            message = f"Impossible d’enregistrer la configuration : {error}"
            if self._overlay is not None:
                self._overlay.report_save_error(message)
            else:
                self._log.append_log(f"ERR: {message}")
            return

        self._ready = True
        self._apply_state("LISTENING")
        self._assistant_name = data.get("assistant_name", "Sarra") or "Sarra"
        self._log.append_log(
            f"SYS: Bienvenue chez Sarra — configuration enregistrée. "
            f"OS={os_name.upper()}. L’interface est prête."
        )
        if self._overlay is not None:
            self._overlay.complete_initialization()
            QTimer.singleShot(900, self._finish_setup_transition)
        else:
            self._finish_setup_transition()

    def _finish_setup_transition(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay = None
        if getattr(self, "_first_setup", False):
            self._first_setup = False
            self._setup_return_to_avatar_only = False
            if not getattr(self, "_avatar_only", False):
                self._set_avatar_only(True)
        elif getattr(self, "_setup_return_to_avatar_only", False):
            self._setup_return_to_avatar_only = False
            QTimer.singleShot(0, lambda: self._set_avatar_only(True))


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


def _emit_when_window_alive(window, signal_name: str, *args) -> None:
    if getattr(window, "_closed", False):
        return
    try:
        getattr(window, signal_name).emit(*args)
    except RuntimeError:
        # A live-session thread can update UI state after Qt has destroyed the
        # window; such a post-close update must never escape that thread.
        window._closed = True


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_application_close(self):
        return self._win.on_application_close

    @on_application_close.setter
    def on_application_close(self, cb):
        self._win.on_application_close = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_directives_change(self):
        return self._win.on_directives_change

    @on_directives_change.setter
    def on_directives_change(self, cb):
        self._win.on_directives_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, confirmation_id: str, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(confirmation_id), str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass
        # Couche visuelle 3D seule : le pipeline audio (main.py) est inchangé,
        # on duplique juste les frames vers Sarra pour animer les blendshapes.
        # `at` cale l'émission sur le son réellement entendu (synchro A/V).
        try:
            sarra = getattr(self._win, "_sarra", None)
            if sarra is not None:
                sarra.push_frames(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        _emit_when_window_alive(self._win, "_state_sig", state)
        # Visuel 3D seul : sourire léger en écoute, neutre sinon.
        try:
            sarra = getattr(self._win, "_sarra", None)
            if sarra is not None:
                sarra.set_etat(state)
        except Exception:
            pass

    def write_log(self, text: str):
        _emit_when_window_alive(self._win, "_log_sig", text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._require_key_update = True
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def show_video(self, source: str, title: str = "", muted: bool = True,
                   audio_source: str = "") -> None:
        """Thread-safe: play a video where the avatar normally is.

        `source` is a local path or a direct media URL. `audio_source` is for
        the case where the sound arrives as its own stream — YouTube serves no
        combined format any more — and when it is given the two are played
        together and kept in step.

        Muted by default, and that is a decision rather than a default: a
        soundtrack talking over JARVIS is the one way this could make the
        assistant worse. The user turns sound on from the header button or by
        asking, and closes it the same two ways.
        """
        self._win._video_open_sig.emit(str(source or ""), str(title or ""),
                                       bool(muted), str(audio_source or ""))

    def stop_video(self) -> None:
        """Thread-safe: close the video and give the HUD back to the avatar."""
        self._win._video_close_sig.emit()

    def set_video_muted(self, muted: bool) -> None:
        """Thread-safe: silence or unsilence whatever is playing."""
        self._win._video_mute_sig.emit(bool(muted))

    def video_is_playing(self) -> bool:
        return bool(self._win.video_is_playing())

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")