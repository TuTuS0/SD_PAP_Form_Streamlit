import copy
import csv
from collections import Counter
from pathlib import Path

import pytest

from study import (
    ACHIEVEMENTS, BATCH_SIZE, COMPETITORS, RESOLUTION_ORDER, Asset, DatasetError, Pair, Participant,
    PoolTooSmall, earned_achievements, load_pool, next_achievement, sample_batch,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def real_pool():
    return load_pool(ROOT / "image_manifest.csv", ROOT)


def synthetic_pool(count=21):
    pairs = []
    for index in range(count):
        method = COMPETITORS[index % 7]
        res = RESOLUTION_ORDER[index % 3]
        ref = Asset("SDPAP", res, str(index), str(index), "", "images/ref.png", f"{index + 1:064x}")
        other = Asset(method, res, str(index), str(index), "", "images/other.png", "b" * 64)
        pairs.append(Pair(f"{index:064x}", ref, other))
    return tuple(pairs)


def complete(person, pool, version="d" * 64):
    person.start_batch(pool)
    for index in range(BATCH_SIZE):
        person.batch.index = index
        person.answer(("left", "right")[index % 2])
    payload = person.prepare_submission(version)
    person.confirm_submission(payload["p_batch"]["batch_id"])
    return payload


def test_repository_matching(real_pool):
    pool, version = real_pool
    assert len(pool) == 2100
    assert len({p.comparison_id for p in pool}) == 2100
    assert len(version) == 64
    for pair in pool:
        assert pair.reference.method == "SDPAP"
        assert pair.competitor.method in COMPETITORS
        assert pair.reference.resolution == pair.competitor.resolution
        assert pair.reference.prompt_id == pair.competitor.prompt_id


def test_seed_is_reproducible_and_sides_are_independent(real_pool):
    pool, _ = real_pool
    first = sample_batch(pool, [], "fixed-seed")
    assert first == sample_batch(pool, [], "fixed-seed")
    assert first != sample_batch(pool, [], "different-seed")
    assert {p.reference_on_left for p in first} == {True, False}


def test_balanced_across_methods_resolutions_and_prompts(real_pool):
    pool, _ = real_pool
    history = []
    for batch in range(20):
        selected = sample_batch(pool, history, f"batch-{batch}")
        assert len(selected) == len({p.pair.comparison_id for p in selected}) == BATCH_SIZE
        assert len({p.pair.reference.sha256 for p in selected}) == BATCH_SIZE
        assert [p.pair.reference.resolution for p in selected] == [48, 64, 128] * 5
        assert not {p.pair.comparison_id for p in selected} & {p.pair.comparison_id for p in history}
        batch_methods = Counter(p.pair.competitor.method for p in selected)
        batch_resolutions = Counter(p.pair.reference.resolution for p in selected)
        assert set(batch_methods) == set(COMPETITORS)
        assert max(batch_methods.values()) - min(batch_methods.values()) <= 1
        assert set(batch_resolutions) == {48, 64, 128}
        assert max(batch_resolutions.values()) - min(batch_resolutions.values()) <= 1
        assert len({p.pair.reference.prompt_id for p in selected}) == BATCH_SIZE
        history.extend(selected)
        methods = Counter(p.pair.competitor.method for p in history)
        resolutions = Counter(p.pair.reference.resolution for p in history)
        assert max(methods.values()) - min(methods.values()) <= 1
        assert max(resolutions.values()) - min(resolutions.values()) <= 1
    # Coverage must not concentrate on a small subset of the 100 prompts.
    prompts = Counter(p.pair.reference.prompt_id for p in history)
    assert len(prompts) == 100
    assert max(prompts.values()) <= 4


@pytest.mark.parametrize("pool_size", [15, 16, 19, 20, 21, 27])
def test_exhaustion_uses_unseen_first_then_repeats_without_short_batches(pool_size):
    pool = synthetic_pool(pool_size)
    history = []
    seen = {resolution: set() for resolution in RESOLUTION_ORDER}
    sizes = Counter(pair.reference.resolution for pair in pool)
    for batch in range(12):
        selected = sample_batch(pool, history, str(batch))
        assert len(selected) == len({p.pair.comparison_id for p in selected}) == BATCH_SIZE
        assert [p.pair.reference.resolution for p in selected] == [48, 64, 128] * 5
        for item in selected:
            resolution = item.pair.reference.resolution
            if len(seen[resolution]) < sizes[resolution]:
                assert item.pair.comparison_id not in seen[resolution]
                assert not item.is_repeat
            else:
                assert item.is_repeat
                assert item.pool_cycle > 1
            seen[resolution].add(item.pair.comparison_id)
        history.extend(selected)


def test_tiny_pool_has_no_partial_batch():
    with pytest.raises(PoolTooSmall):
        sample_batch(synthetic_pool(BATCH_SIZE - 1), [], "seed")


def test_entire_repository_pool_can_be_exhausted_and_continued(real_pool):
    pool, _ = real_pool
    history = []
    for number in range(len(pool) // BATCH_SIZE):
        selected = sample_batch(pool, history, f"full-pool-{number}")
        assert len({item.pair.reference.sha256 for item in selected}) == BATCH_SIZE
        history.extend(selected)
        if (number + 1) % 20 == 0:
            assert len({item.pair.reference.sha256 for item in history[-300:]}) == 300
    assert len({item.pair.comparison_id for item in history}) == len(pool)
    assert not any(item.is_repeat for item in history)
    continued = sample_batch(pool, history, "after-exhaustion")
    assert len(continued) == BATCH_SIZE
    assert all(item.is_repeat and item.pool_cycle == 2 for item in continued)


def test_reference_images_do_not_repeat_across_participant_batches(real_pool):
    pool, _ = real_pool
    person = Participant()
    for _ in range(20):
        complete(person, pool)
    assert len(person.history) == 300
    assert len({item.pair.reference.sha256 for item in person.history}) == 300
    assert not any(item.is_repeat for item in person.history)
    complete(person, pool)
    assert len({item.pair.reference.sha256 for item in person.history[-BATCH_SIZE:]}) == BATCH_SIZE
    # The image can now recur, but its competitor pair is still new.
    assert not any(item.is_repeat for item in person.history[-BATCH_SIZE:])


def test_many_pairs_from_one_reference_cannot_form_a_batch():
    from dataclasses import replace

    pool = tuple(replace(pair, reference=replace(pair.reference, sha256="a" * 64))
                 for pair in synthetic_pool())
    with pytest.raises(PoolTooSmall, match="reference images"):
        sample_batch(pool, [], "seed")


def test_many_batches_share_identity_and_numbering():
    person = Participant()
    original_id = person.participant_id
    ids = set()
    for number in range(1, 26):
        payload = complete(person, synthetic_pool())
        assert person.participant_id == original_id
        assert payload["p_batch"]["batch_number"] == number
        assert len(payload["p_votes"]) == payload["p_batch"]["batch_size"] == BATCH_SIZE
        assert [v["comparison_number"] for v in payload["p_votes"]] == list(range((number - 1) * BATCH_SIZE + 1, number * BATCH_SIZE + 1))
        assert [v["target_resolution"] for v in payload["p_votes"]] == [48, 64, 128] * 5
        assert all(v["participant_id"] == original_id for v in payload["p_votes"])
        assert payload["p_batch"]["batch_id"] not in ids
        ids.add(payload["p_batch"]["batch_id"])
        person.confirm_submission(payload["p_batch"]["batch_id"])
        assert person.completed == number * BATCH_SIZE
    assert earned_achievements(person.completed) == list(ACHIEVEMENTS)
    assert next_achievement(person.completed) is None
    person.finish()
    assert person.phase == "finished"


def test_partial_batch_cannot_submit_and_choices_survive_back_navigation():
    person = Participant()
    person.start_batch(synthetic_pool())
    person.answer("left")
    first = person.batch.answers[0]
    person.batch.index = 1
    person.answer("right")
    person.batch.index = 0
    person.answer("left")
    assert person.batch.answers[0] is first
    with pytest.raises(ValueError):
        person.prepare_submission("d" * 64)
    with pytest.raises(ValueError):
        person.start_batch(synthetic_pool())
    with pytest.raises(ValueError):
        person.finish()
    assert person.completed == 0
    assert person.total_evaluated == 2


def test_submission_is_frozen_until_acknowledged():
    person = Participant()
    person.start_batch(synthetic_pool())
    for index in range(BATCH_SIZE):
        person.batch.index = index
        person.answer("right")
    payload = person.prepare_submission("d" * 64)
    frozen = copy.deepcopy(payload)
    assert person.prepare_submission("other-version") == frozen
    with pytest.raises(ValueError):
        person.answer("left")
    with pytest.raises(ValueError):
        person.confirm_submission("wrong-id")
    assert person.completed == 0


def test_every_vote_requires_a_winner():
    person = Participant()
    person.start_batch(synthetic_pool())
    with pytest.raises(ValueError):
        person.answer("tie")
    assert not person.batch.answers
    for index in range(BATCH_SIZE):
        person.batch.index = index
        person.answer("left")
    payload = person.prepare_submission("d" * 64)
    assert all(vote["winner"] == vote["left_method"] for vote in payload["p_votes"])


@pytest.mark.parametrize("action", ["continue", "finish"])
def test_saved_batch_actions_are_immediate_even_with_a_legacy_animation_deadline(action):
    person = Participant()
    complete(person, synthetic_pool())
    person.celebration_ready_at = 123.0
    if action == "continue":
        person.start_batch(synthetic_pool())
        assert person.batch.number == 2 and person.phase == "evaluation"
    else:
        person.finish()
        assert person.phase == "finished"
    assert person.completed == BATCH_SIZE
    assert person.celebration_ready_at is None


@pytest.mark.parametrize("threshold,name,icon", ACHIEVEMENTS)
def test_achievements_depend_only_on_count(threshold, name, icon):
    assert (threshold, name, icon) not in earned_achievements(threshold - 1)
    assert (threshold, name, icon) in earned_achievements(threshold)
    assert next_achievement(threshold - 1) == (threshold, name, icon)


def test_a_legacy_batch_can_finish_then_continue_with_fifteen_comparisons():
    person = Participant()
    person.start_batch(synthetic_pool())
    person.batch.comparisons = person.batch.comparisons[:10]
    for index in range(10):
        person.batch.index = index
        person.answer("left")
    payload = person.prepare_submission("d" * 64)
    person.confirm_submission(payload["p_batch"]["batch_id"])
    assert person.completed == 10 and person.completed_batches == 1
    payload = complete(person, synthetic_pool())
    assert payload["p_batch"]["batch_number"] == 2
    assert payload["p_batch"]["batch_size"] == 15
    assert [vote["comparison_number"] for vote in payload["p_votes"]] == list(range(11, 26))
    assert person.completed == 25 and person.completed_batches == 2


def test_missing_resolution_cannot_be_replaced_by_extra_images_from_other_resolutions():
    pool = tuple(pair for pair in synthetic_pool(60) if pair.reference.resolution != 128)
    with pytest.raises(PoolTooSmall):
        sample_batch(pool, [], "missing-stratum")


def test_shared_hashes_do_not_strand_later_resolution_slots():
    from dataclasses import replace

    pool = synthetic_pool(30)
    groups = {resolution: [pair for pair in pool if pair.reference.resolution == resolution]
              for resolution in RESOLUTION_ORDER}
    shared = {pair.reference.sha256 for pair in groups[48][:5]}
    groups[64] = [replace(pair, reference=replace(pair.reference, sha256=groups[48][index].reference.sha256))
                  for index, pair in enumerate(groups[64][:5])]
    feasible = tuple(groups[48] + groups[64] + groups[128][:5])
    for seed in range(4):
        selected = sample_batch(feasible, [], str(seed))
        assert len({item.pair.reference.sha256 for item in selected}) == 15
        assert not shared & {item.pair.reference.sha256 for item in selected if item.pair.reference.resolution == 48}
    impossible = tuple(groups[48][:5] + groups[64] + groups[128])
    with pytest.raises(PoolTooSmall):
        sample_batch(impossible, [], "overlapping-strata")


def write_manifest(tmp_path, rows):
    image = tmp_path / "images" / "one.png"
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(b"test image")
    with (tmp_path / "manifest.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["method", "resolution", "prompt_id", "image_id", "prompt", "path", "verified", "sha256"])
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path / "manifest.csv"


def test_unverified_rows_cannot_enter_the_pool(tmp_path):
    path = write_manifest(tmp_path, [{"verified": "false"}])
    with pytest.raises(PoolTooSmall):
        load_pool(path, tmp_path)


@pytest.mark.parametrize("field,value", [("path", "../outside.png"), ("resolution", "bad"),
                                         ("sha256", "changed"), ("method", "unknown")])
def test_invalid_verified_manifest_fails_closed(tmp_path, field, value):
    row = dict(method="SDPAP", resolution=48, prompt_id="0", image_id="0", prompt="",
               path="images/one.png", verified="true", sha256="")
    row[field] = value
    path = write_manifest(tmp_path, [row])
    with pytest.raises(DatasetError):
        load_pool(path, tmp_path)
