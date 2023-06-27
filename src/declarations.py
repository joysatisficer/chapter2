from dataclasses import dataclass
from typing import Callable, List, Union, AsyncIterable, Awaitable
from resolve_config import Config


@dataclass
class UserID:
    id: int
    platform: str


@dataclass
class Author:
    user_id: UserID
    name: str


@dataclass
class Message:
    author: Author
    message: str
    timestamp: float = 0  # sent messages use timestamp to represent time delay


Action = Union[Message]
MessageHistory = AsyncIterable[Message]
JSON = dict[str, Union[str, int, float, bool, list, dict]]
GenerateResponse = Callable[[UserID, MessageHistory, Config], AsyncIterable[Action]]
