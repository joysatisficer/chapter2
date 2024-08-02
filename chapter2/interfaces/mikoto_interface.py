import asyncio
import contextlib
import time
from datetime import datetime

import mikoto

from util.asyncutil import async_generator_to_reusable_async_iterable
from declarations import GenerateResponse, Message, Author, UserID
from ontology import MikotoInterfaceConfig, Config
from interfaces.discord_interface import is_continue_command, isempty
import ontology


# todo: refactor so that subclassing and using super with AbstractInterface are possible
class MikotoInterface(mikoto.MikotoClient):
    def __init__(
        self,
        base_config: Config,
        generate_response: GenerateResponse,
        em_name: str,
        interface_config: MikotoInterfaceConfig,
    ):
        super().__init__()
        self.base_config = base_config
        self.generate_response = generate_response
        self.em_name = em_name
        self.interface_config = interface_config
        self.messages.on_create(self.on_message)

    async def on_message(self, this_message: mikoto.Message):
        if is_continue_command(this_message.content):
            command_message = this_message
        else:
            command_message = None
        try:
            my_user_id = UserID((await self.users.me()).id, "mikoto")
            config = ontology.overlay(
                self.base_config.model_dump(), self.interface_config.custom_config
            )
            if not await self.should_reply(this_message, config):
                return

            async def message_history():
                cursor = this_message.id
                if command_message is None:
                    yield await self.mikoto_message_to_message(config, this_message)
                while True:
                    message = None
                    for message in await self.messages.list(
                        this_message.channelId, cursor, 20
                    ):
                        if not is_continue_command(message.content):
                            yield await self.mikoto_message_to_message(config, message)
                    if message is None:
                        return
                    else:
                        cursor = message.id

            response_messages = self.generate_response(
                my_user_id,
                async_generator_to_reusable_async_iterable(message_history),
                config.em,
            )
            with typing(this_message.channelId, self.messages):
                async for reply_message in response_messages:
                    if reply_message.author.user_id == my_user_id and not isempty(
                        reply_message.content
                    ):
                        current_time = time.time()
                        if reply_message.timestamp > current_time:
                            await asyncio.sleep(reply_message.timestamp - current_time)
                        await self.messages.send(
                            this_message.channelId, reply_message.content
                        )
        finally:
            if command_message is not None:
                await self.messages.delete(this_message.channelId, this_message.id)

    async def mikoto_message_to_message(
        self, config: Config, message: mikoto.Message
    ) -> Message:
        if message.author == await self.users.me():
            author_name = config.em.name
        else:
            author_name = message.author.name
        if message.authorId is None:
            author_id = "system"
        elif message.author == await self.users.me():
            author_id = "em::" + self.em_name
        else:
            author_id = message.authorId
        return Message(
            Author(author_name, UserID(author_id, "mikoto")),
            message.content,
            datetime.fromisoformat(message.timestamp).timestamp(),
        )

    async def should_reply(self, message: mikoto.Message, config: Config):
        return message.author.id != (await self.users.me()).id and (
            self.interface_config.allowed_users is None
            or message.author.id in self.interface_config.allowed_users
        )

    async def start(self):
        await self._login_internal(self.interface_config.auth)

    async def stop(self):
        await self.client.sio.disconnect()


@contextlib.contextmanager
def typing(channel_id, messages):
    task = asyncio.get_event_loop().create_task(loop_typing(channel_id, messages))
    try:
        yield
    finally:
        task.cancel()


async def loop_typing(channel_id, messages):
    while True:
        await messages.start_typing(channel_id)
        await asyncio.sleep(4)
