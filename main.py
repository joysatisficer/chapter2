import os
import time

from declarations import Message, UserID, MessageHistory, Author
from discord_interface import DiscordInterface


async def generate_response(my_user_id: UserID, history: MessageHistory):
    my_external_name = "Chapter2"
    author = Author(my_user_id, my_external_name)
    async for message in history:
        last_message = message
        break
    return [
        Message(author, last_message.message, 0),
        Message(author, 'hello', time.time()),
        Message(author, 'world', time.time() + 1),
    ]


interface = DiscordInterface(generate_response)
interface.run(os.environ['DISCORD_TOKEN'])
