from faculties.character_faculty import character_faculty
from faculties.metaphor_search_faculty import metaphor_search_faculty
from interfaces.chatcompletions_interface import ChatCompletionsInterface
from interfaces.completions_interface import CompletionsInterface
from interfaces.discord_interface import DiscordInterface
from interfaces.mikoto_interface import MikotoInterface
from interfaces.addons.discord_generate_avatar import discord_generate_avatar

FACULTY_NAME_TO_FUNCTION = {
    "character": character_faculty,
    "metaphor_search": metaphor_search_faculty,
}
INTERFACE_NAME_TO_INTERFACE = {
    "discord": DiscordInterface,
    "mikoto": MikotoInterface,
    "completions": CompletionsInterface,
    "chatcompletions": ChatCompletionsInterface,
}
INTERFACE_ADDON_NAME_TO_ADDON = {
    "discord": {
        "generate_avatar": discord_generate_avatar,
    },
}
