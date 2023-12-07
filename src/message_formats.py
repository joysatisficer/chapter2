import re
from dataclasses import dataclass
from functools import reduce
from typing import Callable, Annotated
from datetime import datetime

import pydantic

from declarations import Message, Author

_register: dict[str, "MessageFormat"] = {}


# todo: refactor message formats to support returning an index for where parsing stopped
# and to be a class instead
class MessageFormat(pydantic.BaseModel):
    """In the future, this could format all actions, not just messages, such as inner monologues"""

    render: Callable[[Message], str] | type(NotImplemented)
    name_prefix: Callable[[str], str] | type(NotImplemented)
    parse: Callable[[str], list[Message]] | type(NotImplemented)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            Callable: lambda v: str(v),
        }
        json_schema_extra = {
            "example": {
                "render": "<function>",
                "name_prefix": "<function>",
                "parse": "<function>",
            }
        }

    @pydantic.model_validator(mode="before")
    def read_from_register(cls, data: str | dict):
        if isinstance(data, str):
            return _register[data]
        else:
            return data


def parse_message_format(message_format: str) -> "MessageFormat":
    return MessageFormat.parse_obj(message_format)


irc_message_format = _register["irc"] = MessageFormat(
    render=lambda message: (
        reduce(
            lambda acc, line: acc + "\n" + line if acc != "" else line,
            [
                f"<{message.author.name}> {line}"
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
        Message(Author(name) if name != "" else None, content)
        for name, content in re.findall(
            r"^(?:<([^\n]+)> )?([^<].*)$", continuation, re.MULTILINE
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
        Message(Author(name) if name != "" else None, content)
        for name, content in re.findall(
            r"^(?:([^\n]+):)? ([^:].*)$", continuation, re.MULTILINE
        )
    ],
)

web_document_format = _register["web_document"] = MessageFormat(
    render=lambda message: ("from " + message.author.name)
    + (
        ""
        if message.timestamp is None
        else (datetime.utcfromtimestamp(message.timestamp).strftime(" @ %Y-%m-%d"))
    )
    + "\n"
    + message.content,
    name_prefix=NotImplemented,
    parse=NotImplemented,
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

faux_chat_message_format = _register["faux_chat"] = MessageFormat(
    render=lambda message: "[{role}](#{type})\n{content}".format(
        role=message.author.name,
        type="instructions" if message.author.name == "system" else "message",
        content=message.content,
    ),
    name_prefix=lambda name: "[{role}](#{type})\n".format(
        role=name,
        type="instructions" if name == "system" else "message",
    ),
    # (?:\[(\w+)\]\(#(\w+)\)\n(.*)\s*)+
    parse=lambda continuation: re.match(
        "(?:\[(\w+)\]\(#(\w+)\)\n(.*))+", continuation
    ).groups(),
)

MESSAGE_FORMAT_REGISTRY = _register
