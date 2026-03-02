"""Samsung Smart TV client — WebSocket + UPnP + SSDP + WoL."""

from __future__ import annotations

import json
import logging
import socket
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError

WS_TIMEOUT = 8  # seconds — max wait for any WebSocket operation

from samsungtvws import SamsungTVWS

log = logging.getLogger("samsung-tv")

TOKEN_FILE = str(Path(__file__).parent / "token.json")
_UPNP_NS = "urn:schemas-upnp-org:service"
_SOAP_ENV = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
    ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body>{body}</s:Body></s:Envelope>"
)

# App aliases → official IDs (region-independent first, then fallbacks)
APP_ALIASES: dict[str, list[str]] = {
    "netflix": ["11101200001", "3201907018807"],
    "youtube": ["111299001912"],
    "prime": ["3201512006785", "3201910019365"],
    "disney": ["3201901017640"],
    "spotify": ["3201606009684"],
    "apple tv": ["3201807016597"],
    "hbo": ["3201601007230", "3202301029760"],
    "max": ["3202301029760", "3201601007230"],
    "plex": ["3201512006963"],
    "browser": ["org.tizen.browser", "3201907018784"],
    "steam link": ["3201702011851"],
    "twitch": ["3202203026841"],
    "tiktok": ["3202008021577"],
    "tubi": ["3201504001965"],
    "pluto": ["3201808016802"],
    "paramount": ["3201710014981"],
    "gallery": ["3201710015037"],
    "smartthings": ["3201710015016"],
}

NAVIGATE_KEYS = {
    "home": "KEY_HOME",
    "back": "KEY_RETURN",
    "exit": "KEY_EXIT",
    "menu": "KEY_MENU",
    "source": "KEY_SOURCE",
    "guide": "KEY_GUIDE",
    "info": "KEY_INFO",
    "tools": "KEY_TOOLS",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "enter": "KEY_ENTER",
    "ok": "KEY_ENTER",
    "play": "KEY_PLAY",
    "pause": "KEY_PAUSE",
    "stop": "KEY_STOP",
    "ff": "KEY_FF",
    "rewind": "KEY_REWIND",
}


# ── SSDP Discovery ──────────────────────────────────────────────


def discover(timeout: float = 5.0) -> list[dict[str, Any]]:
    """Discover Samsung TVs on the local network via SSDP."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {int(timeout)}\r\n"
        "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n"
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.sendto(msg.encode(), ("239.255.255.250", 1900))

    locations: set[str] = set()
    tvs: list[dict[str, Any]] = []
    try:
        while True:
            data, _ = sock.recvfrom(4096)
            text = data.decode(errors="ignore")
            if "samsung" not in text.lower():
                continue
            for line in text.splitlines():
                if line.upper().startswith("LOCATION:"):
                    locations.add(line.split(":", 1)[1].strip())
    except socket.timeout:
        pass
    finally:
        sock.close()

    for loc in locations:
        try:
            resp = urlopen(loc, timeout=3).read().decode()
            root = ET.fromstring(resp)
            ns = {"d": "urn:schemas-upnp-org:device-1-0"}
            dev = root.find(".//d:device", ns)
            if dev is None:
                continue
            fn = dev.findtext("d:friendlyName", "", ns)
            mn = dev.findtext("d:modelName", "", ns)
            mfr = dev.findtext("d:manufacturer", "", ns)
            if "samsung" not in (mfr or "").lower():
                continue
            ip = urlparse(loc).hostname
            tvs.append({
                "ip": ip,
                "name": fn,
                "model": mn,
                "manufacturer": mfr,
                "location": loc,
            })
        except Exception:
            continue
    return tvs


# ── UPnP SOAP helpers ───────────────────────────────────────────


def _soap_call(
    ip: str, control_url: str, service: str, action: str, args: str = ""
) -> str:
    """Execute a UPnP SOAP action and return raw XML response."""
    body = f'<u:{action} xmlns:u="{_UPNP_NS}:{service}:1">{args}</u:{action}>'
    envelope = _SOAP_ENV.format(body=body)
    url = f"http://{ip}:9197{control_url}"
    req = Request(
        url,
        data=envelope.encode(),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{_UPNP_NS}:{service}:1#{action}"',
        },
    )
    return urlopen(req, timeout=5).read().decode()


def _soap_value(xml_text: str, tag: str) -> str | None:
    """Extract a single value from SOAP response XML."""
    root = ET.fromstring(xml_text)
    for elem in root.iter():
        if elem.tag.endswith(tag):
            return elem.text
    return None


# ── Wake-on-LAN ─────────────────────────────────────────────────


def wake_on_lan(mac: str) -> None:
    """Send WoL magic packet to power on the TV."""
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, ("255.255.255.255", 9))


# ── Main TV Client ──────────────────────────────────────────────


class SamsungTV:
    """Unified Samsung TV controller combining WebSocket, REST, and UPnP."""

    def __init__(self, ip: str | None = None):
        self._ip = ip
        self._ws: SamsungTVWS | None = None
        self._info: dict[str, Any] | None = None
        self._mac: str | None = None

    # ── Connection ───────────────────────────────────────────

    def _ensure_ip(self) -> str:
        if self._ip:
            return self._ip
        tvs = discover(timeout=4.0)
        if not tvs:
            raise ConnectionError("No Samsung TV found on the network")
        self._ip = tvs[0]["ip"]
        log.info("Auto-discovered TV at %s (%s)", self._ip, tvs[0].get("name"))
        return self._ip

    def _ensure_ws(self) -> SamsungTVWS:
        if self._ws is not None:
            return self._ws
        ip = self._ensure_ip()
        try:
            self._ws = SamsungTVWS(
                host=ip, port=8002, token_file=TOKEN_FILE,
                timeout=WS_TIMEOUT, name="ClaudeCode",
            )
            self._ws.open()
            log.info("Connected via WSS:8002")
        except Exception:
            log.warning("WSS:8002 failed, trying WS:8001")
            self._ws = SamsungTVWS(
                host=ip, port=8001, token_file=TOKEN_FILE,
                timeout=WS_TIMEOUT, name="ClaudeCode",
            )
            self._ws.open()
        return self._ws

    def _reconnect(self) -> SamsungTVWS:
        self._ws = None
        return self._ensure_ws()

    def _send_ws(self, method: str, timeout: float = WS_TIMEOUT, **kwargs: Any) -> Any:
        """Send WebSocket command with auto-reconnect and timeout."""
        ws = self._ensure_ws()
        for attempt in range(2):
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(getattr(ws, method), **kwargs)
                    return future.result(timeout=timeout)
            except FuturesTimeout:
                log.error("WS %s timed out after %ss", method, timeout)
                self._ws = None
                raise TimeoutError(
                    f"TV did not respond to '{method}' within {timeout}s. "
                    "TV may be unresponsive or in a state that doesn't support this command."
                )
            except Exception as e:
                if attempt == 0:
                    log.warning("WS error (%s), reconnecting...", e)
                    ws = self._reconnect()
                else:
                    raise

    # ── Device Info ──────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        ip = self._ensure_ip()
        try:
            raw = urlopen(f"http://{ip}:8001/api/v2/", timeout=5).read()
            data = json.loads(raw)
            device = data.get("device", {})
            self._mac = device.get("wifiMac")
            # Enrich with UPnP volume
            try:
                vol = self.get_volume()
                device["currentVolume"] = vol
            except Exception:
                pass
            self._info = device
            return {
                "name": device.get("name", "Unknown"),
                "model": device.get("modelName", "Unknown"),
                "ip": device.get("ip", ip),
                "mac": self._mac,
                "power": device.get("PowerState", "unknown"),
                "os": device.get("OS", "Tizen"),
                "resolution": device.get("resolution", "unknown"),
                "network": device.get("networkType", "unknown"),
                "volume": device.get("currentVolume"),
            }
        except (URLError, OSError):
            return {"power": "off", "ip": ip, "message": "TV appears to be off"}

    # ── Power ────────────────────────────────────────────────

    def power_off(self) -> None:
        self._send_ws("send_key", key="KEY_POWER")

    def power_on(self, mac: str | None = None) -> None:
        target_mac = mac or self._mac
        if not target_mac:
            # Try to get MAC from previous info
            try:
                self.info()
                target_mac = self._mac
            except Exception:
                pass
        if not target_mac:
            raise ValueError("MAC address required for Wake-on-LAN. Get it with tv_info first.")
        wake_on_lan(target_mac)

    # ── Keys ─────────────────────────────────────────────────

    def send_key(self, key: str, times: int = 1) -> None:
        normalized = key.upper()
        if not normalized.startswith("KEY_"):
            normalized = f"KEY_{normalized}"
        for i in range(times):
            self._send_ws("send_key", key=normalized)
            if i < times - 1:
                time.sleep(0.15)

    def send_keys(self, keys: list[str], delay: float = 0.3) -> None:
        for i, key in enumerate(keys):
            self.send_key(key)
            if i < len(keys) - 1:
                time.sleep(delay)

    def navigate(self, action: str) -> None:
        key = NAVIGATE_KEYS.get(action.lower())
        if not key:
            raise ValueError(
                f"Unknown action '{action}'. Valid: {', '.join(NAVIGATE_KEYS)}"
            )
        self.send_key(key)

    # ── Volume ───────────────────────────────────────────────

    def get_volume(self) -> int:
        ip = self._ensure_ip()
        xml = _soap_call(
            ip, "/upnp/control/RenderingControl1", "RenderingControl",
            "GetVolume", "<InstanceID>0</InstanceID><Channel>Master</Channel>",
        )
        val = _soap_value(xml, "CurrentVolume")
        return int(val) if val else -1

    def set_volume(self, level: int) -> None:
        ip = self._ensure_ip()
        level = max(0, min(100, level))
        _soap_call(
            ip, "/upnp/control/RenderingControl1", "RenderingControl",
            "SetVolume",
            f"<InstanceID>0</InstanceID><Channel>Master</Channel>"
            f"<DesiredVolume>{level}</DesiredVolume>",
        )

    def get_mute(self) -> bool:
        ip = self._ensure_ip()
        xml = _soap_call(
            ip, "/upnp/control/RenderingControl1", "RenderingControl",
            "GetMute", "<InstanceID>0</InstanceID><Channel>Master</Channel>",
        )
        val = _soap_value(xml, "CurrentMute")
        return val == "1" if val else False

    def set_mute(self, mute: bool) -> None:
        ip = self._ensure_ip()
        _soap_call(
            ip, "/upnp/control/RenderingControl1", "RenderingControl",
            "SetMute",
            f"<InstanceID>0</InstanceID><Channel>Master</Channel>"
            f"<DesiredMute>{'1' if mute else '0'}</DesiredMute>",
        )

    # ── Channel ──────────────────────────────────────────────

    def channel(self, number: int | None = None, direction: str | None = None) -> None:
        if number is not None:
            for digit in str(number):
                self.send_key(f"KEY_{digit}")
                time.sleep(0.15)
            time.sleep(0.3)
            self.send_key("KEY_ENTER")
        elif direction:
            key = "KEY_CHUP" if direction.lower() == "up" else "KEY_CHDOWN"
            self.send_key(key)
        else:
            raise ValueError("Provide either number or direction ('up'/'down')")

    # ── Apps ─────────────────────────────────────────────────

    def _resolve_app_id(self, name_or_id: str) -> str:
        alias = name_or_id.lower().strip()
        if alias in APP_ALIASES:
            return APP_ALIASES[alias][0]
        # Check if it looks like an app ID already
        if name_or_id.replace(".", "").replace("_", "").isalnum() and (
            len(name_or_id) > 8 or "." in name_or_id
        ):
            return name_or_id
        # Fuzzy match against aliases
        for key, ids in APP_ALIASES.items():
            if alias in key or key in alias:
                return ids[0]
        return name_or_id

    def list_apps(self) -> list[dict[str, Any]]:
        import signal

        def _timeout_handler(signum, frame):
            raise TimeoutError("app_list timeout")

        from samsungtvws.remote import ChannelEmitCommand
        from samsungtvws.helper import process_api_response
        from samsungtvws.event import ED_INSTALLED_APP_EVENT, parse_installed_app

        ws = self._ensure_ws()
        assert ws.connection

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(WS_TIMEOUT)
        try:
            ws._ws_send(ChannelEmitCommand.get_installed_app())
            response = process_api_response(ws.connection.recv())
            signal.alarm(0)
            if response.get("event") == ED_INSTALLED_APP_EVENT:
                raw = parse_installed_app(response)
                return [{"id": a.get("appId"), "name": a.get("name", "Unknown")} for a in (raw or [])]
            return self._known_apps_fallback()
        except (TimeoutError, Exception) as e:
            signal.alarm(0)
            self._ws = None
            log.warning("list_apps from TV failed (%s), using known aliases", e)
            return self._known_apps_fallback()
        finally:
            signal.signal(signal.SIGALRM, old_handler)

    @staticmethod
    def _known_apps_fallback() -> list[dict[str, Any]]:
        return [{"id": ids[0], "name": name.title()} for name, ids in APP_ALIASES.items()]

    def launch_app(
        self, name_or_id: str, meta_tag: str | None = None
    ) -> None:
        app_id = self._resolve_app_id(name_or_id)
        ip = self._ensure_ip()
        # Try REST first (more reliable for launching)
        try:
            req = Request(f"http://{ip}:8001/api/v2/applications/{app_id}", method="POST")
            urlopen(req, timeout=5)
            return
        except Exception:
            pass
        # Fallback to WebSocket
        self._send_ws(
            "run_app", app_id=app_id, app_type=2, meta_tag=meta_tag or ""
        )

    def close_app(self, name_or_id: str) -> None:
        app_id = self._resolve_app_id(name_or_id)
        ip = self._ensure_ip()
        req = Request(
            f"http://{ip}:8001/api/v2/applications/{app_id}", method="DELETE"
        )
        urlopen(req, timeout=5)

    # ── Browser ──────────────────────────────────────────────

    def open_browser(self, url: str) -> None:
        self._send_ws("open_browser", url=url)

    # ── Text Input ───────────────────────────────────────────

    def send_text(self, text: str) -> None:
        self._send_ws("send_text", text=text)

    # ── Cursor ───────────────────────────────────────────────

    def move_cursor(self, x: int, y: int, duration: int = 500) -> None:
        self._send_ws("move_cursor", x=x, y=y, duration=duration)

    # ── DLNA Media ───────────────────────────────────────────

    def play_media(self, url: str, title: str = "Media") -> None:
        ip = self._ensure_ip()
        meta = (
            f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"'
            f' xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<item><dc:title>{title}</dc:title></item></DIDL-Lite>"
        )
        _soap_call(
            ip, "/upnp/control/AVTransport1", "AVTransport",
            "SetAVTransportURI",
            f"<InstanceID>0</InstanceID>"
            f"<CurrentURI>{url}</CurrentURI>"
            f"<CurrentURIMetaData>{meta}</CurrentURIMetaData>",
        )
        time.sleep(0.5)
        _soap_call(
            ip, "/upnp/control/AVTransport1", "AVTransport",
            "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>",
        )

    def media_control(self, action: str, target: str | None = None) -> dict[str, Any]:
        ip = self._ensure_ip()
        action_lower = action.lower()

        if action_lower == "play":
            _soap_call(
                ip, "/upnp/control/AVTransport1", "AVTransport",
                "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>",
            )
        elif action_lower == "pause":
            _soap_call(
                ip, "/upnp/control/AVTransport1", "AVTransport",
                "Pause", "<InstanceID>0</InstanceID>",
            )
        elif action_lower == "stop":
            _soap_call(
                ip, "/upnp/control/AVTransport1", "AVTransport",
                "Stop", "<InstanceID>0</InstanceID>",
            )
        elif action_lower == "seek" and target:
            _soap_call(
                ip, "/upnp/control/AVTransport1", "AVTransport",
                "Seek",
                f"<InstanceID>0</InstanceID>"
                f"<Unit>REL_TIME</Unit><Target>{target}</Target>",
            )
        elif action_lower == "status":
            xml_t = _soap_call(
                ip, "/upnp/control/AVTransport1", "AVTransport",
                "GetTransportInfo", "<InstanceID>0</InstanceID>",
            )
            xml_p = _soap_call(
                ip, "/upnp/control/AVTransport1", "AVTransport",
                "GetPositionInfo", "<InstanceID>0</InstanceID>",
            )
            return {
                "state": _soap_value(xml_t, "CurrentTransportState"),
                "position": _soap_value(xml_p, "RelTime"),
                "duration": _soap_value(xml_p, "TrackDuration"),
                "uri": _soap_value(xml_p, "TrackURI"),
            }
        else:
            raise ValueError(
                f"Unknown action '{action}'. Valid: play, pause, stop, seek, status"
            )
        return {"action": action_lower, "done": True}

    def close(self) -> None:
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
