import copy
import csv
import hashlib

import pytest

from study import Participant
from test_study import synthetic_pool
from tools.merge_drive_csv import REQUIRED_COLUMNS, merge_batches


def rows_for_batch(person, pool):
    person.start_batch(pool)
    for index in range(10):
        person.batch.index = index
        person.answer("left")
    payload = person.prepare_submission("d" * 64)
    rows = []
    for vote in payload["p_votes"]:
        rows.append({**vote, **{key: payload["p_batch"][key] for key in ("dataset_version", "sampler_version", "sampling_seed")},
                     "submitted_at": "2026-10-18T20:00:00Z", "submission_sha256": hashlib.sha256(b"test").hexdigest()})
    person.confirm_submission(payload["p_batch"]["batch_id"])
    return rows


def write_batch(path, rows):
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=sorted(REQUIRED_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def test_merge_preserves_unicode_and_removes_identical_download_copies(tmp_path):
    person = Participant()
    first = rows_for_batch(person, synthetic_pool())
    first[0]["prompt"] = 'Une étoile, "bleue"\nsur fond noir 🌟'
    second = rows_for_batch(person, synthetic_pool())
    paths = [tmp_path / name for name in ("pixel_votes_2.csv", "pixel_votes_1.csv", "pixel_votes_1_copy.csv")]
    for path, rows in zip(paths, (second, first, first)):
        write_batch(path, rows)
    columns, combined = merge_batches(paths)
    assert set(columns) == REQUIRED_COLUMNS
    assert len(combined) == 20
    assert [int(row["comparison_number"]) for row in combined] == list(range(1, 21))
    assert combined[0]["prompt"] == first[0]["prompt"]


def test_merge_rejects_conflicting_downloads(tmp_path):
    rows = rows_for_batch(Participant(), synthetic_pool())
    conflicting = copy.deepcopy(rows)
    conflicting[0]["prompt"] = "changed"
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    write_batch(first, rows)
    write_batch(second, conflicting)
    with pytest.raises(ValueError, match="Conflicting"):
        merge_batches([first, second])


def test_merge_rejects_incomplete_batches(tmp_path):
    path = tmp_path / "partial.csv"
    write_batch(path, rows_for_batch(Participant(), synthetic_pool())[:9])
    with pytest.raises(ValueError, match="10 complete"):
        merge_batches([path])
