import asyncio
import re

import discord
import discord.context_managers
import discord.utils
from discord.abc import Messageable


class ScheduleTyping(discord.context_managers.Typing):
    """discord.context_managers.Typing where the typing event is scheduled, instead of awaited, reducing latency"""

    def __init__(self, messageable: Messageable, typing: bool = True):
        super().__init__(messageable)
        self.typing = typing

    async def __aenter__(self) -> None:
        self.task: asyncio.Task[None] = self.loop.create_task(self.do_typing())
        self.task.add_done_callback(discord.context_managers._typing_done_callback)

    async def do_typing(self) -> None:
        channel = await self._get_channel()
        typing = channel._state.http.send_typing

        while True:
            if self.typing:
                await typing(channel.id)
            await asyncio.sleep(9)


def parse_discord_content(self: discord.Message, my_user_id: int, my_name: str) -> str:
    """discord.Message.clean_content() where "name" is used in place of display_name"""
    if self.guild:

        def resolve_member(id: int) -> str:
            if id == my_user_id:
                return "@" + my_name
            m = self.guild.get_member(id) or discord.utils.get(self.mentions, id=id)  # type: ignore
            return f"@{m.name}" if m else "@deleted-user"

        def resolve_role(id: int) -> str:
            r = self.guild.get_role(id) or discord.utils.get(self.role_mentions, id=id)  # type: ignore
            return f"@{r.name}" if r else "@deleted-role"

        def resolve_channel(id: int) -> str:
            c = self.guild._resolve_channel(id)  # type: ignore
            return f"#{c.name}" if c else "#deleted-channel"

    else:

        def resolve_member(id: int) -> str:
            if id == my_user_id:
                return "@" + my_name
            m = discord.utils.get(self.mentions, id=id)
            return f"@{m.name}" if m else "@deleted-user"

        def resolve_role(id: int) -> str:
            return "@deleted-role"

        def resolve_channel(id: int) -> str:
            return "#deleted-channel"

    transforms = {
        "@": resolve_member,
        "@!": resolve_member,
        "#": resolve_channel,
        "@&": resolve_role,
    }

    def repl(match: re.Match) -> str:
        type = match[1]
        id = int(match[2])
        transformed = transforms[type](id)
        return transformed

    result = re.sub(
        r"<(@[!&]?|#)([0-9]{15,20})>",
        repl,
        self.system_content if self.is_system() else self.content,
    )

    return discord.utils.escape_mentions(result)
