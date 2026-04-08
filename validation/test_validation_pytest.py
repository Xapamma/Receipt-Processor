import json
import re
from pathlib import Path

import pytest
from deepdiff import DeepDiff

VALIDATION_DIR = Path("validation/validation_texts")
ACTUAL_DIR = Path("texts5")


def _validation_files():
    files = sorted(VALIDATION_DIR.glob("*.json"))
    if not files:
        pytest.skip(f"No validation files found in {VALIDATION_DIR}")
    return files


@pytest.mark.parametrize("expected_path", _validation_files(), ids=lambda p: p.name)
def test_validation_json_matches(expected_path: Path):
    match = re.search(r"\d+", expected_path.name)
    assert match is not None, f"No receipt id found in filename: {expected_path.name}"

    actual_path = ACTUAL_DIR / f"{match.group(0)}.json"
    assert actual_path.exists(), f"Missing extracted file: {actual_path}"

    with expected_path.open("r", encoding="utf-8") as f:
        expected = json.load(f)
    with actual_path.open("r", encoding="utf-8") as f:
        actual = json.load(f)

    diff = DeepDiff(expected, actual, ignore_order=True)
    assert not diff, f"\n{diff.pretty()}"
