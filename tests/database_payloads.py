"""Generate real application payloads for the isolated PostgreSQL test."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from study import RESOLUTION_ORDER, Participant, load_pool  # noqa: E402

pool, version = load_pool(ROOT / "image_manifest.csv", ROOT)
# Seven references per resolution exercise cycle boundaries and exhaustion.
reference_hashes = {digest for resolution in RESOLUTION_ORDER
                    for digest in list(dict.fromkeys(pair.reference.sha256 for pair in pool
                                                     if pair.reference.resolution == resolution))[:7]}
pool = tuple(pair for pair in pool if pair.reference.sha256 in reference_hashes)
person = Participant()
payloads = []
transition = "--legacy-transition" in sys.argv
for number in range(2 if transition else 10):
    person.start_batch(pool)
    if transition and number == 0:
        person.batch.comparisons = person.batch.comparisons[:10]
    for index in range(len(person.batch.comparisons)):
        person.batch.index = index
        person.answer(("left", "right")[index % 2])
    payload = person.prepare_submission(version)
    payloads.append(payload)
    person.confirm_submission(payload["p_batch"]["batch_id"])
print(json.dumps(payloads))
