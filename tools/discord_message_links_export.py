import dataclasses
import os
import re

import discord
from chapter2 import message_formats
from chapter2.declarations import Message, Author
from util.discord_improved import parse_discord_content

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

urls = open("urls.txt").read().splitlines()


def discord_message_to_message(message: discord.Message) -> Message:
    return Message(
        Author(message.author.name),
        parse_discord_content(
            message, client.application.id, client.application.name
        ).strip(),
    )


@dataclasses.dataclass
class HasID:
    id: int


@client.event
async def on_ready():
    try:
        with open("out.txt", "w") as f:
            for url in urls:
                guild, channel, message = re.search(r"(\d+)/(\d+)/(\d+)", url).groups()
                guild = await client.fetch_guild(int(guild))
                channel = await guild.fetch_channel(int(channel))
                ch2_messages = []
                async for result in channel.history(
                    limit=1,
                    before=HasID(int(message)),
                ):
                    ch2_messages.append(discord_message_to_message(result))
                ch2_messages.append(
                    discord_message_to_message(
                        await channel.fetch_message(int(message))
                    )
                )
                s = ""
                for ch2_message in ch2_messages:
                    s += message_formats.IRCMessageFormat().render(ch2_message)
                print(s, "---\n", sep="", end="")
                f.write(s)
                f.write("---\n")
    finally:
        await client.close()


if __name__ == "__main__":
    client.run(os.environ["DISCORD_TOKEN"])
