import os
import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gdk, GdkPixbuf, Gio, GObject, Gtk

from screenshot_tool import screenshot
from screenshot_tool.overlay import SelectionOverlay
from screenshot_tool.editor import AnnotationCanvas
from screenshot_tool.toolbox import AnnotationToolbox


class ScreenshotToolApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="app.grabnotate",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._start_in_select_mode = False
        self.add_main_option(
            "select", ord("s"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Skip the main window and go straight to region capture",
            None,
        )
        self.connect("activate", self._on_activate)
        self.connect("command-line", self._on_command_line)

    def _on_command_line(self, _app, command_line):
        options = command_line.get_options_dict()
        self._start_in_select_mode = options.contains("select")
        self.activate()
        return 0

    def _on_activate(self, app):
        # Reuse an existing window if one is already open
        win = self.get_active_window()
        if win is None:
            win = MainWindow(application=app)
            self.set_accels_for_action("win.capture",   ["<Control><Shift>s"])
            self.set_accels_for_action("win.copy",      ["<Control>c"])
            self.set_accels_for_action("win.save",      ["<Control>s"])
            self.set_accels_for_action("win.shortcuts", ["question", "F1"])

        if self._start_in_select_mode:
            self._start_in_select_mode = False
            win.start_capture()
        else:
            win.present()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Grabnotate")
        self.set_default_size(800, 600)

        self._fullscreen_path: str | None = None
        self._current_path: str | None = None

        # Main layout
        self._toolbar_view = Adw.ToolbarView()
        self.set_content(self._toolbar_view)

        # Header bar
        header = Adw.HeaderBar()
        self._toolbar_view.add_top_bar(header)

        # Capture button
        self._capture_btn = Gtk.Button(label="Capture")
        self._capture_btn.add_css_class("suggested-action")
        self._capture_btn.connect("clicked", self._on_capture_clicked)
        header.pack_start(self._capture_btn)

        # Copy and Save buttons (end of header, disabled until a capture exists)
        self._copy_btn = Gtk.Button(label="Copy")
        self._copy_btn.set_tooltip_text("Copy to clipboard (Ctrl+C)")
        self._copy_btn.set_sensitive(False)
        self._copy_btn.connect("clicked", self._on_copy_clicked)
        header.pack_end(self._copy_btn)

        self._save_btn = Gtk.Button(label="Save")
        self._save_btn.set_tooltip_text("Save to file (Ctrl+S)")
        self._save_btn.set_sensitive(False)
        self._save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_btn)

        # Shortcuts help button
        shortcuts_btn = Gtk.Button(label="?")
        shortcuts_btn.set_tooltip_text("Keyboard shortcuts (? or F1)")
        shortcuts_btn.connect("clicked", self._on_shortcuts_clicked)
        header.pack_end(shortcuts_btn)

        # Status page (shown before any capture)
        self._status_page = Adw.StatusPage()
        self._status_page.set_title("Grabnotate")
        self._status_page.set_description("Click Capture or press Ctrl+Shift+S")
        self._status_page.set_icon_name("camera-photo-symbolic")

        # Editor: canvas + toolbox (shown after capture)
        self._canvas = AnnotationCanvas()
        self._toolbox = AnnotationToolbox(self._canvas)
        self._canvas.on_history_changed = self._toolbox._sync_history_buttons

        editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        editor_box.append(self._toolbox)
        editor_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self._canvas)
        editor_box.append(scroll)
        self._editor_box = editor_box

        self._toolbar_view.set_content(self._status_page)

        # Named actions so accelerators can target them
        capture_action = Gio.SimpleAction.new("capture", None)
        capture_action.connect("activate", self._on_capture_clicked)
        self.add_action(capture_action)

        copy_action = Gio.SimpleAction.new("copy", None)
        copy_action.set_enabled(False)
        copy_action.connect("activate", self._on_copy_clicked)
        self.add_action(copy_action)
        self._copy_action = copy_action

        save_action = Gio.SimpleAction.new("save", None)
        save_action.set_enabled(False)
        save_action.connect("activate", self._on_save_clicked)
        self.add_action(save_action)
        self._save_action = save_action

        shortcuts_action = Gio.SimpleAction.new("shortcuts", None)
        shortcuts_action.connect("activate", self._on_shortcuts_clicked)
        self.add_action(shortcuts_action)

    # ------------------------------------------------------------------
    # Step 1: hide window, request full-screen capture via portal
    # ------------------------------------------------------------------

    def start_capture(self):
        """Begin a capture (callable directly, without a button event)."""
        self._capture_btn.set_sensitive(False)
        self.set_visible(False)
        GLib.timeout_add(200, self._start_capture)

    def _on_capture_clicked(self, _source, _param=None):
        self.start_capture()

    def _start_capture(self):
        screenshot.capture_fullscreen(self._on_fullscreen_captured)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Step 2: full-screen PNG received — show selection overlay
    # ------------------------------------------------------------------

    def _on_fullscreen_captured(self, path: str | None, error: str | None):
        if error:
            self.set_visible(True)
            self.present()
            self._capture_btn.set_sensitive(True)
            self._show_error(error)
            return GLib.SOURCE_REMOVE

        self._fullscreen_path = path

        overlay = SelectionOverlay(path, self._on_region_selected)
        overlay.set_application(self.get_application())
        overlay.present()

        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Step 3: user selected a region — crop and display
    # ------------------------------------------------------------------

    def _on_region_selected(self, x, y, width, height):
        self.set_visible(True)
        self.present()
        self._capture_btn.set_sensitive(True)

        if x is None:
            # Cancelled
            self._cleanup_fullscreen()
            return

        try:
            cropped_path = self._crop(self._fullscreen_path, x, y, width, height)
            self._current_path = cropped_path
            self._canvas.load_image(cropped_path)
            self._toolbar_view.set_content(self._editor_box)
            self._copy_btn.set_sensitive(True)
            self._save_btn.set_sensitive(True)
            self._copy_action.set_enabled(True)
            self._save_action.set_enabled(True)
            self._resize_to_image(width, height)
        except Exception as exc:
            self._show_error(str(exc))
        finally:
            self._cleanup_fullscreen()

    def _crop(self, source_path: str, x: int, y: int, width: int, height: int) -> str:
        import tempfile
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(source_path)
        # Clamp to image bounds
        img_w = pixbuf.get_width()
        img_h = pixbuf.get_height()
        x = max(0, min(x, img_w - 1))
        y = max(0, min(y, img_h - 1))
        width = max(1, min(width, img_w - x))
        height = max(1, min(height, img_h - y))
        cropped = GdkPixbuf.Pixbuf.new_subpixbuf(pixbuf, x, y, width, height)
        fd, path = tempfile.mkstemp(prefix="screenshot-tool-crop-", suffix=".png")
        os.close(fd)
        cropped.savev(path, "png", [], [])
        return path

    def _resize_to_image(self, img_w: int, img_h: int):
        """Resize the window to fit img_w × img_h, capped to the monitor work area."""
        # Extra vertical space for header bar + toolbox + separator (rough estimate)
        chrome_h = 120

        # Try to get the monitor's work area so we don't go off-screen
        display = self.get_display()
        monitor = display.get_monitor_at_surface(self.get_surface()) if self.get_surface() else None
        if monitor is None and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)

        if monitor is not None:
            geom = monitor.get_geometry()
            max_w = geom.width
            max_h = geom.height - chrome_h
        else:
            max_w = 1920
            max_h = 1080

        new_w = min(img_w, max_w)
        new_h = min(img_h + chrome_h, max_h + chrome_h)
        self.set_default_size(new_w, new_h)

    def _cleanup_fullscreen(self):
        if self._fullscreen_path and os.path.exists(self._fullscreen_path):
            os.unlink(self._fullscreen_path)
        self._fullscreen_path = None

    # ------------------------------------------------------------------
    # Keyboard shortcuts window
    # ------------------------------------------------------------------

    def _on_shortcuts_clicked(self, *_):
        win = self._build_shortcuts_window()
        win.set_transient_for(self)
        win.present()

    def _build_shortcuts_window(self) -> Gtk.ShortcutsWindow:
        # Section: Capture
        capture_group = Gtk.ShortcutsGroup(title="Capture")
        capture_group.append(Gtk.ShortcutsShortcut(
            title="Take screenshot", accelerator="<Control><Shift>s"))

        # Section: Editor tools
        tools_group = Gtk.ShortcutsGroup(title="Annotation tools")
        tools_group.append(Gtk.ShortcutsShortcut(title="Rectangle tool",  accelerator="r"))
        tools_group.append(Gtk.ShortcutsShortcut(title="Line tool",       accelerator="l"))
        tools_group.append(Gtk.ShortcutsShortcut(title="Arrow tool",      accelerator="a"))
        tools_group.append(Gtk.ShortcutsShortcut(title="Highlight tool",  accelerator="h"))
        tools_group.append(Gtk.ShortcutsShortcut(title="Blur tool",       accelerator="b"))
        tools_group.append(Gtk.ShortcutsShortcut(title="Pick color",      accelerator="c"))

        # Section: History
        history_group = Gtk.ShortcutsGroup(title="History")
        history_group.append(Gtk.ShortcutsShortcut(title="Undo", accelerator="<Control>z"))
        history_group.append(Gtk.ShortcutsShortcut(title="Redo", accelerator="<Control><Shift>z"))

        # Section: Selection overlay
        overlay_group = Gtk.ShortcutsGroup(title="Region selection")
        overlay_group.append(Gtk.ShortcutsShortcut(title="Confirm selection", accelerator="space"))
        overlay_group.append(Gtk.ShortcutsShortcut(title="Cancel selection",  accelerator="Escape"))

        # Section: Output
        output_group = Gtk.ShortcutsGroup(title="Output")
        output_group.append(Gtk.ShortcutsShortcut(title="Copy to clipboard", accelerator="<Control>c"))
        output_group.append(Gtk.ShortcutsShortcut(title="Save to file",      accelerator="<Control>s"))
        output_group.append(Gtk.ShortcutsShortcut(title="Show this window",  accelerator="question F1"))

        section = Gtk.ShortcutsSection()
        section.append(capture_group)
        section.append(tools_group)
        section.append(history_group)
        section.append(overlay_group)
        section.append(output_group)

        window = Gtk.ShortcutsWindow()
        window.set_child(section)
        return window

    # ------------------------------------------------------------------
    # Copy to clipboard
    # ------------------------------------------------------------------

    def _on_copy_clicked(self, *_):
        surface = self._canvas.get_flat_surface()
        if surface is None:
            return
        # Write composited surface to a temp PNG then load as Pixbuf
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            surface.write_to_png(tmp)
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp)
        finally:
            os.unlink(tmp)

        clipboard = self.get_clipboard()
        clipboard.set(pixbuf)

    # ------------------------------------------------------------------
    # Save to disk
    # ------------------------------------------------------------------

    def _on_save_clicked(self, *_):
        dialog = Gtk.FileDialog()
        dialog.set_title("Save screenshot")
        dialog.set_initial_name("screenshot.png")
        png_filter = Gtk.FileFilter()
        png_filter.set_name("PNG images")
        png_filter.add_mime_type("image/png")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(png_filter)
        dialog.set_filters(filters)
        dialog.save(self, None, self._on_save_chosen)

    def _on_save_chosen(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except Exception:
            return  # user cancelled
        path = gfile.get_path()
        if not path:
            return
        if not path.endswith(".png"):
            path += ".png"
        surface = self._canvas.get_flat_surface()
        if surface is None:
            return
        try:
            surface.write_to_png(path)
        except Exception as exc:
            self._show_error(f"Could not save file:\n{exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _show_error(self, message: str):
        dialog = Adw.AlertDialog()
        dialog.set_heading("Capture failed")
        dialog.set_body(message)
        dialog.add_response("ok", "OK")
        dialog.present(self)
