import asyncio
import time
from typing import AsyncIterable, TypeVar, Callable, Awaitable

from declarations import GenerateResponse, Message, UserID, Author

import discord


class DiscordInterface(discord.Client):
    def __init__(self, generate_response: GenerateResponse) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.generate_response = generate_response

    async def on_message(self, message: discord.Message) -> None:
        # check if we should respond to the message
        if not await self.should_reply(message):
            return
        async with message.channel.typing():
            my_user_id = await discord_user_to_user_id(self.user)
            response_messages = await self.generate_response(
                my_user_id,
                map_async_iterator(
                    message.channel.history(limit=None),
                    self.discord_message_to_message
                )
            )
            for reply_message in response_messages:
                if reply_message.author == my_user_id:
                    await wait_until_timestamp(reply_message.timestamp, message.channel.typing)
                    await message.channel.send(reply_message.message)

    async def discord_message_to_message(self, message: discord.Message) -> Message:
        return Message(
            Author(
                await discord_user_to_user_id(message.author),
                message.author.name
            ),
            message.content,
            message.created_at.timestamp()
        )

    async def should_reply(self, message: discord.Message):
        return message.author != self.user


async def wait_until_timestamp(timestamp, coroutine):
    current_time = time.time()
    if timestamp > current_time:
        # to reduce latency, only send a typing event if there is an actual delay
        async with coroutine():
            await asyncio.sleep(timestamp - current_time)


async def discord_user_to_user_id(user: discord.User) -> UserID:
    return UserID(user.id, 'discord')


T = TypeVar('T')
R = TypeVar('R')


async def map_async_iterator(async_iterator: AsyncIterable[T], fn: Callable[[T], Awaitable[R]]) -> AsyncIterable[R]:
    """
    Calls a function on each item returned by an async iterator
    :param fn:
    :param async_iterator:
    :return:
    """
    async for item in async_iterator:
        yield await fn(item)
