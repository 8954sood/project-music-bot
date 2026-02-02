import asyncio
import os
import urllib.parse

import aiohttp
from dotenv import load_dotenv


async def main():
    load_dotenv()
    host = os.getenv("LAVALINK_HOST", "127.0.0.1")
    port = os.getenv("LAVALINK_PORT", "2333")
    password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

    query = input("Query: ").strip()
    if not query:
        print("Empty query.")
        return

    identifier = query if query.startswith("http") else f"ytsearch:{query}"
    encoded = urllib.parse.quote(identifier, safe="")
    url = f"http://{host}:{port}/v4/loadtracks?identifier={encoded}"
    
    headers = {
        "Authorization": "youshallnotpass",
        "User-Agent": "lavalink-search-test",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            print(f"HTTP {resp.status}")
            data = await resp.json()
            load_type = data.get("loadType")
            tracks = data.get("tracks") or []
            print(f"loadType={load_type} tracks={len(tracks)}")
            if tracks:
                info = tracks[0].get("info", {})
                print(f"first.title={info.get('title')}")
                print(f"first.uri={info.get('uri')}")


if __name__ == "__main__":
    asyncio.run(main())
