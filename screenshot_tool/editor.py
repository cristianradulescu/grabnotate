"""Annotation canvas.

Displays the cropped screenshot and lets the user draw annotations on top
using Cairo. Annotations are rendered onto a separate overlay surface and
composited at draw time, so the original image is never modified until export.

Supported tools (set via .set_tool()):
  - "rectangle"  draw outlined rectangles
  - "line"       draw straight lines
  - "arrow"      draw a line with an arrowhead at the end
  - "highlight"  draw a semi-transparent filled rectangle
  - "blur"       drag a region to apply a Gaussian-style box blur

Active colour is set via .set_color(r, g, b, a).
Stroke width is set via .set_stroke_width(px).
"""

import math
import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

# Default stroke width for rectangle, line, and arrow tools
_DEFAULT_STROKE_WIDTH = 2.5
# Number of blur passes (more = stronger blur)
_BLUR_PASSES = 3
# Blur kernel radius per pass
_BLUR_RADIUS = 4


class AnnotationCanvas(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)

        self._photo_surface: cairo.ImageSurface | None = None
        self._annotation_surface: cairo.ImageSurface | None = None

        # Undo/redo stacks hold raw bytes snapshots of the annotation surface
        self._undo_stack: list[bytes] = []
        self._redo_stack: list[bytes] = []

        # Called with no arguments after every stroke commit or undo/redo
        self.on_history_changed = None

        self._tool: str = "rectangle"
        self._color: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 1.0)
        self._stroke_width: float = _DEFAULT_STROKE_WIDTH

        # Current in-progress stroke (not yet committed)
        self._drag_start: tuple[float, float] | None = None
        self._drag_current: tuple[float, float] | None = None
        self._dragging = False

        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_image(self, path: str):
        """Load a PNG file as the base image and reset annotations."""
        self._photo_surface = cairo.ImageSurface.create_from_png(path)
        w = self._photo_surface.get_width()
        h = self._photo_surface.get_height()
        self._annotation_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.set_content_width(w)
        self.set_content_height(h)
        self.queue_draw()

    def set_tool(self, tool: str):
        """Set active tool: 'rectangle', 'line', 'arrow', 'highlight', or 'blur'."""
        self._tool = tool

    def set_color(self, r: float, g: float, b: float, a: float = 1.0):
        self._color = (r, g, b, a)

    def set_stroke_width(self, width: float):
        self._stroke_width = max(1.0, width)

    def get_stroke_width(self) -> float:
        return self._stroke_width

    def undo(self):
        if not self._undo_stack or self._annotation_surface is None:
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self._notify_history()
        self.queue_draw()

    def redo(self):
        if not self._redo_stack or self._annotation_surface is None:
            return
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self._notify_history()
        self.queue_draw()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def _snapshot(self) -> bytes:
        """Return a copy of the current annotation surface pixel data."""
        self._annotation_surface.flush()
        return bytes(self._annotation_surface.get_data())

    def _restore(self, data: bytes):
        """Overwrite the annotation surface with previously snapshotted data."""
        self._annotation_surface.flush()
        buf = self._annotation_surface.get_data()
        buf[:] = data

    def _notify_history(self):
        if callable(self.on_history_changed):
            self.on_history_changed()

    def get_flat_surface(self) -> cairo.ImageSurface | None:
        """Return a composited surface (photo + annotations) for export."""
        if self._photo_surface is None:
            return None
        w = self._photo_surface.get_width()
        h = self._photo_surface.get_height()
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(out)
        cr.set_source_surface(self._photo_surface, 0, 0)
        cr.paint()
        if self._annotation_surface:
            cr.set_source_surface(self._annotation_surface, 0, 0)
            cr.paint()
        return out

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, _area, cr: cairo.Context, width: int, height: int):
        if self._photo_surface is None:
            return

        img_w = self._photo_surface.get_width()
        img_h = self._photo_surface.get_height()
        scale = min(width / img_w, height / img_h) if img_w and img_h else 1.0
        off_x = (width - img_w * scale) / 2
        off_y = (height - img_h * scale) / 2

        cr.save()
        cr.translate(off_x, off_y)
        cr.scale(scale, scale)

        # Base image
        cr.set_source_surface(self._photo_surface, 0, 0)
        cr.paint()

        # Committed annotations
        if self._annotation_surface:
            cr.set_source_surface(self._annotation_surface, 0, 0)
            cr.paint()

        # In-progress stroke preview
        if self._dragging and self._drag_start and self._drag_current:
            sx, sy = self._drag_start
            cx, cy = self._drag_current
            self._draw_shape(cr, sx, sy, cx, cy, preview=True)

        cr.restore()

    def _draw_shape(self, cr: cairo.Context, sx, sy, cx, cy, preview=False):
        r, g, b, a = self._color
        if preview:
            a *= 0.75

        if self._tool == "rectangle":
            x = min(sx, cx)
            y = min(sy, cy)
            w = abs(cx - sx)
            h = abs(cy - sy)
            cr.set_source_rgba(r, g, b, a)
            cr.set_line_width(self._stroke_width)
            cr.rectangle(x, y, w, h)
            cr.stroke()

        elif self._tool == "line":
            cr.set_source_rgba(r, g, b, a)
            cr.set_line_width(self._stroke_width)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(sx, sy)
            cr.line_to(cx, cy)
            cr.stroke()

        elif self._tool == "arrow":
            self._draw_arrow(cr, sx, sy, cx, cy, r, g, b, a)

        elif self._tool == "highlight":
            x = min(sx, cx)
            y = min(sy, cy)
            w = abs(cx - sx)
            h = abs(cy - sy)
            cr.set_source_rgba(r, g, b, a * 0.4)
            cr.rectangle(x, y, w, h)
            cr.fill()

        elif self._tool == "blur":
            self._draw_blur(cr, sx, sy, cx, cy)

    def _draw_arrow(self, cr: cairo.Context, sx, sy, cx, cy, r, g, b, a):
        """Draw a line from (sx,sy) to (cx,cy) with an arrowhead at the end."""
        cr.set_source_rgba(r, g, b, a)
        cr.set_line_width(self._stroke_width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        # Shaft
        cr.move_to(sx, sy)
        cr.line_to(cx, cy)
        cr.stroke()

        # Arrowhead: two lines fanning back from the tip
        angle = math.atan2(cy - sy, cx - sx)
        head_len = max(12.0, self._stroke_width * 5)
        head_angle = math.pi / 6  # 30°

        for side in (+head_angle, -head_angle):
            bx = cx - head_len * math.cos(angle - side)
            by = cy - head_len * math.sin(angle - side)
            cr.move_to(cx, cy)
            cr.line_to(bx, by)
            cr.stroke()

    def _draw_blur(self, cr: cairo.Context, sx, sy, cx, cy):
        """Apply a box blur to the photo region defined by the drag rectangle."""
        if self._photo_surface is None:
            return

        img_w = self._photo_surface.get_width()
        img_h = self._photo_surface.get_height()

        x = max(0, min(int(min(sx, cx)), img_w - 1))
        y = max(0, min(int(min(sy, cy)), img_h - 1))
        w = max(1, min(int(abs(cx - sx)), img_w - x))
        h = max(1, min(int(abs(cy - sy)), img_h - y))

        # Extract the region from the photo into a small ARGB surface
        region = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        rcr = cairo.Context(region)
        rcr.set_source_surface(self._photo_surface, -x, -y)
        rcr.paint()
        region.flush()

        # Also composite committed annotations into the region before blurring
        if self._annotation_surface:
            rcr.set_source_surface(self._annotation_surface, -x, -y)
            rcr.paint()
        region.flush()

        # Apply a simple box blur by repeated downscale + upscale via Cairo
        buf = bytearray(region.get_data())
        stride = region.get_stride()
        _box_blur_argb(buf, w, h, stride, _BLUR_RADIUS, _BLUR_PASSES)
        region.finish()

        # Write blurred pixels into the region surface
        blurred = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        blurred.flush()
        dst = blurred.get_data()
        dst[:] = bytes(buf)
        blurred.mark_dirty()

        # Paint the blurred region onto the annotation surface at the right offset
        cr.set_source_surface(blurred, x, y)
        cr.rectangle(x, y, w, h)
        cr.fill()

    # ------------------------------------------------------------------
    # Coordinate conversion (overlay px → image px)
    # ------------------------------------------------------------------

    def _to_image_coords(self, ox: float, oy: float) -> tuple[float, float]:
        if self._photo_surface is None:
            return ox, oy
        img_w = self._photo_surface.get_width()
        img_h = self._photo_surface.get_height()
        w = self.get_width()
        h = self.get_height()
        scale = min(w / img_w, h / img_h) if img_w and img_h else 1.0
        off_x = (w - img_w * scale) / 2
        off_y = (h - img_h * scale) / 2
        return (ox - off_x) / scale, (oy - off_y) / scale

    # ------------------------------------------------------------------
    # Drag input
    # ------------------------------------------------------------------

    def _on_drag_begin(self, _gesture, x, y):
        self._drag_start = self._to_image_coords(x, y)
        self._drag_current = self._drag_start
        self._dragging = True
        self.queue_draw()

    def _on_drag_update(self, _gesture, offset_x, offset_y):
        if self._drag_start is None:
            return
        sx, sy = self._drag_start
        w = self.get_width()
        h = self.get_height()
        if self._photo_surface:
            img_w = self._photo_surface.get_width()
            img_h = self._photo_surface.get_height()
            scale = min(w / img_w, h / img_h) if img_w and img_h else 1.0
        else:
            scale = 1.0
        self._drag_current = (sx + offset_x / scale, sy + offset_y / scale)
        self.queue_draw()

    def _on_drag_end(self, _gesture, offset_x, offset_y):
        if self._drag_start is None:
            return
        sx, sy = self._drag_start
        w = self.get_width()
        h = self.get_height()
        if self._photo_surface:
            img_w = self._photo_surface.get_width()
            img_h = self._photo_surface.get_height()
            scale = min(w / img_w, h / img_h) if img_w and img_h else 1.0
        else:
            scale = 1.0
        cx = sx + offset_x / scale
        cy = sy + offset_y / scale

        # Commit the stroke to the annotation surface
        if self._annotation_surface:
            self._undo_stack.append(self._snapshot())
            self._redo_stack.clear()
            cr = cairo.Context(self._annotation_surface)
            self._draw_shape(cr, sx, sy, cx, cy, preview=False)
            self._notify_history()

        self._dragging = False
        self._drag_start = None
        self._drag_current = None
        self.queue_draw()


# ------------------------------------------------------------------
# Pure-Python box blur on a flat ARGB bytearray
# ------------------------------------------------------------------

def _box_blur_argb(buf: bytearray, w: int, h: int, stride: int, radius: int, passes: int):
    """In-place horizontal+vertical box blur on ARGB32 pixel data."""
    for _ in range(passes):
        _box_blur_h(buf, w, h, stride, radius)
        _box_blur_v(buf, w, h, stride, radius)


def _box_blur_h(buf: bytearray, w: int, h: int, stride: int, r: int):
    for y in range(h):
        row_off = y * stride
        # Sliding window sums for each channel
        sums = [0, 0, 0, 0]
        count = 0
        # Seed with the first r+1 pixels
        for x in range(min(r + 1, w)):
            off = row_off + x * 4
            for c in range(4):
                sums[c] += buf[off + c]
            count += 1
        for x in range(w):
            # Write average
            off = row_off + x * 4
            for c in range(4):
                buf[off + c] = sums[c] // count
            # Advance window: add pixel at x+r+1
            add_x = x + r + 1
            if add_x < w:
                aoff = row_off + add_x * 4
                for c in range(4):
                    sums[c] += buf[aoff + c]
                count += 1
            # Remove pixel at x-r
            rem_x = x - r
            if rem_x >= 0:
                roff = row_off + rem_x * 4
                for c in range(4):
                    sums[c] -= buf[roff + c]
                count -= 1


def _box_blur_v(buf: bytearray, w: int, h: int, stride: int, r: int):
    for x in range(w):
        sums = [0, 0, 0, 0]
        count = 0
        for y in range(min(r + 1, h)):
            off = y * stride + x * 4
            for c in range(4):
                sums[c] += buf[off + c]
            count += 1
        for y in range(h):
            off = y * stride + x * 4
            for c in range(4):
                buf[off + c] = sums[c] // count
            add_y = y + r + 1
            if add_y < h:
                aoff = add_y * stride + x * 4
                for c in range(4):
                    sums[c] += buf[aoff + c]
                count += 1
            rem_y = y - r
            if rem_y >= 0:
                roff = rem_y * stride + x * 4
                for c in range(4):
                    sums[c] -= buf[roff + c]
                count -= 1
