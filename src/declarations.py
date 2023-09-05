from dataclasses import dataclass
from typing import Callable, Union, AsyncIterator, AsyncIterable, TYPE_CHECKING, TypeVar
from abc import ABC
from resolve_config import Config

if TYPE_CHECKING:
    from message_formats import MessageFormat


@dataclass
class UserID:
    id: int
    platform: str


@dataclass
class Author:
    name: str
    user_id: UserID | None = None


@dataclass
class Message:
    author: Author
    content: str
    timestamp: float = 0  # sent messages use timestamp to represent time delay


Action = Union[Message]
MessageHistory = AsyncIterable[Message]
JSON = dict[str, Union[str, int, float, bool, list, dict]]
GenerateResponse = Callable[[UserID, MessageHistory, Config], AsyncIterable[Action]]
Faculty = Callable[
    [MessageHistory, Config], list[Message]
]  # AsyncIterable[Message] in the future
