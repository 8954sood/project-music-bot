# project-music

## Setup
- Create `.env` (see `.env.example`) and set `BOT_TOKEN`.
- Install dependencies: `pip install -r requirements.txt`
- Run the bot: `python app.py`

## Environment
Required:
- `BOT_TOKEN`

Audio backend selection:
- `AUDIO_BACKEND=ffmpeg` (default) uses FFmpeg + yt-dlp
- `AUDIO_BACKEND=lavalink` uses Lavalink (Pomice)

Lavalink (local server) settings:
- `LAVALINK_HOST=127.0.0.1`
- `LAVALINK_PORT=2333`
- `LAVALINK_PASSWORD=youshallnotpass`
- `LAVALINK_IDENTIFIER=main`

## Notes
- Keep command UX unchanged; all playback logic is routed through `core/audio` and `AudioService`.
