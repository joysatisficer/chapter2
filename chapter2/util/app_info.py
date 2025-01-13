import base64
from pathlib import Path
import yaml


def get_app_id_from_token(token: str) -> str:
    # The app ID is the first part of the token before the first dot
    first_part = token.split(".")[0]
    # Decode from base64, padding with = if needed
    padding = 4 - (len(first_part) % 4)
    if padding != 4:
        first_part += "=" * padding
    # Decode and convert to string
    decoded = base64.b64decode(first_part)
    return str(int(decoded))


def get_emname_id_map() -> dict:
    parent_dir = Path(__file__).resolve().parents[2]
    em_folder = parent_dir / "ems"

    emname_to_id = {}
    # loop through all folders in em_folder
    for em_folder in em_folder.iterdir():
        if em_folder.is_dir():
            # if there is a file called discord_token, read it and add to map
            if (em_folder / "discord_token").exists():
                token = (em_folder / "discord_token").read_text()
                emname_to_id[em_folder.name] = get_app_id_from_token(token)

    return emname_to_id


def get_steerable_ems() -> list[str]:
    parent_dir = Path(__file__).resolve().parents[2]
    em_folder = parent_dir / "ems"
    steerable_ems = []
    for em_folder in em_folder.iterdir():
        if em_folder.is_dir():
            if (em_folder / "config.yaml").exists():
                config = yaml.safe_load((em_folder / "config.yaml").read_text())
                if (
                    config.get("continuation_model", None)
                    == "claude-3-sonnet-20240229-steering-preview@17998"
                ):
                    steerable_ems.append(em_folder.name)
    return steerable_ems


# token = "MTMwNTMxNDU0MzY0MzI2NzA5Mw.GEqmJb.v2D6Zc1zRAJjPDNSJmGEu7OIzE2AIhkFfufOh8"
# app_id = get_app_id_from_token(token)
# print(app_id)  # Will print something like "123456789012345678"
