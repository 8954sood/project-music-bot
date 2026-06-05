# project-music

**English** | [한국어](README.ko.md)

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
Fetches public nodes from one or both sources (a REST list API and/or a local JSON file), checks
which can play the configured source(s), and plays through them with ordered sticky failover (a
working node stays selected until it fails, then the next node is tried).

Node list sources — a source is used **when its value is provided** (non-empty). Neither has a
default, so set at least one. If both are set they are merged and de-duplicated by host:port; if
both are empty the pool is empty.
- `LAVALINK_NODE_LIST_URL=` — node list API URL (empty by default = off). Recommended value:
  `https://lavalink-list.ajieblogs.eu.org/All` (`/SSL`, `/NonSSL` also work).
- `LAVALINK_NODE_LIST_FILE=` — path to a local JSON node list (empty by default = off; set a path to
  use it). Same schema as the list API — an array of
  `{"identifier","host","port","password","secure","version"}`. Example:
  ```json
  [{"identifier":"lava-v4.ajieblogs.eu.org","host":"lava-v4.ajieblogs.eu.org","port":443,
    "password":"https://dsc.gg/ajidevserver","secure":true,"version":"v4"}]
  ```
  This file is **git-ignored** (it's a local, curated list scraped from public-node pages such as
  heavencloud.in). Copy the committed template `lavalink_nodes.example.json` to `lavalink_nodes.json`
  and fill in real nodes. File nodes go through the **same** `secure-only` / v4 / probe filters as URL
  nodes, so non-SSL entries are dropped unless `LAVALINK_NODE_SECURE_ONLY=false`.
- `LAVALINK_NODE_PROBE_TIMEOUT=8` — total time budget (seconds) for the whole node check
  (list fetch + source probe + node connect, run in parallel). Lower = faster startup but drops slow nodes.
- `LAVALINK_NODE_PROBE_QUERY=lofi hip hop` — YouTube probe search term
- `LAVALINK_NODE_SECURE_ONLY=true` — only use secure (wss) nodes. Recommended on, since non-secure
  nodes transmit the Discord voice token in plaintext. (The bot token is never sent to nodes.)
- `LAVALINK_NODE_MAX_FAILOVER=3` — max nodes to try per play/search request before giving up
  (prevents hammering every node). A new request gets a fresh budget. `0` = unlimited.
- `LAVALINK_NODE_REFRESH_HOURS=24` — auto re-discover the public node pool on this interval
  (public nodes die/recover over time). A refresh destroys all players, so any cycle where a guild is
  connected to voice ("in use") is skipped and retried next interval. `0` disables (manual `-nodes` only).
- `LAVALINK_NODE_SOURCE=youtube` — which sources to allow (see below): `youtube` / `spotify` / `both`
- `LAVALINK_NODE_SPOTIFY_PROBE_URL=https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8` — the
  Spotify track URL used to probe whether a node has the LavaSrc plugin (only used when the source
  includes Spotify).

Only nodes that pass the source probe(s) for the active mode **and** pomice's `/version` check are
used; flaky nodes are skipped automatically and re-evaluated on restart / `-nodes`.

#### Source modes (`LAVALINK_NODE_SOURCE`)
Spotify on public nodes works **server-side via the node's LavaSrc plugin** — the bot sends the
Spotify URL to the node's `loadtracks` and LavaSrc resolves/mirrors it (no Spotify API credentials
needed on the bot). Not every public node has LavaSrc, so the bot probes each node for Spotify
support and only keeps capable ones. **Spotify only plays from an explicit Spotify URL** (track /
album / playlist / artist link); plain-text searches never go to Spotify.

- `youtube` (default) — YouTube only. Pasting a Spotify URL replies
  "스포티파이는 현재 모드에서 제공할 수 없습니다." and auto-deletes. (Unchanged from before.)
- `spotify` — Spotify URLs only. A YouTube link replies "유튜브는 현재 모드에서 제공할 수 없습니다.",
  and a plain-text search replies "현재 모드에서는 Spotify 링크만 재생할 수 있어요." Both auto-delete.
- `both` — Spotify URLs play, and YouTube links / text searches behave as usual. Note that `both`
  keeps only nodes that support *both* sources, so the healthy pool can be smaller.

## Commands
- `-reload [cog]` (owner) — reload cogs
- `-nodes` (owner) — re-discover the public node pool (only meaningful with `AUDIO_BACKEND=lavalink-node`)

## Local testing (no Discord)
- `python test/lavalink_node_test.py` — discover/probe public nodes, run sticky failover, and play a
  track locally via yt-dlp + ffplay (a stand-in for Discord voice; Lavalink can't output to local
  speakers). Run by path; `python -m test.lavalink_node_test` won't work (the root `test.py` shadows
  the `test` package).
- `python test/lavalink_node_spotify_test.py` — probe each public node for YouTube and Spotify
  (LavaSrc) support over pure REST (no Discord/pomice), print a per-node table and the predicted
  healthy-pool size for each `LAVALINK_NODE_SOURCE` mode. Optionally paste a Spotify URL to confirm a
  node actually resolves it. Use this to check whether `spotify`/`both` modes are viable on the
  current public node list before switching the bot over. Run by path (same `test.py` shadowing caveat).

## Notes
- Keep command UX unchanged; all playback logic is routed through `core/audio` and `AudioService`.
- Node discovery + sticky failover lives in `core/audio/lavalink_node_pool.py` (no Discord/pomice deps);
  the `lavalink-node` backend (`core/audio/lavalink_node_backend.py`) drives pomice players over the pool.
