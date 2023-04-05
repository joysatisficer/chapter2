from dataclasses import dataclass
from typing import Callable, List, Union, AsyncIterable, Awaitable


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
    timestamp: float  # sent messages use timestamp to represent time delay


Action = Union[Message]
MessageHistory = AsyncIterable[Message]
GenerateResponse = Callable[[UserID, MessageHistory], AsyncIterable[Action]]
