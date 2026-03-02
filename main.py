"""Samsung Smart TV MCP Server — Control your TV with natural language."""

from __future__ import annotations

import logging
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from tv import SamsungTV, discover

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

mcp = FastMCP("samsung-tv")
_tv = SamsungTV()


def _ok(message: str = "Done", **data: Any) -> dict[str, Any]:
    return {"success": True, "message": message, **data}


def _err(message: str) -> dict[str, Any]:
    return {"success": False, "message": message}


def _safe(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logging.getLogger("samsung-tv").error("%s: %s", fn.__name__, e)
        return _err(str(e))


# ── Discovery & Info ─────────────────────────────────────────────


@mcp.tool()
def tv_discover() -> dict[str, Any]:
    """Scan the local network for Samsung Smart TVs via SSDP.

    Returns a list of found TVs with their IP, name, and model.
    """
    return _safe(lambda: _ok("Scan complete", tvs=discover()))


@mcp.tool()
def tv_info() -> dict[str, Any]:
    """Get TV status: model, IP, power state, current volume, resolution.

    Use this first to verify the TV is reachable and powered on.
    """
    return _safe(lambda: _ok("TV info retrieved", **_tv.info()))


# ── Power ────────────────────────────────────────────────────────


@mcp.tool()
def tv_power(action: str = "off") -> dict[str, Any]:
    """Turn the TV on or off.

    Args:
        action: "on" (Wake-on-LAN) or "off" (power key). Default "off".
    """
    def _do():
        if action.lower() == "on":
            _tv.power_on()
            return _ok("Wake-on-LAN packet sent. TV should turn on in a few seconds.")
        _tv.power_off()
        return _ok("TV power off command sent")
    return _safe(_do)


# ── Remote Keys ──────────────────────────────────────────────────


@mcp.tool()
def tv_key(key: str, times: int = 1) -> dict[str, Any]:
    """Send a remote control key press to the TV.

    Args:
        key: Key name like "VOLUP", "MUTE", "POWER", "HDMI", "PLAY", etc.
             The KEY_ prefix is added automatically if missing.
        times: Number of times to press the key (default 1).

    Common keys: POWER, VOLUP, VOLDOWN, MUTE, CHUP, CHDOWN, SOURCE, HDMI,
    UP, DOWN, LEFT, RIGHT, ENTER, RETURN, EXIT, HOME, MENU, GUIDE, INFO,
    PLAY, PAUSE, STOP, FF, REWIND, RED, GREEN, YELLOW, BLUE, 0-9,
    PMODE, DYNAMIC, STANDARD, MOVIE1, GAME, SLEEP, CAPTION, APP_LIST.
    """
    return _safe(lambda: (_tv.send_key(key, times), _ok(f"Sent {key} x{times}"))[1])


@mcp.tool()
def tv_keys(keys: str, delay: float = 0.3) -> dict[str, Any]:
    """Send a sequence of key presses with configurable delay between them.

    Args:
        keys: Comma-separated key names, e.g. "HOME, DOWN, DOWN, ENTER".
        delay: Seconds between each key press (default 0.3).

    Use this for menu navigation or complex sequences.
    """
    key_list = [k.strip() for k in keys.split(",") if k.strip()]
    return _safe(lambda: (_tv.send_keys(key_list, delay), _ok(f"Sent {len(key_list)} keys: {keys}"))[1])


@mcp.tool()
def tv_navigate(action: str) -> dict[str, Any]:
    """Quick navigation with semantic names instead of raw key codes.

    Args:
        action: One of: home, back, exit, menu, source, guide, info, tools,
                up, down, left, right, enter/ok, play, pause, stop, ff, rewind.
    """
    return _safe(lambda: (_tv.navigate(action), _ok(f"Navigated: {action}"))[1])


# ── Volume & Channel ─────────────────────────────────────────────


@mcp.tool()
def tv_volume(
    level: Optional[int] = None,
    action: Optional[str] = None,
) -> dict[str, Any]:
    """Control TV volume. Get current volume, set to exact level, or mute/unmute.

    Args:
        level: Set volume to this exact value (0-100). Omit to just read.
        action: "up", "down", "mute", "unmute". Omit to just read or use level.

    Examples: tv_volume() → get current, tv_volume(level=25) → set to 25,
    tv_volume(action="mute") → toggle mute.
    """
    def _do():
        if level is not None:
            _tv.set_volume(level)
            return _ok(f"Volume set to {level}", volume=level)
        if action:
            a = action.lower()
            if a == "up":
                _tv.send_key("KEY_VOLUP")
                return _ok("Volume up")
            if a == "down":
                _tv.send_key("KEY_VOLDOWN")
                return _ok("Volume down")
            if a == "mute":
                _tv.set_mute(True)
                return _ok("Muted")
            if a == "unmute":
                _tv.set_mute(False)
                return _ok("Unmuted")
            return _err(f"Unknown action '{a}'. Use: up, down, mute, unmute")
        vol = _tv.get_volume()
        muted = _tv.get_mute()
        return _ok(f"Volume: {vol}, Muted: {muted}", volume=vol, muted=muted)
    return _safe(_do)


@mcp.tool()
def tv_channel(
    number: Optional[int] = None,
    direction: Optional[str] = None,
) -> dict[str, Any]:
    """Change TV channel by number or direction.

    Args:
        number: Channel number to switch to (e.g. 42).
        direction: "up" or "down" to go to next/previous channel.

    Provide either number or direction, not both.
    """
    return _safe(lambda: (_tv.channel(number, direction), _ok(f"Channel changed"))[1])


# ── Apps ─────────────────────────────────────────────────────────


@mcp.tool()
def tv_apps() -> dict[str, Any]:
    """List all installed apps on the TV with their IDs.

    Returns app names and IDs that can be used with tv_launch.
    """
    return _safe(lambda: _ok("Apps retrieved", apps=_tv.list_apps()))


@mcp.tool()
def tv_launch(
    app: str,
    deep_link: Optional[str] = None,
) -> dict[str, Any]:
    """Launch an app on the TV by name or ID.

    Args:
        app: App name ("netflix", "youtube", "spotify", "disney", "prime",
             "browser", "plex", "hbo", "max") or app ID.
        deep_link: Optional deep link parameter to open specific content.

    The app name is case-insensitive and supports fuzzy matching.
    """
    return _safe(lambda: (_tv.launch_app(app, deep_link), _ok(f"Launched {app}"))[1])


@mcp.tool()
def tv_close_app(app: str) -> dict[str, Any]:
    """Close a running app on the TV.

    Args:
        app: App name or ID (same as tv_launch).
    """
    return _safe(lambda: (_tv.close_app(app), _ok(f"Closed {app}"))[1])


# ── Browser & Text ───────────────────────────────────────────────


@mcp.tool()
def tv_browser(url: str) -> dict[str, Any]:
    """Open a URL in the TV's built-in web browser.

    Args:
        url: The full URL to open (e.g. "https://google.com").
    """
    return _safe(lambda: (_tv.open_browser(url), _ok(f"Opened {url}"))[1])


@mcp.tool()
def tv_text(text: str) -> dict[str, Any]:
    """Type text into the currently active input field on the TV.

    Args:
        text: The text to type. Only works when a text input is active
              (virtual keyboard is visible on the TV screen).
    """
    return _safe(lambda: (_tv.send_text(text), _ok(f"Typed text ({len(text)} chars)"))[1])


# ── Cursor ───────────────────────────────────────────────────────


@mcp.tool()
def tv_cursor(x: int, y: int, duration: int = 500) -> dict[str, Any]:
    """Move the virtual cursor/pointer on the TV screen.

    Args:
        x: Horizontal position (pixels from left).
        y: Vertical position (pixels from top).
        duration: Movement duration in milliseconds (default 500).
    """
    return _safe(lambda: (_tv.move_cursor(x, y, duration), _ok(f"Cursor moved to ({x}, {y})"))[1])


# ── DLNA Media ───────────────────────────────────────────────────


@mcp.tool()
def tv_media(
    action: str = "status",
    url: Optional[str] = None,
    title: Optional[str] = None,
    seek_to: Optional[str] = None,
) -> dict[str, Any]:
    """Play media on the TV via DLNA or control current playback.

    Args:
        action: "play_url" to start playing from URL, or "play", "pause",
                "stop", "seek", "status" to control current media.
        url: Media URL (required for "play_url"). Supports video, audio, images.
        title: Display title for the media (optional, default "Media").
        seek_to: Time position for seek, format "HH:MM:SS" (e.g. "00:05:30").

    Examples:
        tv_media(action="play_url", url="http://server/video.mp4")
        tv_media(action="pause")
        tv_media(action="seek", seek_to="00:10:00")
        tv_media(action="status") → returns current position and state.
    """
    def _do():
        if action == "play_url":
            if not url:
                return _err("URL required for play_url action")
            _tv.play_media(url, title or "Media")
            return _ok(f"Playing: {url}")
        if action == "seek" and not seek_to:
            return _err("seek_to required for seek action (format HH:MM:SS)")
        result = _tv.media_control(action, target=seek_to)
        if action == "status":
            return _ok("Playback status", **result)
        return _ok(f"Media {action} executed", **result)
    return _safe(_do)


if __name__ == "__main__":
    mcp.run(transport="stdio")
