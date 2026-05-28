from dataclasses import dataclass
from datetime import datetime


@dataclass
class Session:
    id: int
    name: str
    start_time: datetime
    end_time: datetime
    status: str


@dataclass
class Transcript:
    id: int
    session_id: int
    timestamp: datetime
    text: str
    source: str


@dataclass
class Screenshot:
    id: int
    session_id: int
    timestamp: datetime
    filepath: str
