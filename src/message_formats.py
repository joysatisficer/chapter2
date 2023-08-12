import re
from dataclasses import dataclass
from functools import reduce
from typing import Callable

from declarations import Message, Author


@dataclass
class MessageFormat:
    """In the future, this could format all actions, not just messages, such as inner monologues"""

    render: Callable[[Message], str]
    name_prefix: Callable[[str], str]
    parse: Callable[[str], list[Message]]


_register: dict[str, MessageFormat] = {}

irc_message_format = _register["irc"] = MessageFormat(
    render=lambda message: (
        reduce(
            lambda acc, line: acc + "\n" + line if acc != "" else line,
            [
                f"<{message.author.name}> {message.content}"
                for line in message.content.splitlines()
                if not line.isspace()
            ],
            "",  # initial value
        )
        if message.author is not None
        else message.content
    )
    + "\n",
    name_prefix=lambda name: f"<{name}>",
    # match `<name> string`, `string` but not `<name`, which usually occurs
    # because of a length cutoff
    parse=lambda continuation: [
        Message(Author(name), content)
        for name, content in re.findall(
            r"^(?:<([^\n]+)>)? ([^<].*)$", continuation, re.MULTILINE
        )
    ],
)

colon_message_format = _register["colon"] = MessageFormat(
    render=lambda message: (
        reduce(
            lambda acc, line: acc + "\n" + line if acc != "" else line,
            [
                f"{message.author.name}: {line}"
                for line in message.content.splitlines()
                if not line.isspace()
            ],
            "",  # initial value
        )
        if message.author is not None
        else message.content
    )
    + "\n",
    name_prefix=lambda name: f"{name}:",
    # match `name: string`, `string` but not `name`, which usually occurs
    # because of a length cutoff
    parse=lambda continuation: [
        Message(Author(name), content)
        for name, content in re.findall(
            r"^(?:([^\n]+):)? ([^:].*)$", continuation, re.MULTILINE
        )
    ],
)

# contrib


def parse_repl_log(log_text):
    sections = []
    current_user_section = []
    current_interpreter_section = []

    lines = log_text.split("\n")
    for line in lines:
        if line.startswith(">>> ") or line.startswith("... "):
            if current_interpreter_section:
                interpreter_text = "\n".join(current_interpreter_section)
                if (
                    interpreter_text.strip()
                ):  # Check if the interpreter section is not empty
                    sections.append(("interpreter", interpreter_text))
                current_interpreter_section = []
            current_user_section.append(line[4:])
        else:
            if current_user_section:
                sections.append(("user", "\n".join(current_user_section)))
                current_user_section = []
            current_interpreter_section.append(line)

    # Append any remaining sections
    if current_user_section:
        sections.append(("user", "\n".join(current_user_section)))
    if current_interpreter_section:
        interpreter_text = "\n".join(current_interpreter_section)
        if interpreter_text.strip():  # Check if the interpreter section is not empty
            sections.append(("interpreter", interpreter_text))

    return sections


python_repl_message_format = _register["python_repl"] = MessageFormat(
    render=lambda message: (
        message.content
        if message.author.name == "interpreter" or message.author is None
        else "\n".join(
            [
                f">>> {line}" if i == 0 else f"... {line}"
                for i, line in enumerate(message.content.splitlines())
            ]
        )
        + ("\n..." if len(message.content.splitlines()) > 1 else "")
    )
    + "\n",
    name_prefix=lambda name: "" if name == "interpreter" else ">>>",
    parse=lambda continuation: [
        Message(Author(name), content) for name, content in parse_repl_log(continuation)
    ],
)

MESSAGE_FORMAT_REGISTRY = _register
