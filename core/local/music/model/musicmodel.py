from dataclasses import dataclass


@dataclass
class MusicModel:
    guild_id: int
    channel_id: int
    message_id: int