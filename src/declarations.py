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
    user_id: UserID
    name: str


@dataclass
class Message:
    author: Author
    content: str
    timestamp: float = 0  # sent messages use timestamp to represent time delay


Action = Union[Message]
MessageHistory = AsyncIterator[Message]
JSON = dict[str, Union[str, int, float, bool, list, dict]]
GenerateResponse = Callable[[UserID, MessageHistory, Config], AsyncIterable[Action]]


class Faculty(ABC):
    def __init__(self, config: Config):
        pass

    async def fetch(self) -> list[Message]:
        pass
