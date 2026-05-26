import time
import threading
from typing import Optional, Callable

import AppKit
import Quartz

from ..models import make_observation, CaptureEvent


class ScreenCapture:
    def __init__(self, config: dict, callback: Callable[[CaptureEvent], None],
                 save_screenshot_fn: Callable[[bytes], str]):
        self.config = config["capture"]["screenshot"]
        self.callback = callback
        self.save_screenshot = save_screenshot_fn
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _capture_loop(self):
        while self._running:
            start = time.time()
            try:
                event = self._capture_screenshot()
                if event:
                    self.callback(event)
            except Exception:
                pass
            elapsed = time.time() - start
            sleep_for = max(0, self.config["interval_seconds"] - elapsed)
            time.sleep(sleep_for)

    def _capture_screenshot(self) -> Optional[CaptureEvent]:
        try:
            display_id = Quartz.CGMainDisplayID()
            if display_id == 0:
                return None

            cg_image = Quartz.CGDisplayCreateImage(display_id)
            if cg_image is None:
                return None

            pixel_w = Quartz.CGImageGetWidth(cg_image)
            pixel_h = Quartz.CGImageGetHeight(cg_image)

            max_w = self.config.get("max_width", 0)
            if max_w and pixel_w > max_w:
                scale = max_w / pixel_w
                new_w = int(pixel_w * scale)
                new_h = int(pixel_h * scale)
                cg_image = _scale_cgimage(cg_image, new_w, new_h)

            rep = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(cg_image)
            png_data = rep.representationUsingType_properties_(
                AppKit.NSPNGFileType, None
            )
            screenshot_rel_path = self.save_screenshot(bytes(png_data))

            timestamp = time.time()
            return make_observation(
                timestamp=timestamp,
                session_id="",
                sequence_id=0,
                screenshot_path=screenshot_rel_path,
            )
        except Exception:
            return None


def _scale_cgimage(cg_image, new_w, new_h):
    colorspace = Quartz.CGColorSpaceCreateDeviceRGB()
    context = Quartz.CGBitmapContextCreate(
        None, new_w, new_h, 8, 0,
        colorspace, Quartz.kCGImageAlphaPremultipliedFirst,
    )
    Quartz.CGContextSetInterpolationQuality(context, Quartz.kCGInterpolationHigh)
    Quartz.CGContextDrawImage(
        context,
        Quartz.CGRectMake(0, 0, new_w, new_h),
        cg_image,
    )
    return Quartz.CGBitmapContextCreateImage(context)
