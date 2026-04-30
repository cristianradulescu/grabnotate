"""Screenshot capture via org.freedesktop.portal.Screenshot (XDG Desktop Portal).

Strategy:
  1. Capture the full screen silently (no interactive dialog) via the portal.
  2. Return the path to the saved PNG so the caller can display a fullscreen
     overlay for region selection.

The portal uses an async request/response pattern:
  - Screenshot() returns a Request object path (handle).
  - The Response signal on that handle fires with response_code (0 = success)
    and a results dict containing 'uri' (the saved file URI).
"""

import threading
from urllib.parse import urlparse
from urllib.request import url2pathname

from gi.repository import Gio, GLib

_PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
_PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_IFACE = "org.freedesktop.portal.Screenshot"
_REQUEST_IFACE = "org.freedesktop.portal.Request"


def capture_fullscreen(callback):
    """Capture the full screen via the XDG portal and call callback(path, error).

    Shows a brief GNOME confirmation dialog before capturing.
    callback is called on the GLib main thread with (path: str | None, error: str | None).
    """
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    portal = Gio.DBusProxy.new_sync(
        bus,
        Gio.DBusProxyFlags.NONE,
        None,
        _PORTAL_BUS_NAME,
        _PORTAL_OBJECT_PATH,
        _PORTAL_IFACE,
        None,
    )

    result = portal.call_sync(
        "Screenshot",
        GLib.Variant("(sa{sv})", ("", {})),
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )

    (handle_path,) = result.unpack()

    ready = threading.Event()
    subscription_id = None

    def _on_response(connection, sender, object_path, iface_name, signal_name, parameters):
        ready.wait()
        response_code, results = parameters.unpack()
        bus.signal_unsubscribe(subscription_id)

        if response_code != 0:
            GLib.idle_add(callback, None, "Screenshot cancelled or denied")
            return

        uri = results.get("uri")
        if not uri:
            GLib.idle_add(callback, None, "Portal returned no URI")
            return

        path = url2pathname(urlparse(uri).path)
        GLib.idle_add(callback, path, None)

    subscription_id = bus.signal_subscribe(
        _PORTAL_BUS_NAME,
        _REQUEST_IFACE,
        "Response",
        handle_path,
        None,
        Gio.DBusSignalFlags.NONE,
        _on_response,
    )
    ready.set()
