import os
from pathlib import Path

import pytest
import yaml

from declarations import Message, Author
from ontology import ExaSearchFacultyConfig, load_config_from_kv, EmConfig
from faculties.exa_search_faculty import exa_search_faculty


@pytest.mark.asyncio
async def test_exa_search_faculty():
    kv = {
        "folder": "/tmp",
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
    em = load_config_from_kv(kv).em
    async for message in exa_search_faculty(
        mock_message_history_iterator(em), ExaSearchFacultyConfig(), em
    ):
        print(message)


async def mock_message_history_iterator(em: EmConfig):
    # todo: iterable to lazy iterable function
    messages = [
        Message(Author("alice"), "hello"),
        Message(Author("bob"), "hi alice!"),
        Message(Author(em.name), "hi bob!"),
        Message(Author("alice"), f"hi {em.name}!"),
    ][::-1]
    for message in messages:
        yield message
