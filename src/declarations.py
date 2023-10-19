from dataclasses import dataclass
from typing import Callable, Union, AsyncIterable, TYPE_CHECKING

if TYPE_CHECKING:
    from resolve_config import Config, FacultyConfig


@dataclass(frozen=True)
class UserID:
    id: int
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


Action = Union[Message]
MessageHistory = AsyncIterable[Message]
JSON = dict[str, Union[str, int, float, bool, list, dict]]
GenerateResponse = Callable[[UserID, MessageHistory, "Config"], AsyncIterable[Action]]
Faculty = Callable[[MessageHistory, "FacultyConfig", "Config"], AsyncIterable[Message]]


# class AbstractInterface(ABC):
#     @abstractmethod
#     def __init__(
#         self,
#         agent_name: str,
#         generate_response: GenerateResponse,
#         get_discord_config: GetDiscordConfig,
#     ):
#         pass
