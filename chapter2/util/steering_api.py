from pathlib import Path
import json


# load features.json
path = Path(__file__).parent / "features.json"
with open(path, "r") as f:
    FEATURES = json.load(f)

# loop through features and create a mapping from desc to index
DESC_TO_INDEX = {feature["desc"]: feature["index"] for feature in FEATURES}

# loop through features and create a mapping from index to desc
INDEX_TO_DESC = {feature["index"]: feature["desc"] for feature in FEATURES}

# create filtered list of features that are usable
USABLE_FEATURES = [feature for feature in FEATURES if feature["usable"]]

# print(len(USABLE_FEATURES))
