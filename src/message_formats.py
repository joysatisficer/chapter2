import re
from dataclasses import dataclass
from functools import reduce
from typing import Callable, Optional, List, Tuple


@dataclass
class MessageFormat:
    """In the future, this could format all actions, not just messages, such as inner monologues"""

    wrap: Callable[[str, str], str]
    name_prefix: Callable[[str], str]
    parse: Callable[[str], List[Tuple[str, str]]]


irc_message_format = MessageFormat(
    wrap=lambda name, message: reduce(
        lambda acc, line: acc + "\n" + line,
        [f"<{name}> {message}" for line in message.splitlines() if not line.isspace()],
        "",  # initial value
    ),
    name_prefix=lambda name: f"<{name}>",
    # match `<name> string`, `string` but not `<name`, which usually occurs
    # because of a length cutoff
    parse=lambda line: re.findall(r"^(?:<([^\n]+)>)?([^<].*)$", line, re.MULTILINE),
)
