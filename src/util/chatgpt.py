import string


def strip_name(name: str):
    allowed = string.ascii_letters + string.digits + "_-"
    return "".join(character for character in name[:64] if character in allowed)
