import time
import math
from typing import Callable, Awaitable, Optional, AsyncIterable, Any, Union, Literal

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from declarations import GenerateResponse, Message, UserID, Author
from abstractinterface import AbstractInterface, GetDiscordConfig

Role = Literal["system", "user", "assistant"]


class ChatCompletionsRequestMessage(BaseModel):
    content: str
    role: Role
    name: Optional[str] = None


class ChatCompletionsRequest(BaseModel):
    messages: list[ChatCompletionsRequestMessage]
    max_tokens: Optional[int] = None
    # ignored or unimplemented parameters
    model: str
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[Union[str, list[str]]] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    logit_bias: Optional[dict] = None
    function_call: Optional[Union[Literal["none", "auto"], dict]] = None
    functions: Optional[list[Any]] = None
    n: Optional[int] = 1
    user: Optional[str] = None


class ChatCompletionsResponseMessage(BaseModel):
    content: str
    role: Role


class ChatCompletionsChoice(BaseModel):
    index: int
    message: ChatCompletionsResponseMessage
    finish_reason: str


class OpenAIUsage(BaseModel):
    completion_tokens: int
    prompt_tokens: int
    total_tokens: int


class ChatCompletionsResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionsChoice]
    usage: OpenAIUsage


class ChatCompletionsInterface(AbstractInterface):
    def __init__(
        self,
        get_discord_config: GetDiscordConfig,
        generate_response: GenerateResponse,
        agent_name: str,
    ):
        self.get_config: GetDiscordConfig = get_discord_config
        self.generate_response: GenerateResponse = generate_response
        self.agent_name = agent_name
        self.app = FastAPI()
        origins = ["*"]
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @self.app.post("/v1/chat/completions")
        async def chat_completions(chat_completions_request: ChatCompletionsRequest):
            config = await self.get_config(None)
            config.continuation_model = "gpt-4-base"  # TODO: XXX
            my_user_id = UserID(-hash("ch2-" + self.agent_name), "chatcompletions")

            # todo: error handling

            async def messages_iterator():
                for chat_completion_message in chat_completions_request.messages[::-1]:
                    if chat_completion_message.role == "assistant":
                        author = Author(self.agent_name, my_user_id)
                    elif chat_completion_message.role == "user":
                        name = (
                            chat_completion_message.name
                            if chat_completion_message.name is not None
                            else config.chatcompletions_default_name
                        )
                        author = Author(name, UserID(-hash(name), "chatcompletions"))
                    else:
                        continue
                    yield Message(
                        author=author, content=chat_completion_message.content
                    )

            valid_messages = []
            async for reply_message in generate_response(
                my_user_id, messages_iterator(), config
            ):
                if reply_message.author.user_id == my_user_id and not isempty(
                    reply_message.content
                ):
                    valid_messages.append(reply_message)
                else:
                    break
            if len(valid_messages) == 0:
                raise ValueError()
            message = Message(
                author=valid_messages[0].author,
                content="\n".join([message.content for message in valid_messages]),
            )

            return ChatCompletionsResponse(
                id="chatcmpl-ch2",  # todo
                created=math.floor(time.time()),
                model=agent_name,
                choices=[
                    ChatCompletionsChoice(
                        index=0,
                        message=ChatCompletionsResponseMessage(
                            role="assistant", content=message.content
                        ),
                        finish_reason="stop",  # todo: unimplemented
                    )
                ],
                # todo: unimplemented
                usage=OpenAIUsage(
                    completion_tokens=0,
                    prompt_tokens=0,
                    total_tokens=0,
                ),
            )

    def start(self):
        # TODO: read port from config, read config from env, read unix socket from env
        uv_config = uvicorn.Config(
            self.app, port=6005, log_level="info", host="0.0.0.0"
        )
        self.uv_server = uvicorn.Server(uv_config)
        self.uv_server.install_signal_handlers = lambda: None
        return self.uv_server.serve()

    def stop(self, sig, frame):
        return self.uv_server.handle_exit(sig, frame)


def isempty(string):
    return string == "" or string.isspace()
