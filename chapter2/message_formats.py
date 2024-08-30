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

    @staticmethod
    def render(message: Message) -> str:
        pass

    @staticmethod
    def name_prefix(name: str) -> str:  # TODO: Refactor to Author
        pass

    @staticmethod
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
        # todo: map `* user is walking` to `Message(user`
        return [
            Message(Author(name) if name != "" else None, content)
            for name, content in re.findall(
                r"^(?:<([^\n]+)> ?)?([^<].*)$", continuation, re.MULTILINE
            )
        ]


class ColonMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["colon"] = "colon"
    suffix: str = "\n"

    def render(self, message):
        return (
            reduce(
                lambda acc, line: acc + self.suffix + line if acc != "" else line,
                [
                    (
                        f"{message.author.name}: {line}"
                        if message.author.name is not None
                        else line
                    )
                    for line in message.content.splitlines()
                    if not line.strip() == ""
                ],
                "",  # initial value
            )
            if message.author is not None
            else message.content
        ) + self.suffix

    @staticmethod
    def name_prefix(name):
        return f"{name}:"

    @staticmethod
    def parse(continuation):
        messages = []
        for line in continuation.splitlines():
            match = re.match(r"^(?:(?:([^\n]+):)? ?)?([^:].*)$", line)
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


# wait... this needs to layer with other message formats
# InstructMessageFormat
class LynnMessageFormat(AbstractMessageFormat, pydantic.BaseModel):
    name: Literal["lynn"] = "lynn"
    # subformat # TODO: Validate, unimplemented, always colon atm
    assistant_name: str = "lynn"
    role_prefixes: dict[str, str] = {
        "assistant": "<|start_header_id|>assistant<|end_header_id|>\n\n",
        "user": "<|start_header_id|>user<|end_header_id|>\n\n",
    }
    role_suffixes: dict[str, str] = {
        "assistant": "<|eot_id|>",
        "user": "<|eot_id|>",
    }
    name_prefix_role: str = "assistant"

    @staticmethod
    def render(message: Message) -> str:
        if message.author.name == self.assistant_name:
            role = "assistant"
        else:
            role = "user"
        return self.role_prefixes[role] + message.content + self.role_suffixes[role]

    @staticmethod
    def name_prefix(name: str) -> str:
        return self.role_prefixes[
            self.name_prefix_role
        ] + ColonMessageFormat.name_prefix(name)

    @staticmethod
    def parse(continuation: str) -> list[Message]:
        print(continuation)
        messages = []
        parts = continuation.split("<|start_header_id|>")

        for part in parts:
            try:
                header, content = part.split("<|end_header_id|>\n\n", 1)
                role = header.strip()
                if "<|eot_id|>" in content:
                    content, _ = content.rsplit("<|eot_id|>", 1)
                content = content.strip()

                author = Author(name="lynn" if role == "assistant" else "user")
                message = Message(author=author, content=content)
                messages.append(message)
            except ValueError:
                # If splitting fails, it might be an incomplete message at the end
                # We can choose to ignore it or handle it differently
                pass

        return messages


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
    | LynnMessageFormat
)
