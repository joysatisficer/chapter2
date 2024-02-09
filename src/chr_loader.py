import re
from copy import deepcopy
from typing import Callable
import yaml
import glob


def load_chr(dir, size=3) -> list[str]:
    chunks = []
    for file_contents in load_directory(dir):
        chunks.extend(
            process_file(file_contents, lambda x: make_fixed_size_chunks(size, x))[1]
        )
    return chunks


def load_directory(dir):
    for filename in glob.glob(dir + "/*.txt"):
        with open(filename, "r") as f:
            yield f.read()


def process_file(string: str, chunker: Callable = None):
    """
    Process a string representing a file for semantic search.

    Args:
        string (str): The file content as a string.
        chunker (Callable): The function to use for chunking the file.

    Returns:
        unchunked (List[dict]): List of dictionaries containing the unchunked text.
        chunked (List[dict]): List of dictionaries containing the chunked text.
    """
    unchunked, chunked = [], []

    def unique_same_order(iterable):
        seen = set()
        return [x for x in iterable if not (x in seen or seen.add(x))]

    def filter_blank(iterable):
        return filter(lambda x: bool(x), iterable)

    def parse_metadata(metadata: str) -> dict:
        if metadata.strip() == "":
            return {}
        else:
            metadata_obj = yaml.safe_load(metadata)
            del metadata_obj["comment"]
            return metadata_obj

    def split_metadata(string: str) -> (str, str):
        if string.lstrip().startswith("%%%"):
            discard, metadata, *rest = re.split(r"^|\n%%%\n", string)
            assert discard == ""
            return metadata.lstrip("%"), "\n%%%\n".join(rest)
        else:
            return "", string

    def flatten(l):
        return [item for sublist in l for item in sublist]

    metadata_string, contents = split_metadata(string)
    metadata = parse_metadata(metadata_string)

    unchunked = unique_same_order(
        filter_blank(line for line in contents.splitlines() if line != "---")
    )

    if "\n---\n" in string:
        if "chunking" in metadata and metadata["chunking"] is False:
            chunked = unique_same_order(contents.split("\n---\n"))
        else:
            chunked = unique_same_order(
                flatten(
                    [
                        chunker(section.splitlines())
                        for section in filter_blank(contents.split("\n---\n"))
                    ]
                )
            )
    else:
        chunked = deepcopy(unchunked)

    return unchunked, chunked


def make_fixed_size_chunks(size, data):
    return ["\n".join(data[i : i + size]) for i in range(0, len(data), size)]
