#!/usr/bin/env -S python -u
import os
import signal
import sys
import asyncio
from pathlib import Path

import yaml

import ontology
from generate_response import generate_response
from interfaces import INTERFACE_NAME_TO_INTERFACE, INTERFACE_ADDON_NAME_TO_ADDON
from interfaces.deserves_reply import deserves_reply
from ontology import Config
from declarations import Message, UserID, Author
from util.asyncutil import eager_iterable_to_async_iterable


async def rehearse_em(config: Config):
    """Run an em in mock mode to populate caches and test the em"""
    mock_message_hist = eager_iterable_to_async_iterable(
        [
            Message(Author("alice"), "hello"),
            Message(Author("bob"), "hi alice!"),
            Message(Author(config.em.name), "hi bob!"),
            Message(Author("alice"), f"hi {config.em.name}!"),
        ][::-1]
    )
    user_id = UserID("em::" + config.em.name, "rehearsal")
    config.em.vendors = {"fake-local": ontology.SingleVendorConfig(provides=[".*"])}
    config.em.exa_search_api_key = "sk-rehearsal"
    for interface in config.interfaces:
        if getattr(interface, "reply_on_sim", False):
            d = await deserves_reply(
                generate_response,
                config,
                user_id,
                mock_message_hist,
                interface.reply_on_sim,
            )
            assert isinstance(d, bool)
    async for response in generate_response(
        user_id,
        mock_message_hist,
        config.em,
    ):
        pass


def load_em(name) -> Config:
    parent_dir = Path(__file__).resolve().parents[1]
    em_folder = parent_dir / "ems" / name
    kv = {
        # vendors.yaml is deprecated
        **load_optional(os.path.expanduser("~/.config/chapter2/vendors.yaml")),
        **load_optional(parent_dir / "ems/vendors.yaml"),
        **load_optional(os.path.expanduser("~/.config/chapter2/config.yaml")),
        **load_optional(parent_dir / "ems/config.yaml"),
        **load_optional(em_folder / "config.yaml"),
        "folder": em_folder,
    }
    for subpath in em_folder.iterdir():
        valid_key = (
            lambda key: key in Config.model_fields.keys()
            or key in Config.model_fields.keys()
            or key in ontology.ALIASES.keys()
            or key in ontology.EM_KEYS
            or key in ontology.SHARED_INTERFACE_KEYS
            or key in ontology.ALL_INTERFACE_KEYS
        )
        if valid_key(subpath.name):
            kv[subpath.name] = subpath.read_text()
        elif subpath.name.endswith(".yaml") and valid_key(
            key := subpath.name.removesuffix(".yaml")
        ):
            kv[key] = yaml.safe_load(subpath.read_text())
    if "name" not in kv:
        kv["name"] = name
    # TODO: Replace with defaults versioning system
    if kv.get("legacy", False):
        defaults = ontology.LEGACY_DEFAULTS
        del kv["legacy"]
    else:
        defaults = ontology.DEFAULTS
    return ontology.load_config_from_kv(kv, defaults)


async def run_em(name, end_to_end_test=False):
    config = load_em(name)
    interfaces = []
    for interface in config.interfaces:
        if end_to_end_test and hasattr(interface, "end_to_end_test"):
            interface.end_to_end_test = True
        interface_name = interface.name
        addons = []
        if hasattr(interface, "addons"):
            for addon in interface.addons:
                addons.append(
                    INTERFACE_ADDON_NAME_TO_ADDON[interface_name][addon.name](addon)
                )
        base_interface = INTERFACE_NAME_TO_INTERFACE[interface_name]
        if len(addons) == 0:
            interfaces.append((base_interface, interface))
        else:
            interfaces.append(
                (
                    type(
                        "Custom" + base_interface.__name__,
                        (*addons, base_interface),
                        {},
                    ),
                    interface,
                )
            )

    interface_instances = []
    for interface, interface_config in interfaces:
        interface_instances.append(
            interface(
                config, generate_response, interface_config.name, interface_config
            )
        )

    def handle_interrupt(sig, frame):
        for interface_instance in interface_instances:
            interface_instance.stop(sig, frame)

    signal.signal(signal.SIGINT, handle_interrupt)

    await asyncio.gather(
        asyncio.create_task(rehearse_em(config.model_copy(deep=True))),
        *[interface_instance.start() for interface_instance in interface_instances],
    )

    exit_code = 0
    for interface_instance in interface_instances:
        if (
            hasattr(interface_instance, "end_to_end_test_fail")
            and interface_instance.end_to_end_test_fail
        ):
            exit_code = 1
    return exit_code


def load_optional(filename):
    try:
        with open(filename) as file:
            kv = yaml.safe_load(file)
            if kv is None:
                return {}
            else:
                return kv
    except FileNotFoundError:
        return {}


if __name__ == "__main__":
    from rich.traceback import install
    import fire
    import selectors

    if "PYCHARM_HOSTED" not in os.environ:
        install(suppress=(asyncio, fire, selectors), show_locals=True)

    def _(name, end_to_end_test=False):
        result = asyncio.run(run_em(name, end_to_end_test))
        sys.exit(result)

    fire.Fire(_)
