from __future__ import annotations

import pydantic.dataclasses
from typing import Callable, Union, AsyncIterable, TYPE_CHECKING, Awaitable

if TYPE_CHECKING:
    from ontology import Config, FacultyConfig


@pydantic.dataclasses.dataclass(frozen=True)
class Author:
    name: str | None


@pydantic.dataclasses.dataclass(frozen=True)
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
GenerateResponse = Callable[["EmConfig", ActionHistory], AsyncIterable[Action]]
Faculty = Callable[["EmConfig", "FacultyConfig", ActionHistory], AsyncIterable[Message]]
