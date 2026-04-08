import json
import os
import re
from deepdiff import DeepDiff

VALIDATION_DIR = "validation/validation_texts"
ACTUAL_DIR = "texts5"
SHOW_DIFFS = True

matches = 0
compared = 0
missing = 0
score_total = 0.0

for filename in sorted(os.listdir(VALIDATION_DIR)):
    if not filename.endswith(".json"):
        continue

    match = re.search(r"\d+", filename)
    if not match:
        print(f"SKIP {filename}")
        continue

    expected_path = f"{VALIDATION_DIR}/{filename}"
    actual_path = f"{ACTUAL_DIR}/{match.group(0)}.json"

    if not os.path.exists(actual_path):
        missing += 1
        print(f"MISSING {filename} -> {match.group(0)}.json")
        continue

    with open(expected_path, "r", encoding="utf-8") as f:
        expected = json.load(f)
    with open(actual_path, "r", encoding="utf-8") as f:
        actual = json.load(f)

    diff = DeepDiff(expected, actual, ignore_order=True)
    diff_points = len(diff.affected_paths)
    total_points = max(len(expected), 1)
    file_score = max(0.0, 1.0 - (diff_points / total_points))

    compared += 1
    score_total += file_score

    if diff:
        print(f"❌ {filename} | score={file_score:.3f}")
        if SHOW_DIFFS:
            print(diff.pretty())
    else:
        matches += 1
        print(f"✅ {filename} | score={file_score:.3f}")

print("\nSummary")
print(f"Compared: {compared}")
print(f"Matches: {matches}")
print(f"Mismatches: {compared - matches}")
print(f"Missing files: {missing}")
if compared:
    print(f"Exact match rate: {matches / compared:.1%}")
    print(f"Average eval score: {score_total / compared:.3f} (0 to 1)")
