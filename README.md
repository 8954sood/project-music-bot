# project-music

## Setup
- Create `.env` (see `.env.example`) and set `BOT_TOKEN`.
- (Recommended) Create a venv: `python3 -m venv .venv && source .venv/bin/activate`
- Install dependencies: `pip install -r requirements.txt`
- Run the bot: `python app.py`

## Environment
Required:
- `BOT_TOKEN`

macOS only (Opus voice library):
- `OPUS_PATH` — path to libopus. With Homebrew: `OPUS_PATH=/opt/homebrew/lib/libopus.dylib`
  (Apple Silicon) or `/usr/local/lib/libopus.dylib` (Intel). Install via `brew install opus`.

Audio backend selection (`AUDIO_BACKEND`):
- `ffmpeg` (default) — FFmpeg + yt-dlp (needs `cookies.txt` for YouTube)
- `lavalink` — a single self-hosted Lavalink server (Pomice)
- `hybrid` — Lavalink for search + FFmpeg/yt-dlp for playback
- `lavalink-node` — public Lavalink node pool (no server to host), with ordered sticky failover

### `lavalink` / `hybrid` (self-hosted server) settings
- `LAVALINK_HOST=127.0.0.1`
- `LAVALINK_PORT=2333`
- `LAVALINK_PASSWORD=youshallnotpass`
- `LAVALINK_IDENTIFIER=main`

### `lavalink-node` (public node pool) settings
Fetches public nodes from the DarrenOfficial/lavalink-list REST API, checks which can play
YouTube, and plays through them with ordered sticky failover (a working node stays selected
until it fails, then the next node is tried).
- `LAVALINK_LIST_URL=https://lavalink-list.ajieblogs.eu.org/All` — node list API (`/SSL`, `/NonSSL` also work)
- `LAVALINK_NODE_PROBE_TIMEOUT=8` — total time budget (seconds) for the whole node check
  (list fetch + YouTube probe + node connect, run in parallel). Lower = faster startup but drops slow nodes.
- `LAVALINK_NODE_PROBE_QUERY=lofi hip hop` — probe search term
- `LAVALINK_NODE_SECURE_ONLY=true` — only use secure (wss) nodes. Recommended on, since non-secure
  nodes transmit the Discord voice token in plaintext. (The bot token is never sent to nodes.)
- `LAVALINK_NODE_MAX_FAILOVER=3` — max nodes to try per play/search request before giving up
  (prevents hammering every node). A new request gets a fresh budget. `0` = unlimited.

Only nodes that pass both the YouTube probe and pomice's `/version` check are used; flaky nodes are
skipped automatically and re-evaluated on restart / `-nodes`.

## Commands
- `-reload [cog]` (owner) — reload cogs
- `-nodes` (owner) — re-discover the public node pool (only meaningful with `AUDIO_BACKEND=lavalink-node`)

## Local testing (no Discord)
- `python test/lavalink_node_test.py` — discover/probe public nodes, run sticky failover, and play a
  track locally via yt-dlp + ffplay (a stand-in for Discord voice; Lavalink can't output to local
  speakers). Run by path; `python -m test.lavalink_node_test` won't work (the root `test.py` shadows
  the `test` package).

## Notes
- Keep command UX unchanged; all playback logic is routed through `core/audio` and `AudioService`.
- Node discovery + sticky failover lives in `core/audio/lavalink_node_pool.py` (no Discord/pomice deps);
  the `lavalink-node` backend (`core/audio/lavalink_node_backend.py`) drives pomice players over the pool.
