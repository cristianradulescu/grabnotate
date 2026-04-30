"""Annotation toolbox widget.

A horizontal toolbar containing:
  - Tool toggle buttons: Rect, Line, Arrow, Highlight, Blur
  - A Gtk.ColorDialogButton showing and picking the active annotation color
  - A stroke-width SpinButton
  - Undo / Redo buttons
"""

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, Gtk


class AnnotationToolbox(Gtk.Box):
    # (tool name, label, tooltip)
    _TOOLS = [
        ("rectangle", "Rect",      "Rectangle (R)"),
        ("line",      "Line",      "Line (L)"),
        ("arrow",     "Arrow",     "Arrow (A)"),
        ("highlight", "Highlight", "Highlight (H)"),
        ("blur",      "Blur",      "Blur (B)"),
    ]

    def __init__(self, canvas):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.set_margin_start(8)
        self.set_margin_end(8)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        self._canvas = canvas
        self._active_tool: str = "rectangle"
        self._tool_buttons: dict[str, Gtk.ToggleButton] = {}

        # Tool toggle buttons
        last_btn = None
        for tool, label, tooltip in self._TOOLS:
            btn = Gtk.ToggleButton(label=label)
            btn.set_tooltip_text(tooltip)
            btn.set_active(tool == self._active_tool)
            if last_btn is not None:
                btn.set_group(last_btn)
            btn.connect("toggled", self._on_tool_toggled, tool)
            self.append(btn)
            self._tool_buttons[tool] = btn
            last_btn = btn

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_start(4)
        sep.set_margin_end(4)
        self.append(sep)

        # Color picker — Gtk.ColorDialogButton renders the chosen color itself
        initial_color = Gdk.RGBA()
        initial_color.parse("red")

        color_dialog = Gtk.ColorDialog()
        color_dialog.set_title("Pick annotation color")
        color_dialog.set_with_alpha(True)

        self._color_btn = Gtk.ColorDialogButton(dialog=color_dialog)
        self._color_btn.set_rgba(initial_color)
        self._color_btn.set_tooltip_text("Pick color (C)")
        self._color_btn.connect("notify::rgba", self._on_color_changed)
        self.append(self._color_btn)

        # Separator
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep2.set_margin_start(4)
        sep2.set_margin_end(4)
        self.append(sep2)

        # Stroke width label + SpinButton
        width_label = Gtk.Label(label="Width:")
        self.append(width_label)

        adj = Gtk.Adjustment(value=2.5, lower=1.0, upper=30.0, step_increment=0.5, page_increment=2.0)
        self._width_spin = Gtk.SpinButton(adjustment=adj, climb_rate=0.5, digits=1)
        self._width_spin.set_tooltip_text("Stroke width")
        self._width_spin.set_numeric(True)
        self._width_spin.connect("value-changed", self._on_width_changed)
        self.append(self._width_spin)

        # Separator
        sep3 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep3.set_margin_start(4)
        sep3.set_margin_end(4)
        self.append(sep3)

        # Undo / Redo buttons
        self._undo_btn = Gtk.Button(label="Undo")
        self._undo_btn.set_tooltip_text("Undo (Ctrl+Z)")
        self._undo_btn.set_sensitive(False)
        self._undo_btn.connect("clicked", self._on_undo_clicked)
        self.append(self._undo_btn)

        self._redo_btn = Gtk.Button(label="Redo")
        self._redo_btn.set_tooltip_text("Redo (Ctrl+Shift+Z)")
        self._redo_btn.set_sensitive(False)
        self._redo_btn.connect("clicked", self._on_redo_clicked)
        self.append(self._redo_btn)

        # Push canvas to initial state
        self._canvas.set_tool(self._active_tool)
        self._apply_color(initial_color)
        self._canvas.set_stroke_width(2.5)

        # Register keyboard shortcuts on the window once it is available
        self.connect("realize", self._on_realize)

    def _on_realize(self, _widget):
        window = self.get_root()
        app = window.get_application()

        shortcut_map = {
            "tool-rect":      ("r", "rectangle"),
            "tool-line":      ("l", "line"),
            "tool-arrow":     ("a", "arrow"),
            "tool-highlight": ("h", "highlight"),
            "tool-blur":      ("b", "blur"),
            "tool-color":     ("c", None),
            "undo":           ("<Control>z", None),
            "redo":           ("<Control><Shift>z", None),
        }

        for action_name, (key, tool) in shortcut_map.items():
            action = Gio.SimpleAction.new(action_name, None)
            if tool is not None:
                action.connect("activate", self._on_tool_action, tool)
            elif action_name == "undo":
                action.connect("activate", lambda *_: self._on_undo_clicked())
            elif action_name == "redo":
                action.connect("activate", lambda *_: self._on_redo_clicked())
            else:
                action.connect("activate", self._on_color_action)
            window.add_action(action)
            app.set_accels_for_action(f"win.{action_name}", [key])

    # ------------------------------------------------------------------
    # Tool selection
    # ------------------------------------------------------------------

    def _on_tool_toggled(self, btn: Gtk.ToggleButton, tool: str):
        if btn.get_active():
            self._active_tool = tool
            self._canvas.set_tool(tool)

    def _on_tool_action(self, _action, _param, tool: str):
        btn = self._tool_buttons[tool]
        btn.set_active(True)  # also triggers _on_tool_toggled

    # ------------------------------------------------------------------
    # Color picker
    # ------------------------------------------------------------------

    def _on_color_action(self, _action, _param):
        self._color_btn.activate()

    def _on_color_changed(self, btn, _param):
        self._apply_color(btn.get_rgba())

    def _apply_color(self, rgba: Gdk.RGBA):
        self._canvas.set_color(rgba.red, rgba.green, rgba.blue, rgba.alpha)

    # ------------------------------------------------------------------
    # Stroke width
    # ------------------------------------------------------------------

    def _on_width_changed(self, spin: Gtk.SpinButton):
        self._canvas.set_stroke_width(spin.get_value())

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def _on_undo_clicked(self, *_):
        self._canvas.undo()
        self._sync_history_buttons()

    def _on_redo_clicked(self, *_):
        self._canvas.redo()
        self._sync_history_buttons()

    def _sync_history_buttons(self):
        self._undo_btn.set_sensitive(self._canvas.can_undo())
        self._redo_btn.set_sensitive(self._canvas.can_redo())
