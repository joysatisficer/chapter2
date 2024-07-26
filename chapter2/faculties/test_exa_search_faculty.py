import os
from pathlib import Path

import pytest
import yaml

from declarations import Message, Author
from ontology import ExaSearchFacultyConfig, Config, load_config_from_kv
from faculties.exa_search_faculty import exa_search_faculty


@pytest.mark.asyncio
async def test_exa_search_faculty():
    kv = {
        "em_folder": "/tmp",
        "name": "tmp",
    }
    parent_dir = Path(__file__).resolve().parents[1]
    try:
        with open(os.path.expanduser("~/.config/chapter2/vendors.yaml")) as file:
            kv = {**kv, **yaml.safe_load(file)}
    except FileNotFoundError:
        pass
    try:
        with open(parent_dir / "ems/vendors.yaml") as file:
            kv = {**kv, **yaml.safe_load(file)}
    except FileNotFoundError:
        pass
    config = load_config_from_kv(kv)
    async for message in exa_search_faculty(
        mock_message_history_iterator(config), ExaSearchFacultyConfig(), config
    ):
        print(message)


async def mock_message_history_iterator(config: Config):
    # todo: iterable to lazy iterable function
    messages = [
        Message(Author("alice"), "hello"),
        Message(Author("bob"), "hi alice!"),
        Message(Author(config.name), "hi bob!"),
        Message(Author("alice"), f"hi {config.name}!"),
    ][::-1]
    for message in messages:
        yield message
