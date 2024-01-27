import re
from abc import abstractmethod
from dataclasses import dataclass
from functools import reduce
from typing import Callable, Annotated, Literal
from datetime import datetime

import pydantic

from declarations import Message, Author


# todo: refactor message formats to support returning an index for where parsing stopped
# and to be a class instead
# https://github.com/pydantic/pydantic/issues/1932
class AbstractMessageFormat:
    """In the future, this could format all actions, not just messages, such as inner
    monologues"""

    name: str

    @staticmethod
    @abstractmethod
    def render(message: Message) -> str:
        pass

    @staticmethod
    @abstractmethod
    def name_prefix(name: str) -> str:  # TODO: Refactor to Author
        pass

    @staticmethod
    @abstractmethod
    def parse(continuation: str) -> list[Message]:
        pass


class IRCMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["irc"] = "irc"

    @staticmethod
    def render(message):
        return (
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
        ) + "\n"

    @staticmethod
    def name_prefix(name):
        return f"<{name}>"

    @staticmethod
    def parse(continuation):
        return [
            Message(Author(name) if name != "" else None, content)
            for name, content in re.findall(
                r"^(?:<([^\n]+)> )?([^<].*)$", continuation, re.MULTILINE
            )
        ]


class ColonMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["colon"] = "colon"

    @staticmethod
    def render(message):
        return (
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
        ) + "\n"

    @staticmethod
    def name_prefix(name):
        return f"{name}:"

    @staticmethod
    def parse(continuation):
        return [
            Message(Author(name) if name != "" else None, content)
            for name, content in re.findall(
                r"^(?:([^\n]+):)? ([^:].*)$", continuation, re.MULTILINE
            )
        ]


class WebDocumentMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["web_document"] = "web_document"

    @staticmethod
    def render(message):
        return (
            "from "
            + message.author.name
            + (
                ""
                if message.timestamp is None
                else (
                    datetime.utcfromtimestamp(message.timestamp).strftime(" @ %Y-%m-%d")
                )
            )
            + "\n"
            + message.content
        )


# contrib
LiteralMessageFormat = Literal["irc"] | Literal["colon"] | Literal["web_document"]


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


class PythonREPLMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["python_repl"] = "python_repl"

    @staticmethod
    def render(message: Message):
        return (
            message.content
            if message.author.name == "interpreter" or message.author is None
            else "\n".join(
                [
                    f">>> {line}" if i == 0 else f"... {line}"
                    for i, line in enumerate(message.content.splitlines())
                ]
            )
            + ("\n..." if len(message.content.splitlines()) > 1 else "")
        ) + "\n"

    @staticmethod
    def name_prefix(name: str):
        return "" if name == "interpreter" else ">>>"

    @staticmethod
    def parse(continuation: str):
        return [
            Message(Author(name), content)
            for name, content in parse_repl_log(continuation)
        ]


class InfrastructMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["infrastruct"] = "infrastruct"

    @staticmethod
    def render(message: Message) -> str:
        return "[{role}](#{type})\n{content}".format(
            role=message.author.name,
            type="instructions" if message.author.name == "system" else "message",
            content=message.content,
        )

    @staticmethod
    def name_prefix(name: str) -> str:
        return "[{role}](#{type})\n".format(
            role=name,
            type="instructions" if name == "system" else "message",
        )

    @staticmethod
    def parse(continuation: str) -> str:
        re.match("(?:\[(\w+)\]\(#(\w+)\)\n(.*))+", continuation).groups()


MessageFormat = (
    IRCMessageFormat
    | ColonMessageFormat
    | WebDocumentMessageFormat
    | PythonREPLMessageFormat
    | InfrastructMessageFormat
)
