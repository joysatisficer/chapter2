#!/usr/bin/env -S python -u

from ontology import (
    Config,
    ALIASES,
    EM_KEYS,
    SHARED_INTERFACE_KEYS,
    ALL_INTERFACE_KEYS,
    DEFAULTS,
    LEGACY_DEFAULTS,
    load_config_from_kv,
)
import yaml
from pathlib import Path
import os


def load_em_kv(name) -> dict:
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
            or key in ALIASES.keys()
            or key in EM_KEYS
            or key in SHARED_INTERFACE_KEYS
            or key in ALL_INTERFACE_KEYS
        )
        if valid_key(subpath.name):
            kv[subpath.name] = subpath.read_text()
        elif subpath.name.endswith(".yaml") and valid_key(
            key := subpath.name.removesuffix(".yaml")
        ):
            kv[key] = yaml.safe_load(subpath.read_text())
    if "name" not in kv:
        kv["name"] = name
    kv["emname"] = name
    # TODO: Replace with defaults versioning system
    return kv


def load_em(name) -> Config:
    kv = load_em_kv(name)
    # pprint.pprint(kv)

    if kv.get("legacy", False):
        defaults = LEGACY_DEFAULTS
        del kv["legacy"]
    else:
        defaults = DEFAULTS

    return load_config_from_kv(kv, defaults)


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
