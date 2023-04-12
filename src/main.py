import os
import time

import aioitertools.more_itertools

from declarations import Message, UserID, MessageHistory, Author
from discord_interface import DiscordInterface


async def generate_response(my_user_id: UserID, history: MessageHistory):
    my_external_name = "Chapter2"
    author = Author(my_user_id, my_external_name)
    recent_messages = await aioitertools.more_itertools.take(20, history)
    yield Message(author, recent_messages[0].message)
    yield Message(author, 'hello')
    yield Message(author, 'world', time.time() + 4)


interface = DiscordInterface(generate_response)
interface.run(os.environ['DISCORD_TOKEN'])
