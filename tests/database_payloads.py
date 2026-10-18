"""Generate real application payloads for the isolated PostgreSQL test."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from study import Participant, load_pool  # noqa: E402

pool, version = load_pool(ROOT / "image_manifest.csv", ROOT)
# Thirteen reference images exercise reference-cycle boundaries inside a batch.
reference_hashes = list(dict.fromkeys(pair.reference.sha256 for pair in pool))[:13]
pool = tuple(pair for pair in pool if pair.reference.sha256 in reference_hashes)
person = Participant()
payloads = []
for _ in range(10):
    person.start_batch(pool)
    for index in range(10):
        person.batch.index = index
        person.answer(("left", "right")[index % 2])
    payload = person.prepare_submission(version)
    payloads.append(payload)
    person.confirm_submission(payload["p_batch"]["batch_id"])
print(json.dumps(payloads))
