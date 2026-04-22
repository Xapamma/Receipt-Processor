import json
import os
import re
from deepdiff import DeepDiff

# Folder containing your ground-truth validation JSON files.
VALIDATION_DIR = "validation/validation_texts"
# Folder containing extracted/actual JSON files to compare against validation.
ACTUAL_DIR = "data/texts25"
# Toggle verbose field-level differences for mismatches.
SHOW_DIFFS = True

# Running counters used for final summary metrics.
matches = 0
compared = 0
missing = 0
score_total = 0.0

# Iterate through each validation JSON file.
for filename in sorted(os.listdir(VALIDATION_DIR)):
    if not filename.endswith(".json"):
        continue

    # Extract the numeric ID from filename.
    match = re.search(r"\d+", filename)
    if not match:
        print(f"SKIP {filename}")
        continue

    # Build expected/actual paths where actual is ID-based (e.g., data/texts25/20.json).
    expected_path = f"{VALIDATION_DIR}/{filename}"
    actual_path = f"{ACTUAL_DIR}/{match.group(0)}.json"

    # Track files that are present in validation but missing in extracted outputs.
    if not os.path.exists(actual_path):
        missing += 1
        print(f"MISSING {filename} -> {match.group(0)}.json")
        continue

    # Load both JSON documents for comparison.
    with open(expected_path, "r", encoding="utf-8") as f:
        expected = json.load(f)
    with open(actual_path, "r", encoding="utf-8") as f:
        actual = json.load(f)

    # DeepDiff finds structural/content differences (ignoring list order).
    diff = DeepDiff(expected, actual, ignore_order=True)

    # Basic similarity score:
    # start at 1.0 (perfect), then subtract points based on how many differences were found
    # relative to the expected JSON size; never let the score go below 0.0. 
    diff_points = len(diff.affected_paths)
    total_points = max(len(expected), 1)
    file_score = max(0.0, 1.0 - (diff_points / total_points))

    compared += 1
    score_total += file_score

    # Report file-level result and optionally full diff details.
    if diff:
        print(f"❌ {filename} | score={file_score:.3f}")
        if SHOW_DIFFS:
            print(diff.pretty())
    else:
        matches += 1
        print(f"✅ {filename} | score={file_score:.3f}")

# Final aggregate summary.
print("\nSummary")
print(f"Compared: {compared}")
print(f"Matches: {matches}")
print(f"Mismatches: {compared - matches}")
print(f"Missing files: {missing}")
if compared:
    print(f"Exact match rate: {matches / compared:.1%}")
    print(f"Average eval score: {score_total / compared:.3f} (0 to 1)")
