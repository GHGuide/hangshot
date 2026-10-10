#!/usr/bin/env python3
"""Hangshot for Windows and Linux: every screenshot you take hangs on a line at the top of your screen.

The same app as the macOS one in Sources/Hangshot, rebuilt on Qt so this one file runs on Windows and
Linux (and on macOS, for testing). It watches the folder your system saves screenshots to and hangs each
new one on a line you bring down by pushing the pointer against the top edge of the screen.

    python hangshot.py                  run it
    python hangshot.py --folder DIR     watch DIR instead of the usual screenshot folders
    python hangshot.py --self-test      check the layout math and a screenshot landing, then exit
"""
import argparse, math, os, random, subprocess, sys, tempfile, time
from pathlib import Path

# Wayland does not let apps place windows or read the pointer. XWayland does, so prefer it when present.
if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import QFile, QFileSystemWatcher, QMimeData, QPointF, QRectF, QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QColor, QCursor, QDesktopServices, QDrag, QFont, QGuiApplication, QIcon, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygon, QRegion)
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon, QWidget

APP = "Hangshot"
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
# In a general Pictures folder only files named like screenshots count, never your other photos.
SCREENSHOT_WORDS = ("screenshot", "screen shot", "bildschirmfoto", "captura", "capture d", "schermata", "zrzut")

# The line's geometry: the same numbers as Layout in Sources/Hangshot/LineView.swift.
PANEL_H, ROPE_TOP, SPACING, CARD_W, PIN_ABOVE = 210, 10, 174, 150, 9.5
MARGIN, MIN_SCALE, INSET, CARD_BELOW_PIN = 190, 0.6, 4, 14
HIT = 26  # the corner cross and selection circle


def sag(width, count):
    """The rope sags with what it carries: a little for one photo, fully from eight."""
    return min(30, width * 0.018) * (0.35 + 0.65 * min(1, count / 8))


def rope_y(x, width, s):
    f = x / width if width else 0
    return ROPE_TOP + 4 * s * f * (1 - f)


def gap(count, width):
    """Room between card centers: the full spacing while it fits, then tighter."""
    return SPACING if count <= 1 else min(SPACING, (width - 2 * MARGIN) / (count - 1))


def card_scale(count, width):
    """Cards shrink with the gap, so they never overlap."""
    return max(MIN_SCALE, min(1, gap(count, width) / SPACING))


def capacity(width):
    return max(3, int((width - 2 * MARGIN) / (SPACING * MIN_SCALE)) + 1)


def card_x(i, count, width):
    g = gap(count, width)
    return width / 2 - max(count - 1, 0) * g / 2 + i * g


def photo_size(w, h, scale):
    mw, mh = (CARD_W - 14) * scale, 104 * scale
    k = min(mw / w, mh / h) if w and h else 1
    return w * k, h * k


def resource(name):
    """A file shipped next to this script, or inside the PyInstaller bundle."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


# MARK: Where screenshots land

def default_folders():
    """Where Windows and Linux save screenshots: Pictures/Screenshots (Win+PrtScn, Snipping Tool,
    GNOME 42+, KDE Spectacle), the OneDrive copy of it on Windows, and Pictures itself on older Linux."""
    pics = Path(QStandardPaths.writableLocation(QStandardPaths.PicturesLocation) or Path.home() / "Pictures")
    folders = [pics / "Screenshots"]
    if sys.platform == "win32" and os.environ.get("OneDrive"):
        folders.append(Path(os.environ["OneDrive"]) / "Pictures" / "Screenshots")
    if not sys.platform == "win32":
        folders.append(pics)
    # The usual folder may not exist before the first screenshot; make it so it can be watched.
    folders[0].mkdir(parents=True, exist_ok=True)
    return [f for f in dict.fromkeys(folders) if f.is_dir()]


class Watcher:
    """Reports new screenshots in the given folders once their file is complete."""

    def __init__(self, folders, on_new, chosen=False):
        self.folders, self.on_new, self.chosen = [Path(f) for f in folders], on_new, chosen
        self.known = set(self.listing())
        self.sizes = {}
        self.qw = QFileSystemWatcher([str(f) for f in self.folders])
        self.qw.directoryChanged.connect(lambda _: self.timer.start())
        self.timer = QTimer(singleShot=True, interval=300)
        self.timer.timeout.connect(self.scan)

    def wanted(self, path):
        if path.suffix.lower() not in IMAGES or path.name.startswith("."):
            return False
        # A dedicated screenshots folder, or one you chose: any image. Elsewhere only screenshots by name.
        if self.chosen or path.parent.name.lower() == "screenshots":
            return True
        return any(w in path.name.lower() for w in SCREENSHOT_WORDS)

    def listing(self):
        for folder in self.folders:
            try:
                yield from (Path(e.path) for e in os.scandir(folder) if e.is_file())
            except OSError:
                pass

    def scan(self):
        retry = False
        # Oldest first, so several screenshots at once hang in the order they were taken.
        for path in sorted(self.listing(), key=lambda f: f.stat().st_mtime if f.exists() else 0):
            if path in self.known or not self.wanted(path):
                continue
            # Snipping tools write the file in steps: wait until its size holds still and it reads.
            size = path.stat().st_size if path.exists() else 0
            if size and self.sizes.get(path) == size and not QImage(str(path)).isNull():
                self.known.add(path)
                self.sizes.pop(path, None)
                self.on_new(path)
            else:
                self.sizes[path] = size
                retry = True
        if retry:
            self.timer.start()


# MARK: The line

class Pegged:
    def __init__(self, path, x):
        self.path = Path(path)
        img = QImage(str(path))
        self.pix = QPixmap.fromImage(img.scaled(480, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.tilt = random.uniform(-2.5, 2.5)
        self.x, self.s = x, 1.0
        self.kick_at, self.kick = time.monotonic(), 14.0  # it swings as it lands

    def swing(self, now):
        t = now - self.kick_at
        return self.kick * math.exp(-t / 0.9) * math.cos(t * 7)


class LineWindow(QWidget):
    def __init__(self, settings):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                         | (Qt.X11BypassWindowManagerHint if QGuiApplication.platformName() == "xcb" else Qt.Widget))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setMouseTracking(True)
        self.settings = settings
        self.items, self.selected = [], set()
        self.revealed, self.pinned, self.dragging, self.busy_menu = False, False, False, False
        self.drag_items = []
        self.slide, self.sag_now = 0.0, 0.0
        self.peek_until = self.hot_since = self.away_since = 0
        self.hover, self.press, self.copied = None, None, (None, 0)
        self.mask_key = None
        self.next_breeze = time.monotonic() + 10
        self.timer = QTimer(interval=16)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        for p in settings.value("pegged", [], list) or []:
            if Path(p).exists():
                self.items.append(Pegged(p, 0))
        self.place()

    # Layout -------------------------------------------------------------------------------------------

    @property
    def W(self):
        return self.width() or 1440

    def live(self):
        return [it for it in self.items if it.path.exists()]

    def card_rect(self, it):
        pw, ph = photo_size(it.pix.width(), it.pix.height(), it.s)
        top = rope_y(it.x, self.W, self.sag_now) - PIN_ABOVE + CARD_BELOW_PIN
        return QRectF(it.x - pw / 2 - INSET, top, pw + 2 * INSET, ph + 2 * INSET)

    def clear_rect(self):
        x = self.W - 64
        return QRectF(x - 44, rope_y(x, self.W, self.sag_now) + 22 - 13, 88, 26)

    def hint_rect(self):
        return QRectF(self.W / 2 - 150, rope_y(self.W / 2, self.W, self.sag_now) + 22, 300, 26)

    def offset(self):
        return -(1 - self.slide) * (PANEL_H + 12)

    def item_at(self, pos):
        p = QPointF(pos.x(), pos.y() - self.offset())
        for it in reversed(self.items):
            r = self.card_rect(it)
            if r.adjusted(-4, -4, 4, 4).contains(p):
                part = ("cross" if p.x() < r.left() + HIT else "circle" if p.x() > r.right() - HIT else "card") \
                    if p.y() < r.top() + HIT else "card"
                return it, part
        if self.items and self.clear_rect().contains(p):
            return None, "clear"
        return None, None

    def place(self, screen=None):
        screen = screen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g, a = screen.geometry(), screen.availableGeometry()
        self.setGeometry(g.left(), a.top(), g.width(), PANEL_H)

    # Showing and hiding -------------------------------------------------------------------------------

    def reveal(self, pinned=False, peek=0.0):
        if not self.revealed:
            self.place()
            self.show()
            self.raise_()
        self.revealed = True
        self.pinned = self.pinned or pinned
        if peek:
            self.peek_until = time.monotonic() + peek
        self.away_since = 0

    def retract(self):
        self.revealed, self.pinned, self.peek_until = False, False, 0

    def toggle(self):
        self.retract() if self.revealed else self.reveal(pinned=True)

    def tick(self):
        now = time.monotonic()
        cur = QCursor.pos()
        screen = QGuiApplication.screenAt(cur)
        if not self.revealed:
            # Resting against the top edge brings the line down, if anything hangs on it.
            at_edge = screen is not None and cur.y() <= screen.geometry().top() + 1
            if at_edge and self.items:
                self.hot_since = self.hot_since or now
                if now - self.hot_since >= 0.25:
                    self.hot_since = 0
                    self.reveal()
            else:
                self.hot_since = 0
        else:
            zone = self.frameGeometry().adjusted(0, -self.frameGeometry().top() + (screen.geometry().top() if screen else 0), 0, 0)
            inside = zone.contains(cur)
            if inside:
                self.pinned = False
            busy = self.pinned or self.dragging or self.busy_menu or self.press or now < self.peek_until
            if inside or busy:
                self.away_since = 0
            else:
                self.away_since = self.away_since or now
                if now - self.away_since >= 0.5:
                    self.retract()
        if not self.isVisible():
            return
        self.animate(now)
        self.update_mask()
        self.update()

    def animate(self, now):
        n, W = len(self.items), self.W
        self.slide += ((1.0 if self.revealed else 0.0) - self.slide) * 0.25
        self.sag_now += (sag(W, n) - self.sag_now) * 0.15
        for i, it in enumerate(self.items):
            it.x += (card_x(i, n, W) - it.x) * 0.2
            it.s += (card_scale(n, W) - it.s) * 0.2
        if now > self.next_breeze and self.items and not self.dragging:
            self.next_breeze = now + random.uniform(8, 15)
            for it in self.items:
                it.kick_at, it.kick = now + random.uniform(0, 0.35), random.uniform(1.6, 3.4)
        if not self.revealed and self.slide < 0.01:
            self.slide = 0
            self.hide()  # tucked away, no window is left over the screen

    def update_mask(self):
        """Clicks reach the line only over what is drawn; everywhere else they go to the app below."""
        dy = int(self.offset())
        rects = [self.card_rect(it).adjusted(-6, -20, 6, 30).toAlignedRect() for it in self.items]
        if self.items:
            rects.append(self.clear_rect().toAlignedRect())
        else:
            rects.append(self.hint_rect().toAlignedRect())
        key = (dy, tuple((r.x(), r.y(), r.width(), r.height()) for r in rects), int(self.sag_now))
        if key == self.mask_key:
            return
        self.mask_key = key
        pts = [(x, int(rope_y(x, self.W, self.sag_now))) for x in range(0, self.W + 40, 40)]
        band = QPolygon([QPointF(x, y - 3).toPoint() for x, y in pts] + [QPointF(x, y + 4).toPoint() for x, y in reversed(pts)])
        region = QRegion(band)
        for r in rects:
            region = region.united(QRegion(r))
        self.setMask(region.translated(0, dy))

    # Drawing ------------------------------------------------------------------------------------------

    def paintEvent(self, _):
        p = QPainter(self)
        try:
            p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
            p.translate(0, self.offset())
            self.draw_rope(p)
            now = time.monotonic()
            if not self.items:
                self.pill(p, self.hint_rect(), "Take a screenshot and it will hang here")
            for it in self.items:
                self.draw_card(p, it, now)
            if self.items:
                self.pill(p, self.clear_rect(), "Clear all", cross=True, hot=self.hover == (None, "clear"))
        finally:
            p.end()

    def draw_rope(self, p):
        W = self.W
        path = QPainterPath(QPointF(-20, ROPE_TOP))
        path.quadTo(QPointF(W / 2, ROPE_TOP + 2 * self.sag_now), QPointF(W + 20, ROPE_TOP))
        for color, width, dy in ((QColor(0, 0, 0, 56), 1.6, 1.2), (QColor(140, 140, 140), 1.2, 0), (QColor(255, 255, 255, 115), 0.5, -0.4)):
            fade = QLinearGradient(0, 0, W, 0)
            clear = QColor(color)
            clear.setAlpha(0)
            for at, c in ((0, clear), (0.08, color), (0.92, color), (1, clear)):
                fade.setColorAt(at, c)
            p.setPen(QPen(fade, width))
            p.drawPath(path.translated(0, dy))

    def draw_card(self, p, it, now):
        r = self.card_rect(it)
        pin_y = rope_y(it.x, self.W, self.sag_now)
        hovered = self.hover and self.hover[0] is it
        sel = it in self.selected
        p.save()
        p.translate(it.x, pin_y)
        p.rotate(it.tilt + it.swing(now))
        p.translate(-it.x, -pin_y)
        p.setOpacity(0.45 if self.dragging and it in self.drag_items else 1)
        # Card: dark glass frame around the photo.
        p.setPen(QPen(QColor(255, 255, 255, 70), 0.8))
        p.setBrush(QColor(28, 28, 30, 215))
        p.drawRoundedRect(r, 12, 12)
        photo = QPainterPath()
        inner = r.adjusted(INSET, INSET, -INSET, -INSET)
        photo.addRoundedRect(inner, 8, 8)
        p.save()
        p.setClipPath(photo)
        p.drawPixmap(inner.toRect(), it.pix)
        p.restore()
        if sel:
            p.setPen(QPen(QColor("#2B87F2"), 2.4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 13, 13)
        # Clip on the line.
        p.setPen(QPen(QColor(0, 0, 0, 60), 0.6))
        p.setBrush(QColor(205, 205, 210))
        p.drawRoundedRect(QRectF(it.x - 4.5, pin_y - PIN_ABOVE, 9, 26), 3.5, 3.5)
        # Corner cross and selection circle, on hover or while anything is picked.
        if hovered or self.selected:
            if hovered:
                self.round_button(p, QRectF(r.left() + 3, r.top() + 3, 20, 20), "cross")
            self.round_button(p, QRectF(r.right() - 23, r.top() + 3, 20, 20), "check" if sel else "ring")
        p.restore()
        cid, until = self.copied
        if cid is it and now < until:
            self.pill(p, QRectF(it.x - 40, r.bottom() + 4, 80, 24), "Copied")

    def round_button(self, p, r, kind):
        p.setPen(Qt.NoPen if kind == "check" else QPen(QColor(255, 255, 255, 220), 1.2))
        p.setBrush(QColor("#2B87F2") if kind == "check" else QColor(28, 28, 30, 200))
        p.drawEllipse(r)
        c = r.center()
        p.setPen(QPen(QColor(255, 255, 255), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if kind == "cross":
            p.drawLine(QPointF(c.x() - 3.5, c.y() - 3.5), QPointF(c.x() + 3.5, c.y() + 3.5))
            p.drawLine(QPointF(c.x() + 3.5, c.y() - 3.5), QPointF(c.x() - 3.5, c.y() + 3.5))
        elif kind == "check":
            p.drawPolyline([QPointF(c.x() - 4, c.y()), QPointF(c.x() - 1, c.y() + 3), QPointF(c.x() + 4, c.y() - 3)])

    def pill(self, p, r, text, cross=False, hot=False):
        p.setPen(QPen(QColor(255, 255, 255, 70), 0.8))
        p.setBrush(QColor(28, 28, 30, 235 if hot else 205))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor(255, 255, 255))
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        if cross:
            x, y = r.left() + 14, r.center().y()
            p.setPen(QPen(QColor(255, 255, 255), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x - 3.5, y - 3.5), QPointF(x + 3.5, y + 3.5))
            p.drawLine(QPointF(x + 3.5, y - 3.5), QPointF(x - 3.5, y + 3.5))
            r = r.adjusted(14, 0, 0, 0)
        p.drawText(r, Qt.AlignCenter, text)

    # The photos -------------------------------------------------------------------------------------

    def hang(self, path):
        path = Path(path)
        if any(it.path == path for it in self.items) or QImage(str(path)).isNull():
            return
        self.place()
        self.items.append(Pegged(path, self.W - MARGIN))
        # A full line lets the oldest photo fall off the far end. Its file stays where it is.
        while len(self.items) > capacity(self.W):
            self.take_down(self.items[0])
        self.save()
        self.reveal(peek=2.5)

    def take_down(self, it):
        if it in self.items:
            self.items.remove(it)
        self.selected.discard(it)
        self.save()

    def prune(self):
        for it in [it for it in self.items if not it.path.exists()]:
            self.take_down(it)

    def save(self):
        self.settings.setValue("pegged", [str(it.path) for it in self.items])

    def copy(self, it):
        mime = QMimeData()
        mime.setImageData(QImage(str(it.path)))
        mime.setUrls([QUrl.fromLocalFile(str(it.path))])
        QGuiApplication.clipboard().setMimeData(mime)
        self.copied = (it, time.monotonic() + 1.2)

    def open(self, it):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(it.path)))

    def show_in_folder(self, it):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(it.path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(it.path.parent)))

    def trash(self, it):
        ok = QFile.moveToTrash(str(it.path))
        if (ok[0] if isinstance(ok, tuple) else ok) or not it.path.exists():
            self.take_down(it)

    def group(self, it):
        """What a drag from this photo carries: the selection when it is part of it, else just the photo."""
        if it in self.selected and len(self.selected) > 1:
            return [x for x in self.items if x in self.selected]
        return [it]

    def drag(self, it):
        group = self.group(it)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(x.path)) for x in group])
        d = QDrag(self)
        d.setMimeData(mime)
        thumb = it.pix.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if len(group) > 1:
            painter = QPainter(thumb)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor("#2B87F2"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(thumb.width() - 30, 4, 26, 26))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(QRectF(thumb.width() - 30, 4, 26, 26), Qt.AlignCenter, str(len(group)))
            painter.end()
        d.setPixmap(thumb)
        self.dragging, self.drag_items = True, group
        # Apps take a copy; a folder may move the files there, and then they leave the line.
        result = d.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
        self.dragging, self.drag_items = False, []
        if result != Qt.IgnoreAction:
            self.selected.clear()
        QTimer.singleShot(700, self.prune)

    # Mouse -------------------------------------------------------------------------------------------

    def mouseMoveEvent(self, e):
        self.hover = self.item_at(e.position())
        if self.press and e.buttons() & Qt.LeftButton and self.press[0] is not None:
            it, part, start = self.press
            if part == "card" and (e.position() - start).manhattanLength() > 6:
                self.press = None
                self.drag(it)

    def leaveEvent(self, _):
        self.hover = None

    def mousePressEvent(self, e):
        it, part = self.item_at(e.position())
        if e.button() == Qt.RightButton and it:
            self.context_menu(it, e.globalPosition().toPoint())
        elif e.button() == Qt.LeftButton:
            self.press = (it, part, e.position())

    def mouseReleaseEvent(self, e):
        if not self.press:
            return
        it, part, _ = self.press
        self.press = None
        if part == "clear":
            self.items.clear()
            self.selected.clear()
            self.save()
        elif it and part == "cross":
            self.take_down(it)
        elif it and part == "circle":
            self.selected.symmetric_difference_update({it})
        elif it:
            self.copy(it)

    def mouseDoubleClickEvent(self, e):
        it, part = self.item_at(e.position())
        if it and part == "card":
            self.open(it)

    def context_menu(self, it, at):
        m = QMenu()
        for label, fn in (("Copy", self.copy), ("Open", self.open),
                          ("Show in folder", self.show_in_folder), (None, None),
                          ("Take down", self.take_down), ("Move to Trash", self.trash)):
            if label is None:
                m.addSeparator()
            else:
                m.addAction(label).triggered.connect(lambda _=False, fn=fn: fn(it))
        self.busy_menu = True
        m.exec(at)
        self.busy_menu = False


# MARK: Tray menu and start at login

def autostart_path():
    if sys.platform == "win32":
        return None
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "hangshot.desktop"


def launch_command():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


def autostart_enabled():
    if sys.platform == "win32":
        return QSettings(r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run", QSettings.NativeFormat).contains(APP)
    return autostart_path().exists()


def set_autostart(on):
    cmd = " ".join(f'"{c}"' for c in launch_command())
    if sys.platform == "win32":
        run = QSettings(r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run", QSettings.NativeFormat)
        run.setValue(APP, cmd) if on else run.remove(APP)
        return
    path = autostart_path()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[Desktop Entry]\nType=Application\nName={APP}\nExec={cmd}\nX-GNOME-Autostart-enabled=true\n")
    else:
        path.unlink(missing_ok=True)


class App:
    def __init__(self, folder=None):
        self.settings = QSettings(APP, APP)
        self.line = LineWindow(self.settings)
        chosen = folder or self.settings.value("folder", "")
        self.watch(chosen)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(QIcon(str(resource("icon.png"))))
            self.tray.setToolTip(APP)
            self.menu = QMenu()
            self.menu.aboutToShow.connect(self.build_menu)
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(lambda reason: self.line.toggle() if reason == QSystemTrayIcon.Trigger else None)
            self.tray.show()

    def watch(self, chosen):
        folders = [Path(chosen)] if chosen and Path(chosen).is_dir() else default_folders()
        self.watcher = Watcher(folders, self.line.hang, chosen=bool(chosen))

    def build_menu(self):
        m = self.menu
        m.clear()
        m.addAction("Hide line" if self.line.revealed else "Show line", self.line.toggle)
        clear = m.addAction("Take everything down", lambda: (self.line.items.clear(), self.line.selected.clear(), self.line.save()))
        clear.setEnabled(bool(self.line.items))
        m.addSeparator()
        m.addAction("Choose screenshots folder…", self.choose_folder)
        m.addAction("Open screenshots folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.watcher.folders[0]))))
        if sys.platform != "darwin":
            login = QAction("Start at login", m, checkable=True, checked=autostart_enabled())
            login.toggled.connect(set_autostart)
            m.addAction(login)
        m.addSeparator()
        m.addAction(f"Quit {APP}", QApplication.quit)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(None, "Folder your screenshots are saved to", str(self.watcher.folders[0]))
        if folder:
            self.settings.setValue("folder", folder)
            self.watch(folder)


# MARK: Self-test

def self_test():
    """Checks the layout math and that a screenshot written to a watched folder hangs on the line."""
    for w in (1280, 1440, 1800, 2560):
        c = capacity(w)
        for n in (1, 5, c):
            g, s = gap(n, w), card_scale(n, w)
            right_edge = w / 2 + (n - 1) * g / 2 + (CARD_W - 14) * s / 2 + INSET
            assert right_edge <= w - 108, (w, n, right_edge)      # clear of the Clear all button
            assert n == 1 or g >= (CARD_W - 14) * s + 8, (w, n)    # cards never overlap
    assert sag(1800, 1) < sag(1800, 8) == sag(1800, 20)

    app = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.IniFormat)
        line = LineWindow(settings)
        watcher = Watcher([tmp], line.hang, chosen=True)
        shots = []
        for i in range(3):
            img = QImage(320, 200, QImage.Format_RGB32)
            img.fill(QColor(40 * i, 120, 200))
            path = Path(tmp) / f"Screenshot {i}.png"
            img.save(str(path))
            shots.append(path)
        deadline = time.monotonic() + 10
        while len(line.items) < 3 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert len(line.items) == 3, f"expected 3 screenshots on the line, got {len(line.items)}"
        assert line.revealed, "a new screenshot should bring the line down"
        a, b, c = line.items
        line.selected = {a, c}
        assert line.group(a) == [a, c] and line.group(b) == [b]
        line.take_down(b)
        assert [x.path for x in line.items] == [shots[0], shots[2]] and b.path.exists()
        assert settings.value("pegged") == [str(shots[0]), str(shots[2])]
        for _ in range(60):
            line.tick()
            app.processEvents()
        assert abs(line.items[0].s - card_scale(2, line.W)) < 0.01
        # Draw every state: hovered, picked and not, copied, the empty line. Qt only prints errors
        # raised while painting, so catch them here.
        errors = []
        sys.excepthook = lambda *e: errors.append(e)
        line.selected, line.hover, line.copied = {line.items[0]}, (line.items[1], "card"), (line.items[0], time.monotonic() + 5)
        line.slide = 1
        line.grab()
        line.items.clear()
        line.grab()
        sys.excepthook = sys.__excepthook__
        assert not errors, errors
        del watcher
    print("self-test passed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", help="watch this folder instead of the usual screenshot folders")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(resource("icon.png"))))
    hangshot = App(args.folder)  # noqa: F841, kept alive for the event loop
    if not hangshot.tray:
        print("No system tray here: the line still comes down for new screenshots and at the top edge.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
