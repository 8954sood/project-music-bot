from typing import Optional, List

import aiosqlite
from core.local import db_path
from core.local.music.model import MusicModel


class MusicDataSource:

    @staticmethod
    async def init_table():
        async with aiosqlite.connect(db_path) as db:
            # 테이블 존재 확인 및 생성
            await db.execute("""
                            CREATE TABLE IF NOT EXISTS tbl_music (
                                guild_id INTEGER PRIMARY KEY,
                                channel_id INTEGER,-- 추가 필드 정의
                                message_id INTEGER
                            )
                        """)
            await db.commit()

    @staticmethod
    async def get(guild_id: int) -> Optional[MusicModel]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"SELECT * FROM tbl_music WHERE guild_id = ?"
            tu = (guild_id,)
            cursor = await db.execute(query, tu)
            row = await cursor.fetchone()
            if row:
                return MusicModel(**row)
            else:
                return None

    @staticmethod
    async def update(guild_id: int, channel_id: int, message_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = f"UPDATE tbl_music SET channel_id = ?, message_id = ? WHERE guild_id = ?"
            tu = (channel_id, message_id, guild_id)
            await db.execute(query, tu)
            await db.commit()

    @staticmethod
    async def update_message_id(guild_id: int, message_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = f"UPDATE tbl_music SET message_id = ? WHERE guild_id = ?"
            tu = (message_id, guild_id)
            await db.execute(query, tu)
            await db.commit()

    @staticmethod
    async def insert(guild_id: int, channel_id: int, message_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = f"INSERT INTO tbl_music VALUES (?, ?, ?)"
            tu = (guild_id, channel_id, message_id)
            await db.execute(query, tu)
            await db.commit()

    @staticmethod
    async def get_all() -> List[MusicModel]:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            query = f"SELECT * FROM tbl_music"
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [MusicModel(**row) for row in rows] if rows else []

    @staticmethod
    async def delete(guild_id: int) -> None:
        async with aiosqlite.connect(db_path) as db:
            query = f"DELETE FROM tbl_music WHERE guild_id = ?"
            tu = (guild_id,)
            await db.execute(query, tu)
            await db.commit()
