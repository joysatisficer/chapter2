from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Union, AsyncIterable, TYPE_CHECKING, Awaitable

if TYPE_CHECKING:
    from resolve_config import Config, FacultyConfig


@dataclass(frozen=True)
class UserID:
    id: str
    platform: str


@dataclass(frozen=True)
class Author:
    name: str
    user_id: UserID | None = None


@dataclass(frozen=True)
class Message:
    author: Author | None
    content: str
    timestamp: float = 0  # sent messages use timestamp to represent time delay
    type: str | None = None


Action = Union[Message]
ActionHistory = AsyncIterable[Action]
JSON = dict[str, Union[str, int, float, bool, list, dict]]
GenerateResponse = Callable[[UserID, ActionHistory, "Config"], AsyncIterable[Action]]
Faculty = Callable[[ActionHistory, "FacultyConfig", "Config"], AsyncIterable[Message]]
