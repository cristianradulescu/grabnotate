"""Fullscreen overlay window for region selection.

Displays the full-screen capture as a dimmed background and lets the user:
  - Drag to create an initial selection rectangle
  - Drag the 8 resize handles to adjust any edge/corner
  - Drag inside the rectangle to move it
  - Use arrow keys / hjkl to move the selection (no modifier)
  - Use Shift + arrow/hjkl to resize by moving the bottom-right corner
  - Use Ctrl + arrow/hjkl to resize by moving the top-left corner
  - Press Space or double-click to confirm
  - Press Escape to cancel

Calls callback(x, y, width, height) in image-pixel coordinates on confirm,
or callback(None, None, None, None) on cancel.
"""

import math

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

# Size of the square resize handles in overlay pixels
_HANDLE_SIZE = 10
# How close the cursor needs to be (in px) to grab a handle
_HANDLE_HIT = 12


class _Mode:
    IDLE = "idle"
    DRAWING = "drawing"       # initial drag before a selection exists
    MOVING = "moving"         # dragging inside the selection
    RESIZING = "resizing"     # dragging a handle


class SelectionOverlay(Gtk.Window):
    def __init__(self, image_path: str, callback):
        super().__init__()
        self._image_path = image_path
        self._callback = callback

        # Selection in overlay coordinates (normalised: x<=x2, y<=y2)
        self._sel_x = 0.0
        self._sel_y = 0.0
        self._sel_x2 = 0.0
        self._sel_y2 = 0.0
        self._has_selection = False

        self._mode = _Mode.IDLE
        self._drag_start: tuple[float, float] = (0.0, 0.0)
        self._drag_sel_snapshot: tuple[float, float, float, float] = (0, 0, 0, 0)
        self._active_handle: int | None = None  # 0-7 index

        self._surface: cairo.ImageSurface | None = None
        self._default_selection_set = False

        self.set_decorated(False)
        self.fullscreen()

        self._drawing_area = Gtk.DrawingArea()
        self._drawing_area.set_draw_func(self._draw)
        self._drawing_area.connect("resize", self._on_drawing_area_resize)
        self.set_child(self._drawing_area)

        # Drag gesture — handles drawing, moving, resizing
        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self._drawing_area.add_controller(drag)

        # Double-click to confirm
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_click_pressed)
        self._drawing_area.add_controller(click)

        # Motion — update cursor shape
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self._drawing_area.add_controller(motion)

        # Keyboard
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)

        self.connect("realize", self._on_realize)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _on_realize(self, _widget):
        self._surface = cairo.ImageSurface.create_from_png(self._image_path)

    def _on_drawing_area_resize(self, _area, width: int, height: int):
        """Seed a default selection the first time we know the overlay size."""
        if self._default_selection_set or width < 10 or height < 10:
            return
        self._default_selection_set = True
        margin_x = width * 0.25
        margin_y = height * 0.25
        self._sel_x  = margin_x
        self._sel_y  = margin_y
        self._sel_x2 = width  - margin_x
        self._sel_y2 = height - margin_y
        self._has_selection = True
        self._drawing_area.queue_draw()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, _area, cr: cairo.Context, width: int, height: int):
        if self._surface is None:
            return

        img_w = self._surface.get_width()
        img_h = self._surface.get_height()
        scale_x = width / img_w if img_w else 1.0
        scale_y = height / img_h if img_h else 1.0

        # Background screenshot
        cr.save()
        cr.scale(scale_x, scale_y)
        cr.set_source_surface(self._surface, 0, 0)
        cr.paint()
        cr.restore()

        # Full-screen dim
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.paint()

        if not self._has_selection:
            self._draw_hint(cr, width, height)
            return

        rx, ry, rw, rh = self._sel_rect()

        # Undim the selection
        cr.save()
        cr.rectangle(rx, ry, rw, rh)
        cr.clip()
        cr.scale(scale_x, scale_y)
        cr.set_source_surface(self._surface, 0, 0)
        cr.paint()
        cr.restore()

        # Selection border
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(1.5)
        cr.rectangle(rx, ry, rw, rh)
        cr.stroke()

        # Resize handles
        for hx, hy in self._handle_positions(rx, ry, rw, rh):
            cr.set_source_rgba(1, 1, 1, 1.0)
            cr.rectangle(hx - _HANDLE_SIZE / 2, hy - _HANDLE_SIZE / 2, _HANDLE_SIZE, _HANDLE_SIZE)
            cr.fill()
            cr.set_source_rgba(0.2, 0.2, 0.2, 1.0)
            cr.set_line_width(1.0)
            cr.rectangle(hx - _HANDLE_SIZE / 2, hy - _HANDLE_SIZE / 2, _HANDLE_SIZE, _HANDLE_SIZE)
            cr.stroke()

        # Confirm hint bar
        self._draw_confirm_hint(cr, rx, ry, rw, rh, width, height)

    def _draw_hint(self, cr: cairo.Context, width: int, height: int):
        """Initial instruction shown before any selection is drawn."""
        text = "Drag to select a region  ·  Arrows/hjkl to move  ·  Shift+arrows to resize"
        cr.set_font_size(18)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        extents = cr.text_extents(text)
        tx = (width - extents.width) / 2
        ty = height / 2
        cr.set_source_rgba(0, 0, 0, 0.5)
        cr.rectangle(tx - 16, ty - extents.height - 10, extents.width + 32, extents.height + 20)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.move_to(tx, ty)
        cr.show_text(text)

    def _draw_confirm_hint(self, cr, rx, ry, rw, rh, win_w, win_h):
        """Small tooltip below (or above) the selection: 'Enter to confirm · Esc to cancel'."""
        text = "Space to confirm  ·  Esc to cancel  ·  Arrows/hjkl to move  ·  Shift+arrows to resize BR  ·  Ctrl+arrows to resize TL"
        cr.set_font_size(13)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        extents = cr.text_extents(text)
        pad = 8
        bw = extents.width + pad * 2
        bh = extents.height + pad * 2
        bx = rx + (rw - bw) / 2
        # Place below selection, but flip above if it would go off-screen
        by = ry + rh + 8
        if by + bh > win_h - 4:
            by = ry - bh - 8
        bx = max(4, min(bx, win_w - bw - 4))

        cr.set_source_rgba(0.1, 0.1, 0.1, 0.82)
        _rounded_rect(cr, bx, by, bw, bh, 5)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.move_to(bx + pad, by + pad + extents.height)
        cr.show_text(text)

    # ------------------------------------------------------------------
    # Handle positions (8 handles: 4 corners + 4 edge midpoints)
    # Order: TL, T, TR, R, BR, B, BL, L
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_positions(rx, ry, rw, rh):
        cx = rx + rw / 2
        cy = ry + rh / 2
        return [
            (rx,      ry),       # 0 TL
            (cx,      ry),       # 1 T
            (rx + rw, ry),       # 2 TR
            (rx + rw, cy),       # 3 R
            (rx + rw, ry + rh),  # 4 BR
            (cx,      ry + rh),  # 5 B
            (rx,      ry + rh),  # 6 BL
            (rx,      cy),       # 7 L
        ]

    def _hit_handle(self, mx, my) -> int | None:
        if not self._has_selection:
            return None
        rx, ry, rw, rh = self._sel_rect()
        for i, (hx, hy) in enumerate(self._handle_positions(rx, ry, rw, rh)):
            if math.hypot(mx - hx, my - hy) <= _HANDLE_HIT:
                return i
        return None

    def _inside_selection(self, mx, my) -> bool:
        if not self._has_selection:
            return False
        rx, ry, rw, rh = self._sel_rect()
        return rx <= mx <= rx + rw and ry <= my <= ry + rh

    # ------------------------------------------------------------------
    # Drag input
    # ------------------------------------------------------------------

    def _on_drag_begin(self, gesture, start_x, start_y):
        handle = self._hit_handle(start_x, start_y)
        if handle is not None:
            self._mode = _Mode.RESIZING
            self._active_handle = handle
        elif self._inside_selection(start_x, start_y):
            self._mode = _Mode.MOVING
        else:
            self._mode = _Mode.DRAWING
            self._sel_x = start_x
            self._sel_y = start_y
            self._sel_x2 = start_x
            self._sel_y2 = start_y
            self._has_selection = True

        self._drag_start = (start_x, start_y)
        self._drag_sel_snapshot = (self._sel_x, self._sel_y, self._sel_x2, self._sel_y2)
        self._drawing_area.queue_draw()

    def _on_drag_update(self, gesture, offset_x, offset_y):
        sx, sy = self._drag_start
        cx = sx + offset_x
        cy = sy + offset_y
        snap_x, snap_y, snap_x2, snap_y2 = self._drag_sel_snapshot

        if self._mode == _Mode.DRAWING:
            self._sel_x = sx
            self._sel_y = sy
            self._sel_x2 = cx
            self._sel_y2 = cy

        elif self._mode == _Mode.MOVING:
            dx = offset_x
            dy = offset_y
            self._sel_x = snap_x + dx
            self._sel_y = snap_y + dy
            self._sel_x2 = snap_x2 + dx
            self._sel_y2 = snap_y2 + dy

        elif self._mode == _Mode.RESIZING:
            # Normalised snapshot coords
            rx = min(snap_x, snap_x2)
            ry = min(snap_y, snap_y2)
            rx2 = max(snap_x, snap_x2)
            ry2 = max(snap_y, snap_y2)
            h = self._active_handle
            # Each handle moves specific edges
            if h in (0, 6, 7):   rx  = cx   # left edge
            if h in (2, 3, 4):   rx2 = cx   # right edge
            if h in (0, 1, 2):   ry  = cy   # top edge
            if h in (4, 5, 6):   ry2 = cy   # bottom edge
            if h == 1:           ry  = cy   # top-mid
            if h == 5:           ry2 = cy   # bottom-mid
            if h == 7:           rx  = cx   # left-mid
            if h == 3:           rx2 = cx   # right-mid
            self._sel_x  = rx
            self._sel_y  = ry
            self._sel_x2 = rx2
            self._sel_y2 = ry2

        self._drawing_area.queue_draw()

    def _on_drag_end(self, gesture, offset_x, offset_y):
        # Normalise so sel_x < sel_x2 etc. after any drag
        rx, ry, rw, rh = self._sel_rect()
        self._sel_x  = rx
        self._sel_y  = ry
        self._sel_x2 = rx + rw
        self._sel_y2 = ry + rh
        if rw < 4 or rh < 4:
            self._has_selection = False
        self._mode = _Mode.IDLE
        self._drawing_area.queue_draw()

    # ------------------------------------------------------------------
    # Click (double-click to confirm)
    # ------------------------------------------------------------------

    def _on_click_pressed(self, gesture, n_press, x, y):
        if n_press == 2 and self._has_selection:
            self._confirm()

    # ------------------------------------------------------------------
    # Motion — cursor shape
    # ------------------------------------------------------------------

    _HANDLE_CURSORS = [
        "nw-resize", "n-resize", "ne-resize",
        "e-resize",
        "se-resize", "s-resize", "sw-resize",
        "w-resize",
    ]

    def _on_motion(self, _ctrl, x, y):
        handle = self._hit_handle(x, y)
        if handle is not None:
            name = self._HANDLE_CURSORS[handle]
        elif self._inside_selection(x, y):
            name = "move"
        else:
            name = "crosshair"
        self._drawing_area.set_cursor(Gdk.Cursor.new_from_name(name))

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    # Step sizes for nudge: normal and large (Shift held while also using Ctrl,
    # or just for coarse movement with no modifier)
    _NUDGE_SMALL = 1
    _NUDGE_LARGE = 10

    def _on_key_pressed(self, _ctrl, keyval, _keycode, state):
        if keyval == Gdk.KEY_space:
            if self._has_selection:
                self._confirm()
            return True
        if keyval == Gdk.KEY_Escape:
            self._cancel()
            return True

        # Arrow / hjkl nudge
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        ctrl  = bool(state & Gdk.ModifierType.CONTROL_MASK)

        dx, dy = self._keyval_to_delta(keyval)
        if dx == 0 and dy == 0:
            return False

        if not self._has_selection:
            return True

        # Determine intent from modifier keys (arrows) or keyval case (hjkl)
        is_upper_hjkl = keyval in (Gdk.KEY_H, Gdk.KEY_J, Gdk.KEY_K, Gdk.KEY_L)
        is_ctrl_hjkl  = keyval in (0x08, 0x0a, 0x0b, 0x0c)

        do_shift = shift or is_upper_hjkl
        do_ctrl  = ctrl  or is_ctrl_hjkl

        # Shift (or Shift+Ctrl) → large step; plain Ctrl or no modifier → small step
        step = self._NUDGE_LARGE if do_shift else self._NUDGE_SMALL

        if do_ctrl:
            # Move top-left corner only (resize from the start)
            self._sel_x  = self._clamp_x(self._sel_x  + dx * step)
            self._sel_y  = self._clamp_y(self._sel_y  + dy * step)
        elif do_shift:
            # Move bottom-right corner only (resize from the end)
            self._sel_x2 = self._clamp_x(self._sel_x2 + dx * step)
            self._sel_y2 = self._clamp_y(self._sel_y2 + dy * step)
        else:
            # Move the whole selection
            aw = self._drawing_area.get_width()
            ah = self._drawing_area.get_height()
            rx, ry, rw, rh = self._sel_rect()
            nx = max(0.0, min(rx + dx * step, aw - rw))
            ny = max(0.0, min(ry + dy * step, ah - rh))
            self._sel_x  = nx
            self._sel_y  = ny
            self._sel_x2 = nx + rw
            self._sel_y2 = ny + rh

        # Re-normalise
        rx, ry, rw, rh = self._sel_rect()
        self._sel_x  = rx
        self._sel_y  = ry
        self._sel_x2 = rx + rw
        self._sel_y2 = ry + rh

        self._drawing_area.queue_draw()
        return True

    @staticmethod
    def _keyval_to_delta(keyval: int) -> tuple[int, int]:
        """Return (dx, dy) for arrow keys and hjkl; (0,0) for anything else."""
        return {
            Gdk.KEY_Left:  (-1,  0),
            Gdk.KEY_Right: ( 1,  0),
            Gdk.KEY_Up:    ( 0, -1),
            Gdk.KEY_Down:  ( 0,  1),
            # lowercase
            Gdk.KEY_h:     (-1,  0),
            Gdk.KEY_l:     ( 1,  0),
            Gdk.KEY_k:     ( 0, -1),
            Gdk.KEY_j:     ( 0,  1),
            # uppercase (Shift+hjkl)
            Gdk.KEY_H:     (-1,  0),
            Gdk.KEY_L:     ( 1,  0),
            Gdk.KEY_K:     ( 0, -1),
            Gdk.KEY_J:     ( 0,  1),
            # Ctrl+hjkl produces ASCII control codes
            0x08:          (-1,  0),  # Ctrl+h (BS)
            0x0c:          ( 1,  0),  # Ctrl+l (FF)
            0x0b:          ( 0, -1),  # Ctrl+k (VT)
            0x0a:          ( 0,  1),  # Ctrl+j (LF)
        }.get(keyval, (0, 0))

    def _clamp_x(self, v: float) -> float:
        return max(0.0, min(v, float(self._drawing_area.get_width())))

    def _clamp_y(self, v: float) -> float:
        return max(0.0, min(v, float(self._drawing_area.get_height())))

    # ------------------------------------------------------------------
    # Confirm / cancel
    # ------------------------------------------------------------------

    def _confirm(self):
        rx, ry, rw, rh = self._sel_rect()
        self.close()
        aw = self._drawing_area.get_width()
        ah = self._drawing_area.get_height()
        if self._surface and aw and ah:
            sx = self._surface.get_width() / aw
            sy = self._surface.get_height() / ah
            self._callback(int(rx * sx), int(ry * sy), int(rw * sx), int(rh * sy))
        else:
            self._callback(int(rx), int(ry), int(rw), int(rh))

    def _cancel(self):
        self.close()
        self._callback(None, None, None, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sel_rect(self):
        """Return (x, y, w, h) always positive."""
        x = min(self._sel_x, self._sel_x2)
        y = min(self._sel_y, self._sel_y2)
        w = abs(self._sel_x2 - self._sel_x)
        h = abs(self._sel_y2 - self._sel_y)
        return x, y, w, h


def _rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r,     r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0,             math.pi / 2)
    cr.arc(x + r,     y + h - r, r, math.pi / 2,   math.pi)
    cr.arc(x + r,     y + r,     r, math.pi,        3 * math.pi / 2)
    cr.close_path()
