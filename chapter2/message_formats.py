import hashlib
import re
from abc import abstractmethod
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

    def render(self, message: Message) -> str:
        pass

    def name_prefix(self, name: str) -> str:  # TODO: Refactor to Author
        pass

    def parse(self, continuation: str) -> list[Message]:
        pass


class IRCMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["irc"] = "irc"
    include_id: bool = True

    def render(self, message):
        if message.author is None:
            return message.content + "\n"
        result = ""
        for line in message.content.splitlines():
            if not line.isspace():
                if result:
                    result += "\n"
                result += f"<{message.author.name}>"
                if self.include_id and message.reply_to is not None:
                    result += f" [reply:{message.reply_to[:5]}] "
                result += f" {line}"
                if self.include_id and message.id is not None:
                    result += " [id:" + message.id[:5] + "]"
        return result + "\n"

    @staticmethod
    def name_prefix(name):
        return f"<{name}>"

    def parse(self, continuation):
        result = []
        pattern = r"^(?:<([^\n]+)> ?)?([^<].*?)\s?(?:\[id:[0-9a-f]+])?$"
        for match in re.finditer(pattern, continuation, re.MULTILINE):
            name, content = match.groups()
            if name != "":
                author = Author(name)
            else:
                author = None
            message = Message(author, content)
            result.append(message)
        # TODO: map `* user is walking` to `Message(user`
        return result


class ColonMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["colon"] = "colon"
    suffix: str = "\n"

    def render(self, message):
        return (
            (message.author.name if message.author.name is not None else "")
            + (
                reduce(
                    lambda acc, line: acc + self.suffix + line if acc != "" else line,
                    [
                        line
                        for line in message.content.splitlines()
                        if not line.strip() == ""
                    ],
                    "",  # initial value
                )
                if message.author is not None
                else message.content
            )
            + self.suffix
        )

    @staticmethod
    def name_prefix(name):
        return f"{name}:"

    @staticmethod
    def parse(continuation):
        messages = []
        for line in continuation.splitlines():
            match = re.match(r"^(?:(?:([^\n]+?):)? ?)?([^:].*)$", line)
            if match is not None:
                groups = match.groups()
                name, raw_content = groups
                if name is None or name.strip() == "":
                    author = None
                elif re.match(r"^\s*\d*\.", line) or re.match(r"^-|•", line):
                    author = None
                    raw_content = name + ": " + raw_content
                else:
                    author = Author(name.strip())
                content = raw_content.strip()
                messages.append(Message(author, content))

        return messages


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
            + "\n"
        )


class ChatMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["chat"] = "chat"
    assistant_name: str | None
    name_start: str
    name_end: str
    role_start: str
    role_end: str
    turn_end: str

    def render(self, message: Message) -> str:
        if message.author is not None and message.author.name != "":
            name = message.author.name
        else:
            name = ""
        return self.name_prefix(name) + message.content + self.turn_end

    def name_prefix(self, name: str) -> str:
        cleaned_name = "".join(
            [c if re.match("[a-zA-Z0-9_-]", c) else "-" for c in name]
        )
        role = "user"
        part_change_name = ""
        if cleaned_name != "":
            if self.assistant_name == cleaned_name:
                role = "assistant"
            part_change_name = self.name_start + cleaned_name + self.name_end
        part_role = self.role_start + role + self.role_end
        return part_role + part_change_name

    def parse(self, continuation: str) -> list[Message]:
        # todo: fix for open-source models
        a = continuation.removeprefix(self.name_prefix(self.assistant_name))
        return [
            Message(author=Author(self.assistant_name), content=line)
            for line in a.splitlines()
        ]


def hashint(integer: int) -> str:
    m = hashlib.sha256(usedforsecurity=False)
    m.update(integer.to_bytes(length=64))
    return m.hexdigest()


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
    type: str = "message"

    def render(self, message: Message) -> str:
        if message.type is not None:
            message_type = message.type
        elif message.author.name == "system":
            message_type = "instructions"
        else:
            message_type = self.type
        if "\n\n" in message.content:
            content = message.content
        else:
            content = re.sub(r"(?<!\n)\n(?!\n)", "\n\n", message.content)
        if message.author.name in ("", None):
            return content.rstrip()
        else:
            return "[{role}](#{type})\n{content}\n".format(
                role=message.author.name,
                type=message_type,
                content=content.rstrip(),
            )

    def name_prefix(self, name: str) -> str:
        return "[{role}](#{type})".format(
            role=name,
            type="instructions" if name == "system" else self.type,
        )

    @staticmethod
    def parse(continuation: str) -> list[Message]:
        messages = []
        cur_message_content = ""
        name = None
        first_message = True
        message_type = None
        for line in continuation.splitlines(keepends=True):
            # allow usernames to be URLs
            if match := re.match(r"^\[([\w:/.-]+)]\(#([\w-]*)\)", line):
                if not first_message:
                    messages.append(
                        Message(
                            Author(name),
                            cur_message_content.rstrip(),
                            type=message_type,
                        )
                    )
                first_message = False
                name, message_type = match.groups()
                if name == "system":
                    if message_type == "instructions":
                        message_type = None
                elif message_type == "message":
                    message_type = None
                cur_message_content = ""
            elif line.strip() == "":
                if not cur_message_content.endswith("\n"):
                    cur_message_content += "\n"
            elif line.strip() != "":
                cur_message_content += line

        if cur_message_content != "" or name is not None:
            messages.append(
                Message(Author(name), cur_message_content.rstrip(), type=message_type)
            )
        return messages


MessageFormat = (
    IRCMessageFormat
    | ColonMessageFormat
    | WebDocumentMessageFormat
    | PythonREPLMessageFormat
    | InfrastructMessageFormat
    | ChatMessageFormat
)
