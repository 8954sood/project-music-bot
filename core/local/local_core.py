from core.local.music import MusicDataSource


class LocalCore:

    @staticmethod
    async def init_table():
        await MusicDataSource.init_table()