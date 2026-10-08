#!/usr/bin/env python3
"""Alert overlay for new messages.

Styles:
  island     — Dynamic-Island-style Liquid Glass pill that springs out of the
               MacBook notch (or top-center on screens without one): real
               backdrop blur, dark glass tint, specular sheen and rim, and a
               breathing radial glow that cycles through colors. Stays up
               until clicked. Default.
  bar        — flashing red strip across the top of every display
  fullscreen — whole-screen red takeover

Usage: flash.py [headline] [body] [seconds] [style] [bar_height]
[seconds] auto-closes bar/fullscreen; the island only closes on click.
Set TELEGRAM_ALARM_SNAPSHOT=/path.png to write a self-capture of the overlay
window mid-animation and exit (debug aid); TELEGRAM_ALARM_SNAPSHOT_T sets the
capture moment.
"""
import os
import re
import signal
import sys
from pathlib import Path

import objc
from Foundation import NSMakeRect, NSObject, NSTimer, NSURL
from AppKit import (
    NSAnimationContext, NSAppearance, NSApplication, NSBackingStoreBuffered,
    NSColor, NSFont, NSScreen, NSTextField, NSView, NSWindow,
)

try:
    from AppKit import NSScreenSaverWindowLevel
except ImportError:
    NSScreenSaverWindowLevel = 1000
try:
    from AppKit import NSWindowStyleMaskBorderless
except ImportError:
    NSWindowStyleMaskBorderless = 0
try:
    from AppKit import NSTextAlignmentCenter
except ImportError:
    NSTextAlignmentCenter = 1
try:
    from AppKit import NSTextAlignmentLeft, NSTextAlignmentRight
except ImportError:
    NSTextAlignmentLeft, NSTextAlignmentRight = 0, 1
try:
    from AppKit import NSApplicationActivationPolicyAccessory
except ImportError:
    NSApplicationActivationPolicyAccessory = 1
try:
    from Quartz import (
        CABasicAnimation, CAGradientLayer, CAKeyframeAnimation,
        CAMediaTimingFunction, CGPathCreateWithRoundedRect, CGRectMake,
    )
    HAVE_QUARTZ = True
except Exception:
    HAVE_QUARTZ = False

# NSWindowCollectionBehavior: CanJoinAllSpaces | Stationary | FullScreenAuxiliary
# (visible on every Space, untouched by Mission Control, shows over fullscreen apps)
COLLECTION_BEHAVIOR = (1 << 0) | (1 << 4) | (1 << 8)
AUTORESIZE_WH = 18  # NSViewWidthSizable | NSViewHeightSizable

BASE = Path(__file__).resolve().parent
PIDFILE = BASE / "island.pid"

HEADLINE = sys.argv[1] if len(sys.argv) > 1 else "New message!"
BODY = sys.argv[2] if len(sys.argv) > 2 else ""
TIMEOUT = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
STYLE = sys.argv[4] if len(sys.argv) > 4 else "island"
BAR_HEIGHT = float(sys.argv[5]) if len(sys.argv) > 5 else 150.0
THEME = sys.argv[6] if len(sys.argv) > 6 else "project"


def _c(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


RED = _c(0.82, 0.04, 0.04)
DARK = _c(0.07, 0.0, 0.0)
# Glow color cycles, Siri-style. "project" = hot red -> amber -> magenta ->
# azure; "warning" (connection trouble) = amber -> yellow -> warm gray.
GLOW_CYCLES = {
    "project": [_c(1.0, 0.23, 0.08), _c(1.0, 0.55, 0.10), _c(0.95, 0.30, 0.85),
                _c(0.35, 0.55, 1.00), _c(1.0, 0.23, 0.08)],
    "warning": [_c(1.0, 0.60, 0.05), _c(1.0, 0.85, 0.15), _c(0.75, 0.70, 0.60),
                _c(1.0, 0.60, 0.05)],
}
GLOW_CYCLE = GLOW_CYCLES.get(THEME, GLOW_CYCLES["project"])
if THEME == "warning":
    RED = _c(0.85, 0.45, 0.02)  # bar/fullscreen turn amber for warnings
FLASH_INTERVAL = 0.18
FLASH_TICKS = 14  # ~2.5 s of strobing, then solid red until dismissed


def quit_app():
    NSApplication.sharedApplication().terminate_(None)


def make_label(text, frame, size, bold, alpha=1.0, align=None):
    label = NSTextField.alloc().initWithFrame_(frame)
    label.setStringValue_(text)
    label.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha))
    label.setAlignment_(NSTextAlignmentCenter if align is None else align)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    label.cell().setWraps_(True)
    label.cell().setTruncatesLastVisibleLine_(True)
    return label


def base_window(win_cls, rect):
    win = win_cls.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
    win.setLevel_(NSScreenSaverWindowLevel)
    win.setCollectionBehavior_(COLLECTION_BEHAVIOR)
    win.setIgnoresMouseEvents_(False)
    win.setReleasedWhenClosed_(False)
    return win


def enable_window_blur(win, radius=26):
    """Ask the window server for a real backdrop blur behind this window.

    NSVisualEffectView's behind-window material is not composited for windows
    at very high levels (it falls back to flat gray), so the blur is applied
    directly via SkyLight/CGS instead.
    """
    import ctypes
    candidates = (
        ("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
         "SLSMainConnectionID", "SLSSetWindowBackgroundBlurRadius"),
        ("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
         "CGSMainConnectionID", "CGSSetWindowBackgroundBlurRadius"),
        ("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
         "CGSMainConnectionID", "CGSSetWindowBackgroundBlurRadius"),
    )
    for path, conn_name, blur_name in candidates:
        try:
            lib = ctypes.CDLL(path)
            conn_fn = getattr(lib, conn_name)
            blur_fn = getattr(lib, blur_name)
            conn_fn.restype = ctypes.c_int32
            blur_fn.argtypes = [ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32]
            blur_fn.restype = ctypes.c_int32
            conn = conn_fn()
            if conn and blur_fn(conn, int(win.windowNumber()), int(radius)) == 0:
                return True
        except Exception:
            continue
    return False


# --------------------------------------------------------------------------
# bar / fullscreen styles
# --------------------------------------------------------------------------

class FlashWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True

    def mouseDown_(self, event):
        quit_app()

    def keyDown_(self, event):
        quit_app()


class FlashController(NSObject):
    def initWithWindows_(self, windows):
        self = objc.super(FlashController, self).init()
        if self is None:
            return None
        self.windows = windows
        self.ticks = 0
        return self

    def tick_(self, timer):
        self.ticks += 1
        color = RED if self.ticks % 2 == 0 else DARK
        if self.ticks >= FLASH_TICKS:
            timer.invalidate()
            color = RED
        for w in self.windows:
            w.setBackgroundColor_(color)

    def timeout_(self, timer):
        quit_app()


def flash_main(app):
    body = BODY if STYLE == "fullscreen" else " ".join(BODY.split())[:250]
    windows = []
    for screen in NSScreen.screens():
        sf = screen.frame()
        if STYLE == "fullscreen":
            rect = sf
        else:
            bh = min(BAR_HEIGHT, sf.size.height)
            rect = NSMakeRect(sf.origin.x, sf.origin.y + sf.size.height - bh,
                              sf.size.width, bh)
        win = base_window(FlashWindow, rect)
        win.setBackgroundColor_(RED)

        content = win.contentView()
        w = content.bounds().size.width
        h = content.bounds().size.height
        if STYLE == "fullscreen":
            content.addSubview_(make_label(
                HEADLINE, NSMakeRect(w * 0.05, h * 0.58, w * 0.9, h * 0.18), 64, True))
            if body:
                content.addSubview_(make_label(
                    body, NSMakeRect(w * 0.1, h * 0.22, w * 0.8, h * 0.32), 28, False))
            content.addSubview_(make_label(
                "click or press any key to dismiss",
                NSMakeRect(w * 0.05, h * 0.05, w * 0.9, h * 0.05), 16, False))
        else:
            content.addSubview_(make_label(
                HEADLINE, NSMakeRect(w * 0.02, h * 0.50, w * 0.96, h * 0.40), 22, True))
            if body:
                content.addSubview_(make_label(
                    body, NSMakeRect(w * 0.02, h * 0.06, w * 0.96, h * 0.42), 14, False))
            content.addSubview_(make_label(
                "click to dismiss", NSMakeRect(w - 150, h - 20, 142, 15), 10, False))
        windows.append(win)

    controller = FlashController.alloc().initWithWindows_(windows)
    for win in windows:
        # Bar mode deliberately never takes keyboard focus, so typing is not
        # interrupted; fullscreen mode grabs focus so any key dismisses it.
        if STYLE == "fullscreen":
            win.makeKeyAndOrderFront_(None)
        win.orderFrontRegardless()
    if STYLE == "fullscreen":
        app.activateIgnoringOtherApps_(True)

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        FLASH_INTERVAL, controller, "tick:", None, True)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        TIMEOUT, controller, "timeout:", None, False)
    return controller


# --------------------------------------------------------------------------
# island style (Liquid Glass + radial glow, persists until clicked)
# --------------------------------------------------------------------------

DRIVER = None


class IslandWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return False

    def mouseDown_(self, event):
        if DRIVER is not None:
            DRIVER.beginCollapse()


if HAVE_QUARTZ:
    class GradientView(NSView):
        def makeBackingLayer(self):
            return CAGradientLayer.layer()
else:
    GradientView = NSView


class IslandDriver(NSObject):
    def expandTimer_(self, timer):
        def changes(ctx):
            ctx.setDuration_(0.55)
            if HAVE_QUARTZ:
                # y control points > 1 give the springy overshoot
                ctx.setTimingFunction_(
                    CAMediaTimingFunction.functionWithControlPoints____(0.2, 1.35, 0.35, 1.0))
            ctx.setAllowsImplicitAnimation_(True)
            self.pill.animator().setFrame_(self.end)
            self.halo.animator().setAlphaValue_(1.0)

        NSAnimationContext.runAnimationGroup_completionHandler_(changes, None)
        if HAVE_QUARTZ:
            hl = self.halo.layer()
            # Breathe brightness and reach of the pill-shaped glow while its
            # color drifts through the cycle — motion + color shift is what
            # catches the eye.
            pulse = CABasicAnimation.animationWithKeyPath_("shadowOpacity")
            pulse.setFromValue_(0.55)
            pulse.setToValue_(1.0)
            pulse.setDuration_(0.55)
            pulse.setAutoreverses_(True)
            pulse.setRepeatCount_(1.0e9)
            hl.addAnimation_forKey_(pulse, "pulse")
            swell = CABasicAnimation.animationWithKeyPath_("shadowRadius")
            swell.setFromValue_(12.0)
            swell.setToValue_(44.0)
            swell.setDuration_(0.55)
            swell.setAutoreverses_(True)
            swell.setRepeatCount_(1.0e9)
            hl.addAnimation_forKey_(swell, "swell")
            cycle = CAKeyframeAnimation.animationWithKeyPath_("shadowColor")
            cycle.setValues_([c.CGColor() for c in GLOW_CYCLE])
            cycle.setDuration_(3.0)
            cycle.setRepeatCount_(1.0e9)
            hl.addAnimation_forKey_(cycle, "colors")
            hl.setShadowOpacity_(0.8)
            hl.setShadowRadius_(28.0)

    def fadeInTimer_(self, timer):
        def changes(ctx):
            ctx.setDuration_(0.35)
            for label in self.labels:
                label.animator().setAlphaValue_(1.0)

        NSAnimationContext.runAnimationGroup_completionHandler_(changes, None)

    def beginCollapse(self):
        if self.collapsing:
            return
        self.collapsing = True
        if HAVE_QUARTZ:
            for key in ("pulse", "swell", "colors"):
                self.halo.layer().removeAnimationForKey_(key)

        def changes(ctx):
            ctx.setDuration_(0.35)
            if HAVE_QUARTZ:
                ctx.setTimingFunction_(
                    CAMediaTimingFunction.functionWithControlPoints____(0.45, 0.0, 1.0, 1.0))
            ctx.setAllowsImplicitAnimation_(True)
            self.pill.animator().setFrame_(self.start)
            self.halo.animator().setAlphaValue_(0.0)
            for label in self.labels:
                label.animator().setAlphaValue_(0.0)

        NSAnimationContext.runAnimationGroup_completionHandler_(changes, quit_app)

    def snapshotTimer_(self, timer):
        # Debug aid: capture this window's own composited pixels (no Screen
        # Recording permission needed for one's own window), then exit so
        # scripted runs don't wait forever on a click.
        try:
            from Quartz import (
                CGRectNull, CGWindowListCreateImage,
                kCGWindowImageBoundsIgnoreFraming,
                kCGWindowListOptionIncludingWindow,
                CGImageDestinationCreateWithURL, CGImageDestinationAddImage,
                CGImageDestinationFinalize,
            )
            targets = [(self.win, self.snapshotPath)]
            if getattr(self, "haloWin", None) is not None:
                targets.append((self.haloWin, self.snapshotPath + ".halo.png"))
            for window, path in targets:
                img = CGWindowListCreateImage(
                    CGRectNull, kCGWindowListOptionIncludingWindow,
                    window.windowNumber(), kCGWindowImageBoundsIgnoreFraming)
                if img is None:
                    print("snapshot: no image for", path, file=sys.stderr)
                    continue
                dest = CGImageDestinationCreateWithURL(
                    NSURL.fileURLWithPath_(path), "public.png", 1, None)
                CGImageDestinationAddImage(dest, img, None)
                CGImageDestinationFinalize(dest)
        except Exception as exc:
            print("snapshot failed:", exc, file=sys.stderr)
        finally:
            quit_app()


def parse_project(body):
    """Pull the header fields out of a message.

    The first non-empty line is the title. Job-board bots (such as
    @KayaProjectsBot) follow it with a "fixed:/hourly: <amount>" budget line
    and a "<flag emoji> Country-City" location line; any message without
    those lines simply gets None for them.
    """
    title = budget = location = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if title is None:
            title = line
            continue
        m = re.match(r"(fixed|hourly)\s*:\s*(.+)", line, re.I)
        if m and budget is None:
            budget = re.sub(r"\$\s+", "$", m.group(2)).strip()
            if m.group(1).lower() == "hourly":
                budget += "/hr"
            continue
        if re.match(r"payment\s+verified", line, re.I):
            # End of the header block — anything below (e.g. the flag emoji
            # in "🇮🇷 Open in Kaya") must not be mistaken for the location.
            break
        if location is None and re.search(r"[\U0001F1E6-\U0001F1FF]", line):
            location = line
        if budget is not None and location is not None:
            break
    return title, budget, location


def notched_screen():
    for screen in NSScreen.screens():
        try:
            if screen.safeAreaInsets().top > 0:
                return screen
        except Exception:
            pass
    return None


def pad_rect(rect, pad):
    return NSMakeRect(rect.origin.x - pad, rect.origin.y - pad,
                      rect.size.width + pad * 2, rect.size.height + pad * 2)


def replace_previous_island():
    # The island lives until clicked, so a newer alert takes the old one down
    # instead of stacking pills.
    try:
        os.kill(int(PIDFILE.read_text().strip()), signal.SIGTERM)
    except Exception:
        pass
    try:
        PIDFILE.write_text(str(os.getpid()))
    except Exception:
        pass


def island_main(app):
    global DRIVER
    replace_previous_island()
    screen = notched_screen() or NSScreen.screens()[0]
    sf = screen.frame()
    top = sf.origin.y + sf.size.height
    cx = sf.origin.x + sf.size.width / 2.0

    # Seed the pill with the real notch geometry so it grows out of it;
    # sane defaults for screens without a notch.
    notch_w, notch_h = 230.0, 34.0
    try:
        insets = screen.safeAreaInsets()
        if insets.top > 0:
            notch_h = float(insets.top)
            left = screen.auxiliaryTopLeftArea()
            right = screen.auxiliaryTopRightArea()
            if left is not None and right is not None:
                nl = left.origin.x + left.size.width
                nr = right.origin.x
                if nr > nl:
                    notch_w = float(nr - nl)
                    cx = (nl + nr) / 2.0
    except Exception:
        pass

    exp_w = min(600.0, sf.size.width * 0.5)
    exp_h = notch_h + 62.0
    radius = max(8.0, min(16.0, notch_h / 2.0 - 1.0))
    margin = 110.0  # head-room for the bloom around the pill
    win_w = exp_w + margin * 2
    win_h = exp_h + margin
    win = base_window(IslandWindow, NSMakeRect(cx - win_w / 2, top - win_h, win_w, win_h))
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setHasShadow_(False)
    try:
        win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
    except Exception:
        pass

    # The halo lives in its own, never-blurred window stacked right below the
    # pill window: the window-server blur fogs every alpha'd pixel of the
    # window it is applied to, so the bloom must stay out of the blurred one.
    haloWin = base_window(IslandWindow, NSMakeRect(cx - win_w / 2, top - win_h, win_w, win_h))
    haloWin.setOpaque_(False)
    haloWin.setBackgroundColor_(NSColor.clearColor())
    haloWin.setHasShadow_(False)

    content = win.contentView()
    content.setWantsLayer_(True)
    halo_content = haloWin.contentView()
    halo_content.setWantsLayer_(True)

    start = NSMakeRect(win_w / 2 - notch_w / 2, win_h - notch_h, notch_w, notch_h)
    end = NSMakeRect(win_w / 2 - exp_w / 2, win_h - exp_h, exp_w, exp_h)

    # The glow: a blurred silhouette of the pill's exact rounded rect
    # (shadowPath on a fill-less layer), so the halo hugs the box shape on
    # all sides instead of forming a detached ellipse. It sits at the pill's
    # final rect and simply fades in/out; the shadow needs no visible fill.
    halo = NSView.alloc().initWithFrame_(end)
    halo.setWantsLayer_(True)
    hl = halo.layer()
    hl.setBackgroundColor_(NSColor.clearColor().CGColor())
    first = GLOW_CYCLE[0]
    hl.setShadowColor_(first.CGColor())
    hl.setShadowOffset_((0.0, 0.0))
    hl.setShadowRadius_(28.0)
    hl.setShadowOpacity_(0.0)
    if HAVE_QUARTZ:
        hl.setShadowPath_(CGPathCreateWithRoundedRect(
            CGRectMake(0, 0, end.size.width, end.size.height), radius, radius, None))
    halo.setAlphaValue_(0.0)
    halo_content.addSubview_(halo)

    pill = NSView.alloc().initWithFrame_(start)
    pill.setWantsLayer_(True)

    # Liquid Glass, hand-built: the window server blurs what's behind the
    # window (enable_window_blur below), and the pill layers a dark tint,
    # a specular top sheen, and a hairline rim on top of that blur.
    tint = NSView.alloc().initWithFrame_(pill.bounds())
    tint.setAutoresizingMask_(AUTORESIZE_WH)
    tint.setWantsLayer_(True)
    tint.layer().setBackgroundColor_(
        NSColor.colorWithCalibratedWhite_alpha_(0.02, 0.42).CGColor())
    tint.layer().setCornerRadius_(radius)
    pill.addSubview_(tint)

    sheen = GradientView.alloc().initWithFrame_(pill.bounds())
    sheen.setAutoresizingMask_(AUTORESIZE_WH)
    sheen.setWantsLayer_(True)
    if HAVE_QUARTZ:
        sl = sheen.layer()
        sl.setStartPoint_((0.5, 1.0))
        sl.setEndPoint_((0.5, 0.5))
        sl.setColors_([
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.20).CGColor(),
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.02).CGColor(),
        ])
        sl.setCornerRadius_(radius)
        sl.setMasksToBounds_(True)
    pill.addSubview_(sheen)

    rim = NSView.alloc().initWithFrame_(pill.bounds())
    rim.setAutoresizingMask_(AUTORESIZE_WH)
    rim.setWantsLayer_(True)
    rim.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
    rim.layer().setCornerRadius_(radius)
    rim.layer().setBorderWidth_(1.0)
    rim.layer().setBorderColor_(
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.30).CGColor())
    pill.addSubview_(rim)

    # The top notch_h strip of the pill sits alongside the physical notch:
    # its center is hidden behind the notch but its left/right wings are
    # visible, so they carry the small meta text (the headline left, any
    # budget + location right) while the message title sits centered in the
    # lower, always-visible band directly under the notch.
    band_h = exp_h - notch_h
    strip_y = band_h
    wing_w = (exp_w - notch_w) / 2.0 - 28  # 16 pill margin + 12 notch clearance
    title = budget = location = None
    if THEME == "project":
        title, budget, location = parse_project(BODY)
    if title and wing_w >= 60:
        labels = [make_label(
            HEADLINE.strip().rstrip("!"),
            NSMakeRect(16, strip_y + (notch_h - 16) / 2.0, wing_w, 16),
            12, True, alpha=0.85, align=NSTextAlignmentLeft)]
        rx = exp_w - 16 - wing_w
        if budget and location:
            rows = [(budget, strip_y + notch_h / 2.0 + 1.0),
                    (location, strip_y + notch_h / 2.0 - 14.0)]
        else:
            rows = [(m, strip_y + (notch_h - 14) / 2.0)
                    for m in (budget, location) if m]
        for text, y in rows:
            labels.append(make_label(text, NSMakeRect(rx, y, wing_w, 14),
                                     11, False, alpha=0.8, align=NSTextAlignmentRight))
        t = make_label(title, NSMakeRect(20, 0, exp_w - 40, band_h - 10), 17, True)
        th = min(t.cell().cellSizeForBounds_(
            NSMakeRect(0, 0, exp_w - 40, 1000)).height, band_h - 10)
        t.setFrame_(NSMakeRect(20, (band_h - th) / 2.0, exp_w - 40, th))
        labels.append(t)
    else:
        # Warnings (and anything unparsable) keep the plain centered layout.
        body = " ".join(BODY.split())[:110]
        labels = [make_label(HEADLINE, NSMakeRect(20, band_h * 0.52, exp_w - 40, 20), 15, True)]
        if body:
            labels.append(make_label(body, NSMakeRect(20, 8, exp_w - 40, band_h * 0.42),
                                     12, False, alpha=0.75))
    for label in labels:
        label.setAlphaValue_(0.0)
        pill.addSubview_(label)
    content.addSubview_(pill)

    driver = IslandDriver.alloc().init()
    driver.pill = pill
    driver.halo = halo
    driver.labels = labels
    driver.start = start
    driver.end = end
    driver.collapsing = False
    driver.win = win
    driver.haloWin = haloWin
    driver.snapshotPath = os.environ.get("TELEGRAM_ALARM_SNAPSHOT")
    DRIVER = driver

    win.orderFrontRegardless()  # never steals keyboard focus
    haloWin.orderFrontRegardless()
    haloWin.orderWindow_relativeTo_(-1, win.windowNumber())  # NSWindowBelow
    if not enable_window_blur(win, 26):
        print("backdrop blur unavailable; falling back to tint only", file=sys.stderr)

    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.05, driver, "expandTimer:", None, False)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.3, driver, "fadeInTimer:", None, False)
    # No auto-collapse: the island stays until it's clicked.
    if driver.snapshotPath:
        snap_t = float(os.environ.get("TELEGRAM_ALARM_SNAPSHOT_T", "2.8"))
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            snap_t, driver, "snapshotTimer:", None, False)
    return win


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    if STYLE == "island":
        keep = island_main(app)
    else:
        keep = flash_main(app)

    from PyObjCTools import AppHelper
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
