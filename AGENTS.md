# AGENTS.md

## Project

Grabnotate — Wayland-only screenshot and annotation tool for GNOME. Python + GTK4 + libadwaita + Cairo.

## Setup

Run the installer — it handles everything (system packages, uv, Python env, launchers, optional GNOME keybindings):

```bash
./install.sh
```

For manual setup (e.g. CI or non-apt systems):

```bash
sudo apt install -y libcairo2-dev libgirepository-2.0-dev libgtk-4-dev \
  gir1.2-gtk-4.0 python3-gi python3-gi-cairo python3-dev python3.14-dev
uv pip install -e .
```

## Running

```bash
uv run grabnotate          # open main window
uv run grabnotate --select # skip window, go straight to region capture
```

The entry point is `screenshot_tool/main.py:main` — not the root `main.py` (which is a stale placeholder and can be ignored).

## Architecture

- `screenshot_tool/app.py` — `Adw.Application` + `Adw.ApplicationWindow`; owns the full capture flow and header bar actions; handles `--select` CLI flag via `HANDLES_COMMAND_LINE` + `_on_command_line`; auto-resizes window to fit the cropped image on each capture
- `screenshot_tool/screenshot.py` — D-Bus capture via `org.freedesktop.portal.Screenshot` (XDG portal, async request/response pattern)
- `screenshot_tool/overlay.py` — fullscreen `Gtk.Window` for region selection; seeds a default selection (25% margin) on first resize; calls back with image-pixel coordinates
- `screenshot_tool/editor.py` — `AnnotationCanvas` (`Gtk.DrawingArea`); maintains a separate Cairo annotation surface over the photo; tools: rectangle, line, arrow, highlight, blur; stroke width configurable via `set_stroke_width()`
- `screenshot_tool/toolbox.py` — `AnnotationToolbox` (`Gtk.Box`); drives the canvas, registers all tool/undo/shortcut actions on the window at realize time
- `screenshot_tool/main.py` — calls `app.run()`
- Root `main.py` is unused scaffolding left from `uv init`
- `install.sh` — full installer: checks/installs apt deps, installs uv if missing, runs `uv pip install -e .`, writes launchers to `~/.local/bin`, optionally registers GNOME keybindings

## Key decisions

- **Wayland only** — no X11 support planned
- `org.gnome.Shell.Screenshot` is **blocked** by GNOME 47+ security policy for third-party callers — `SelectArea` and `Screenshot` both return `AccessDenied`. Use the XDG portal instead.
- Capture strategy: full-screen capture via `org.freedesktop.portal.Screenshot` → fullscreen overlay for region selection → crop with `GdkPixbuf` → annotate
- **`--select` mode**: `ScreenshotToolApp` uses `HANDLES_COMMAND_LINE`; `_on_command_line` sets a flag and calls `activate()`; in `_on_activate` the window is never shown and `start_capture()` is called directly. Works on both fresh launches and already-running instances.
- **Window auto-resize**: after each crop, `_resize_to_image(w, h)` calls `set_default_size()` capped to the monitor work area (queried via `display.get_monitor_at_surface()`).
- **Default selection**: `overlay.py` seeds a 25%-margin rectangle in `_on_drawing_area_resize` (fires once the fullscreen surface is actually sized, not at realize time).
- **Arrow / hjkl keys in overlay**: no modifier = move whole selection (1 px); Shift = move bottom-right corner (10 px); Ctrl = move top-left corner (1 px). All moves clamp to overlay bounds and re-normalise the rect.
- Region selection confirm key is **Space** (not Enter)
- Annotations are stored on a separate `cairo.ImageSurface` (never mutates the photo surface); undo/redo uses raw byte snapshots of that surface
- Blur tool: pure-Python box blur (`_box_blur_argb`) with repeated horizontal + vertical passes; composites committed annotations into the region before blurring
- `Gtk.ShortcutsWindow` uses `set_child(section)` — it has no `append()` method
- `Gtk.ColorDialogButton` is used for the color picker (renders the swatch natively, no custom drawing needed)
- Keyboard shortcuts are registered as `Gio.SimpleAction`s on the window inside `AnnotationToolbox._on_realize` — not at construction time, because `get_root()` is only valid after the widget is realized
- `AnnotationCanvas.on_history_changed` is a plain callable (not a GObject signal) — set it in `app.py` after constructing both canvas and toolbox
- GApplication ID is `app.grabnotate` — must contain at least one dot to pass `g_application_id_is_valid()`
- Launcher uses absolute path to uv (`~/.local/bin/uv`) so GNOME keybinding daemon (minimal PATH) can find it

## Keyboard shortcuts (all window-scoped)

| Key | Action |
|---|---|
| `Ctrl+Shift+S` | Capture |
| `Ctrl+C` | Copy to clipboard |
| `Ctrl+S` | Save to file |
| `R` / `L` / `A` / `H` / `B` | Rect / Line / Arrow / Highlight / Blur tool |
| `C` | Open color picker |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / Redo |
| `Space` | Confirm region selection |
| `Escape` | Cancel region selection |
| `Arrows` / `hjkl` | Move selection (overlay) |
| `Shift+Arrows` / `Shift+hjkl` | Resize selection — bottom-right corner |
| `Ctrl+Arrows` / `Ctrl+hjkl` | Resize selection — top-left corner |
| `?` / `F1` | Keyboard shortcuts window |
