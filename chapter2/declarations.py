from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union, AsyncIterable, TYPE_CHECKING, Awaitable

if TYPE_CHECKING:
    from ontology import Config, FacultyConfig


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
    id: str | None = None
    reply_to: str | None = None


Action = Union[Message]
ActionHistory = AsyncIterable[Action]
Ensemble = AsyncIterable[Action | AsyncIterable["Ensemble"]]
JSON = dict[str, Union[str, int, float, bool, list, dict]]
GenerateResponse = Callable[[UserID, ActionHistory, "EmConfig"], AsyncIterable[Action]]
Faculty = Callable[[ActionHistory, "FacultyConfig", "Config"], AsyncIterable[Message]]
