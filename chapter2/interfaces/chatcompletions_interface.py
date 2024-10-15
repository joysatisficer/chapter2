import asyncio
import time
import math
from typing import Callable, Awaitable, Optional, AsyncIterable, Any, Union, Literal

from pydantic import BaseModel

from declarations import GenerateResponse, Message, UserID, Author
from abstractinterface import AbstractInterface
from ontology import Config, ChatCompletionsInterfaceConfig
from util import asyncutil
from util.asyncutil import async_generator_to_reusable_async_iterable

Role = Literal["system", "user", "assistant"]


class ChatCompletionsRequestMessage(BaseModel):
    content: str
    role: Role
    name: Optional[str] = None


class ChatCompletionsRequest(BaseModel):
    messages: list[ChatCompletionsRequestMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[Union[str, list[str]]] = None
    logit_bias: Optional[dict] = None
    # ignored or unimplemented parameters
    model: str
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
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
        base_config: Config,
        generate_response: GenerateResponse,
        em_name: str,
        interface_config: ChatCompletionsInterfaceConfig,
    ):
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware

        self.base_config = base_config
        self.generate_response: GenerateResponse = generate_response
        self.em_name = em_name
        self.iface_config = interface_config
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
            config = self.base_config.copy()
            if chat_completions_request.max_tokens is not None:
                config.em.continuation_max_tokens = chat_completions_request.max_tokens
            if chat_completions_request.temperature is not None:
                config.em.temperature = chat_completions_request.temperature
            if chat_completions_request.top_p is not None:
                config.em.top_p = chat_completions_request.top_p
            if chat_completions_request.stop is not None:
                if isinstance(chat_completions_request.stop, list):
                    config.em.stop_sequences.extend(chat_completions_request.stop)
                else:
                    config.em.stop_sequences.append(chat_completions_request.stop)
            if chat_completions_request.logit_bias is not None:
                config.em.logit_bias |= chat_completions_request.logit_bias

            my_user_id = UserID("em::" + self.em_name, "chatcompletions")

            # todo: error handling

            async def messages_iterator():
                for chat_completion_message in chat_completions_request.messages[::-1]:
                    if chat_completion_message.role == "assistant":
                        author = Author(self.em_name, my_user_id)
                        message_type = None
                    elif chat_completion_message.role == "user":
                        name = (
                            chat_completion_message.name
                            if chat_completion_message.name is not None
                            else self.iface_config.default_name
                        )
                        author = Author(
                            name, UserID(str(hash(name)), "chatcompletions")
                        )
                        message_type = None
                    elif chat_completion_message.role == "system":
                        name = (
                            chat_completion_message.name
                            if chat_completion_message.name is not None
                            else "system"
                        )
                        author = Author(
                            name, UserID(str(hash(name)), "chatcompletions")
                        )
                        message_type = "instructions"
                    else:
                        continue
                    yield Message(
                        author=author,
                        content=chat_completion_message.content,
                        type=message_type,
                    )

            valid_messages = []
            async for reply_message in generate_response(
                my_user_id,
                async_generator_to_reusable_async_iterable(messages_iterator),
                config.em,
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
                model=em_name,
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

    async def start(self):
        import uvicorn
        from util.uvicorn_improved import RapidShutdownUvicornServer

        # TODO: Option for listening on an HTTP port (port 0 = random port)
        if self.iface_config.port is None:
            socket_loc = str(self.base_config.em.folder / "socket")
            uv_config = uvicorn.Config(
                self.app,
                log_level="info",
                uds=socket_loc,
            )
            print(f"Listening on {socket_loc}")
        else:
            uv_config = uvicorn.Config(
                self.app, log_level="info", port=self.iface_config.port
            )
            print(f"Listening on {self.iface_config.port}")
        self.uv_server = RapidShutdownUvicornServer(uv_config)
        self.uv_server.install_signal_handlers = lambda: None
        if self.iface_config.end_to_end_test:
            self.uv_server.on_ready = lambda: asyncutil.run_task(self.end_to_end_test())
        self.task_serve = asyncio.create_task(self.uv_server.serve())
        return await self.task_serve

    def stop(self, *args):
        self.task_serve.cancel()

    async def end_to_end_test(self):
        import openai

        try:
            await openai.ChatCompletion.acreate(
                model="foo",
                api_base="http://0.0.0.0:6005/v1",
                messages=[{"role": "user", "content": "Hello"}],
            )
        except RuntimeError:
            self.end_to_end_test_fail = True
        self.task_serve.cancel()


def isempty(string):
    return string == "" or string.isspace()
