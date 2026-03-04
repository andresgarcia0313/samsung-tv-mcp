# Samsung TV MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that lets AI assistants control Samsung Smart TVs (Tizen OS, 2016+) over the local network. No cloud, no SmartThings account required.

> **Early release** — Core functionality works and has been tested on a Samsung UN65TU7000 (2020, Tizen). Not all tools have been exhaustively tested across different TV models and firmware versions. Bug reports and PRs are welcome.

## Features

- **18 tools** for complete TV control via natural language
- **Auto-discovery** — finds Samsung TVs on your network via SSDP
- **Zero config** — no API keys, no cloud, everything runs locally
- **Multi-protocol** — combines WebSocket, REST, UPnP/DLNA, and Wake-on-LAN
- **Persistent auth** — first connection requires TV approval, then uses saved token
- **Auto-reconnect** — recovers from dropped WebSocket connections

## Tools

| Tool | Description |
|------|-------------|
| `tv_discover` | Scan LAN for Samsung Smart TVs |
| `tv_info` | Get TV status (model, IP, power, volume) |
| `tv_power` | Turn TV on (WoL) or off |
| `tv_key` | Send any remote control key press |
| `tv_keys` | Send a sequence of key presses |
| `tv_navigate` | Semantic navigation (home, back, source, menu...) |
| `tv_volume` | Get/set volume (0-100), mute/unmute |
| `tv_channel` | Change channel by number or up/down |
| `tv_apps` | List all installed apps |
| `tv_launch` | Launch app by name ("netflix") or ID |
| `tv_close_app` | Close a running app |
| `tv_current_app` | Detect which app is currently running |
| `tv_aspect_ratio` | Get/set aspect ratio (Default, 16:9, Zoom, 4:3) |
| `tv_captions` | Get caption state or toggle subtitles |
| `tv_browser` | Open a URL in the TV's browser |
| `tv_text` | Type text into active input fields |
| `tv_cursor` | Move virtual cursor on screen |
| `tv_media` | Play media via DLNA (video/audio/images) with transport controls |

## Requirements

- Python 3.10+
- Samsung Smart TV with Tizen OS (2016 or newer)
- TV and MCP server on the same local network

## Installation

```bash
pip install "samsungtvws[encrypted]" "mcp[cli]"
```

### Claude Code

```bash
claude mcp add samsung-tv -- python /path/to/samsung-tv-mcp/main.py
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "samsung-tv": {
      "command": "python",
      "args": ["/path/to/samsung-tv-mcp/main.py"]
    }
  }
}
```

## First Connection

On the first WebSocket connection, the TV will show a popup asking to allow "ClaudeCode". Accept it once — a token is saved to `token.json` for future sessions.

To minimize prompts, go to **Settings > General > External Device Manager > Device Connection Manager** and set **Access Notification** to "First Time Only".

## Supported App Aliases

You can use friendly names instead of numeric IDs:

`netflix`, `youtube`, `prime`, `disney`, `spotify`, `apple tv`, `hbo`, `max`, `plex`, `browser`, `steam link`, `twitch`, `tiktok`, `tubi`, `pluto`, `paramount`, `gallery`, `smartthings`

## Architecture

```
main.py  →  FastMCP tools (thin layer, ~120 lines)
tv.py    →  SamsungTV client (WebSocket + UPnP + SSDP + WoL, ~250 lines)
```

- **WebSocket** (port 8002/8001): remote keys, apps, browser, text input, cursor
- **REST** (port 8001): device info, app launch/close
- **UPnP SOAP** (port 9197): precise volume control, DLNA media playback
- **Wake-on-LAN**: power on from standby

## Known Limitations

- Wake-on-LAN may not work reliably on all models (requires TV config)
- No direct HDMI source switching via API (must navigate the source menu with keys)
- No brightness/contrast/picture settings control
- No screenshot capability
- Text input only works when the TV's virtual keyboard is active
- DRM-protected content cannot be streamed via DLNA

## Tested On

- Samsung UN65TU7000KXZL (2020, Crystal UHD, Tizen, Colombia)

## License

MIT
