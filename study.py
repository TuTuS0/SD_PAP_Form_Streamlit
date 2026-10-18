"""Blinded comparisons, balanced sampling, and the participant state machine."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BATCH_SIZE = 10
REFERENCE_METHOD = "SDPAP"
COMPETITORS = ("AOP", "APIP", "FLUX", "GPTImage", "MYOS", "PAP", "PIA")
METHODS = (*COMPETITORS, REFERENCE_METHOD)
SAMPLER_VERSION = "balanced-reference-without-replacement-v2"
ACHIEVEMENTS = (
    (10, "Pixel Explorer", "✦"),
    (30, "Pixel Enthusiast", "✧"),
    (50, "Pixel Expert", "◆"),
    (100, "Pixel Master", "❖"),
    (200, "Pixel Legend", "★"),
)


class DatasetError(ValueError):
    """The manifest cannot safely produce matched research comparisons."""


class PoolTooSmall(ValueError):
    """Ten distinct comparisons cannot be formed."""


@dataclass(frozen=True)
class Asset:
    method: str
    resolution: int
    prompt_id: str
    image_id: str
    prompt: str
    path: str
    sha256: str


@dataclass(frozen=True)
class Pair:
    comparison_id: str
    reference: Asset
    competitor: Asset


@dataclass(frozen=True)
class PresentedPair:
    pair: Pair
    reference_on_left: bool
    pool_cycle: int
    is_repeat: bool

    @property
    def left(self) -> Asset:
        return self.pair.reference if self.reference_on_left else self.pair.competitor

    @property
    def right(self) -> Asset:
        return self.pair.competitor if self.reference_on_left else self.pair.reference


def load_pool(manifest_path: Path, base_dir: Path) -> tuple[tuple[Pair, ...], str]:
    """Only explicitly verified prompt/resolution matches enter the pool.

    Paths stay under images/. Content hashes also detect changed source files.
    The app caches this validation, then keeps a fixed snapshot per session.
    """
    required = {"method", "resolution", "prompt_id", "image_id", "prompt", "path", "verified"}
    assets: dict[tuple[str, int, str], Asset] = {}
    paths: set[Path] = set()
    prompt_texts: dict[str, str] = {}
    try:
        with manifest_path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not required.issubset(reader.fieldnames or []):
                raise DatasetError("The image manifest is missing required columns.")
            for line, row in enumerate(reader, start=2):
                if row["verified"].strip().lower() != "true":
                    continue
                method = row["method"].strip()
                prompt_id = row["prompt_id"].strip()
                image_id = row["image_id"].strip()
                resolution = int(row["resolution"])
                if method not in METHODS or not prompt_id or not image_id or resolution <= 0:
                    raise DatasetError(f"Invalid verified metadata on manifest line {line}.")
                raw_path = Path(row["path"])
                path = (base_dir / raw_path).resolve()
                if raw_path.is_absolute() or not path.is_relative_to((base_dir / "images").resolve()):
                    raise DatasetError(f"Image path leaves images/ on manifest line {line}.")
                key = (method, resolution, prompt_id)
                if key in assets or path in paths:
                    raise DatasetError(f"Duplicate verified image or match key on line {line}.")
                prompt = row["prompt"].strip()
                if prompt:
                    if prompt_id in prompt_texts and prompt_texts[prompt_id] != prompt:
                        raise DatasetError(f"Conflicting text for prompt {prompt_id}.")
                    prompt_texts[prompt_id] = prompt
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if row.get("sha256") and row["sha256"].strip() != digest:
                    raise DatasetError(f"Image content changed on manifest line {line}; rebuild the manifest.")
                assets[key] = Asset(method, resolution, prompt_id, image_id, prompt,
                                    path.relative_to(base_dir.resolve()).as_posix(), digest)
                paths.add(path)
    except (OSError, TypeError, KeyError, ValueError) as exc:
        if isinstance(exc, DatasetError):
            raise
        raise DatasetError("The image manifest contains missing files or invalid rows.") from exc

    pairs = []
    for (method, resolution, prompt_id), reference in sorted(assets.items()):
        if method != REFERENCE_METHOD:
            continue
        for competitor in COMPETITORS:
            other = assets.get((competitor, resolution, prompt_id))
            if other is None:
                continue
            identity = [prompt_id, resolution, reference.image_id, reference.sha256,
                        competitor, other.image_id, other.sha256]
            comparison_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            # A prompt may be supplied in just one manifest row.
            text = prompt_texts.get(prompt_id, "")
            reference_with_text = Asset(**{**reference.__dict__, "prompt": text})
            other_with_text = Asset(**{**other.__dict__, "prompt": text})
            pairs.append(Pair(comparison_id, reference_with_text, other_with_text))
    if len(pairs) < BATCH_SIZE:
        raise PoolTooSmall("At least 10 distinct verified image pairs are required to start a batch.")
    if len({pair.reference.sha256 for pair in pairs}) < BATCH_SIZE:
        raise PoolTooSmall("At least 10 distinct reference images are required to start a batch.")
    version = hashlib.sha256(json.dumps([
        (pair.comparison_id, pair.reference.prompt) for pair in pairs
    ], ensure_ascii=False).encode()).hexdigest()
    return tuple(pairs), version


def sample_batch(pool: tuple[Pair, ...], history: list[PresentedPair], seed: str) -> list[PresentedPair]:
    """Draw reference images without replacement across the whole participation.

    All reference images must be seen before one is reused. Within a batch,
    reference images remain distinct even at a reference-cycle boundary. For
    each reference, prefer an unseen competitor pair, then balance methods,
    resolutions, and prompts. Sparse strata use the best available balance.
    """
    if len({pair.reference.sha256 for pair in pool}) < BATCH_SIZE:
        raise PoolTooSmall("At least 10 distinct reference images are needed for a full batch.")
    rng = random.Random(seed)
    methods: Counter = Counter()
    resolutions: Counter = Counter()
    strata: Counter = Counter()
    prompts: Counter = Counter()
    batch_methods: Counter = Counter()
    batch_resolutions: Counter = Counter()
    batch_prompts: Counter = Counter()
    reference_exposures: Counter = Counter()
    pair_exposures: Counter = Counter()
    for item in history:
        pair = item.pair
        reference_exposures[pair.reference.sha256] += 1
        pair_exposures[pair.comparison_id] += 1
        methods[pair.competitor.method] += 1
        resolutions[pair.reference.resolution] += 1
        strata[pair.competitor.method, pair.reference.resolution] += 1
        prompts[pair.reference.prompt_id] += 1

    selected: list[PresentedPair] = []
    selected_references: set[str] = set()
    for _ in range(BATCH_SIZE):
        candidates = [pair for pair in pool
                      if pair.reference.sha256 not in selected_references]

        def priority(pair: Pair) -> tuple:
            return (reference_exposures[pair.reference.sha256], pair_exposures[pair.comparison_id],
                    batch_methods[pair.competitor.method], methods[pair.competitor.method],
                    batch_resolutions[pair.reference.resolution], resolutions[pair.reference.resolution],
                    batch_prompts[pair.reference.prompt_id], prompts[pair.reference.prompt_id],
                    strata[pair.competitor.method, pair.reference.resolution], rng.random())

        pair = min(candidates, key=priority)
        appearances = pair_exposures[pair.comparison_id]
        selected.append(PresentedPair(pair, rng.choice((True, False)), appearances + 1, appearances > 0))
        selected_references.add(pair.reference.sha256)
        reference_exposures[pair.reference.sha256] += 1
        pair_exposures[pair.comparison_id] += 1
        methods[pair.competitor.method] += 1
        resolutions[pair.reference.resolution] += 1
        strata[pair.competitor.method, pair.reference.resolution] += 1
        prompts[pair.reference.prompt_id] += 1
        batch_methods[pair.competitor.method] += 1
        batch_resolutions[pair.reference.resolution] += 1
        batch_prompts[pair.reference.prompt_id] += 1
    return selected


def earned_achievements(total: int) -> list[tuple[int, str, str]]:
    return [achievement for achievement in ACHIEVEMENTS if total >= achievement[0]]


def next_achievement(total: int) -> tuple[int, str, str] | None:
    return next((achievement for achievement in ACHIEVEMENTS if total < achievement[0]), None)


@dataclass
class Answer:
    side: str
    answered_at: str


@dataclass
class Batch:
    batch_id: str
    number: int
    seed: str
    comparisons: list[PresentedPair]
    answers: dict[int, Answer] = field(default_factory=dict)
    index: int = 0


@dataclass
class Participant:
    participant_id: str = field(default_factory=lambda: str(uuid4()))
    phase: str = "welcome"
    completed: int = 0
    history: list[PresentedPair] = field(default_factory=list)
    batch: Batch | None = None
    pending_submission: dict | None = None
    celebrated_batch: str | None = None
    celebration_ready_at: float | None = None

    @property
    def total_evaluated(self) -> int:
        unsaved = len(self.batch.answers) if self.batch and self.phase in {"evaluation", "submitting"} else 0
        return self.completed + unsaved

    def start_batch(self, pool: tuple[Pair, ...]) -> None:
        if self.phase not in {"welcome", "milestone"}:
            raise ValueError("Complete and submit the current batch first.")
        if self.celebration_ready_at is not None:
            raise ValueError("Wait for the completion animation to finish.")
        seed = secrets.token_hex(16)
        comparisons = sample_batch(pool, self.history, seed)
        self.batch = Batch(str(uuid4()), self.completed // BATCH_SIZE + 1, seed, comparisons)
        self.pending_submission = None
        self.celebration_ready_at = None
        self.phase = "evaluation"

    def answer(self, side: str) -> None:
        if self.phase != "evaluation" or self.batch is None or side not in {"left", "right"}:
            raise ValueError("There is no active comparison to answer.")
        previous = self.batch.answers.get(self.batch.index)
        if previous is None or previous.side != side:
            self.batch.answers[self.batch.index] = Answer(side, datetime.now(timezone.utc).isoformat())

    def prepare_submission(self, dataset_version: str) -> dict:
        if self.pending_submission is not None:
            return self.pending_submission
        batch = self.batch
        if self.phase != "evaluation" or batch is None or set(batch.answers) != set(range(BATCH_SIZE)):
            raise ValueError("Exactly 10 answered comparisons are required before submission.")
        if any(answer.side not in {"left", "right"} for answer in batch.answers.values()):
            raise ValueError("Every comparison must have a selected winning image.")
        votes = []
        for index, item in enumerate(batch.comparisons):
            answer = batch.answers[index]
            winner = {"left": item.left.method, "right": item.right.method}[answer.side]
            votes.append({
                "participant_id": self.participant_id, "batch_id": batch.batch_id,
                "batch_number": batch.number, "comparison_number": self.completed + index + 1,
                "batch_comparison_number": index + 1, "comparison_id": item.pair.comparison_id,
                "prompt_id": item.left.prompt_id, "prompt": item.left.prompt,
                "target_resolution": item.left.resolution,
                "left_method": item.left.method, "right_method": item.right.method,
                "left_image_id": item.left.image_id, "right_image_id": item.right.image_id,
                "left_path": item.left.path, "right_path": item.right.path,
                "left_sha256": item.left.sha256, "right_sha256": item.right.sha256,
                "selected_side": answer.side, "winner": winner, "answered_at": answer.answered_at,
                "pool_cycle": item.pool_cycle, "is_repeat": item.is_repeat,
                "repeat_reason": "pool_exhausted" if item.is_repeat else "",
            })
        self.pending_submission = {
            "p_batch": {"participant_id": self.participant_id, "batch_id": batch.batch_id,
                        "batch_number": batch.number, "dataset_version": dataset_version,
                        "sampler_version": SAMPLER_VERSION, "sampling_seed": batch.seed},
            "p_votes": votes,
        }
        self.phase = "submitting"
        return self.pending_submission

    def confirm_submission(self, batch_id: str) -> None:
        if self.batch is None or self.batch.batch_id != batch_id:
            raise ValueError("Submission receipt does not match this batch.")
        if self.phase == "milestone":
            return  # A repeated acknowledgement must not add participation twice.
        if self.phase != "submitting":
            raise ValueError("This batch is not awaiting a receipt.")
        self.history.extend(self.batch.comparisons)
        self.completed += BATCH_SIZE
        self.phase = "milestone"

    def finish(self) -> None:
        if self.phase != "milestone":
            raise ValueError("Finish is available after a saved batch.")
        if self.celebration_ready_at is not None:
            raise ValueError("Wait for the completion animation to finish.")
        self.phase = "finished"
