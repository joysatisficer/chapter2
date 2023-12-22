import asyncio
import time
from typing import Optional, Literal

import uvicorn
import fastapi
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from declarations import GenerateResponse, Message, UserID, Author
from resolve_config import Config, CompletionsInterfaceConfig
from message_formats import MessageFormat, MESSAGE_FORMAT_REGISTRY
from abstractinterface import AbstractInterface, GetDiscordConfig
from util.asyncit import eager_iterable_to_async_iterable
from util.uvicorn_improved import RapidShutdownUvicornServer


class CompletionRequest(BaseModel):
    prompt: str  # array not implemented
    n: Optional[int] = 1
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    model: Optional[str] = None  # ignored
    stop: str | list | None = None  # not implemented
    frequency_penalty: Optional[float] = None  # not implemented
    presence_penalty: Optional[float] = None  # not implemented
    logit_bias: Optional[dict] = None  # not implemented
    logprobs: Optional[int] = None  # not implemented
    best_of: int = 1  # not implemented
    echo: bool = False  # not implemented
    seed: Optional[int] = None  # not implemented
    stream: Literal[False] | None = None
    user: Optional[str] = None  # ignored


class CompletionResponse(BaseModel):
    id: str
    object: str = "text_completion"
    created: int
    model: Optional[str]
    choices: list
    usage: dict


class CompletionsInterface(AbstractInterface):
    def __init__(
        self,
        get_discord_config: GetDiscordConfig,
        generate_response: GenerateResponse,
        em_name: str,
        interface_config: CompletionsInterfaceConfig,
    ):
        self.get_config: GetDiscordConfig = get_discord_config
        self.generate_response: GenerateResponse = generate_response
        self.em_name = em_name
        self.interface_config = interface_config
        self.app = FastAPI()
        origins = ["*"]
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.app.post("/v1/completions")(self.completions)

    async def completions(self, completion_request: CompletionRequest):
        irc_format = MESSAGE_FORMAT_REGISTRY["irc"]
        messages = irc_format.parse(completion_request.prompt)
        em_user_id = UserID("em::" + self.em_name, "completions")
        config: Config = await self.get_config(None)
        if completion_request.temperature is not None:
            config.temperature = completion_request.temperature
        if completion_request.top_p is not None:
            config.top_p = completion_request.top_p
        if completion_request.max_tokens is not None:
            config.continuation_max_tokens = completion_request.max_tokens

        async def get_response_messages():
            response_messages = []
            async for message in self.generate_response(
                em_user_id, eager_iterable_to_async_iterable(messages), config
            ):
                response_messages.append(message)
            return response_messages

        tasks = []
        for i in range(completion_request.n):
            tasks.append(get_response_messages())
        response_message_arrays = await asyncio.gather(*tasks)
        response_choices = []
        for i, response_messages in enumerate(response_message_arrays):
            text = ""
            for response_message in response_messages:
                text += irc_format.render(response_message)
            response_choices.append(
                {
                    "text": text,
                    "index": i,
                    "logprobs": None,
                    "finish_reason": "unknown",  # not implemented
                }
            )
        return CompletionResponse(
            id="cmpl-ch2",  # not implemented
            created=int(time.time()),
            model=completion_request.model,
            choices=response_choices,
            usage={  # not implemented
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        )

    def start(self):
        # TODO: read port from config, read config from env, read unix socket from env
        uv_config = uvicorn.Config(
            self.app, port=6006, log_level="info", host="0.0.0.0"
        )
        self.uv_server = RapidShutdownUvicornServer(uv_config)
        self.uv_server.install_signal_handlers = lambda: None
        return self.uv_server.serve()

    def stop(self, sig, frame):
        return self.uv_server.handle_exit(sig, frame)
