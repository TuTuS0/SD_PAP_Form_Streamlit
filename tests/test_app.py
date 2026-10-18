from pathlib import Path
import re
import hashlib
import json
import time

import requests

from streamlit.testing.v1 import AppTest

from storage import PreviewStore, StorageError

ROOT = Path(__file__).resolve().parents[1]


def app(monkeypatch):
    monkeypatch.setenv("PIXEL_STUDY_PREVIEW", "1")
    result = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert not result.exception
    result.button(key="start").click().run()
    assert not result.exception
    return result


def finish_batch(at, choice="Left image", unlock=True):
    for _ in range(10):
        assert not at.radio
        assert not any(button.key == "next" for button in at.button)
        vote_button(at, choice).click().run()
        assert not at.exception
    if unlock and at.session_state.person.phase == "milestone":
        unlock_milestone(at)
    return at


def unlock_milestone(at):
    # Advance only the stored UI deadline, without sleeping or changing votes.
    at.session_state.person.celebration_ready_at = time.monotonic() - 1
    at.run()
    assert not at.exception
    return at


def vote_button(at, choice):
    side = {"Left image": "left", "Right image": "right"}[choice]
    return next(button for button in at.button if button.key.startswith(f"vote_{side}_"))


def test_three_batches_progress_achievements_and_finish(monkeypatch):
    at = app(monkeypatch)
    participant_id = at.session_state.person.participant_id
    for number in range(1, 4):
        finish_batch(at)
        person = at.session_state.person
        assert person.phase == "milestone"
        assert person.completed == number * 10
        assert person.participant_id == participant_id
        assert len(at.session_state.store.submissions) == number
        visible = " ".join(item.value for item in at.markdown)
        assert f"Lot {number} terminé" in visible
        assert "Pixel Explorer" in visible
        if number == 3:
            assert "Pixel Enthusiast" in visible and "Nouveau" in visible
        if number < 3:
            at.button(key="continue").click().run()
    at.button(key="finish").click().run()
    assert not at.exception
    assert at.session_state.person.phase == "finished"
    assert at.session_state.person.completed == 30


def test_back_preserves_and_can_change_previous_vote(monkeypatch):
    at = app(monkeypatch)
    original_pairs = at.session_state.person.batch.comparisons[:]
    vote_button(at, "Left image").click().run()
    assert at.session_state.person.batch.index == 1
    vote_button(at, "Right image").click().run()
    assert at.session_state.person.batch.index == 2
    at.button(key="back").click().run()
    at.button(key="back").click().run()
    assert 'class="image-card is-selected"' in " ".join(c.value for c in at.markdown)
    vote_button(at, "Right image").click().run()
    assert at.session_state.person.batch.answers[0].side == "right"
    assert at.session_state.person.batch.index == 1
    assert at.session_state.person.batch.answers[1].side == "right"
    assert at.session_state.person.batch.comparisons == original_pairs
    visible = " ".join(item.value for item in at.markdown)
    visible = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[blinded image]", visible)
    assert not any(method in visible for method in ("SDPAP", "GPTImage", "MYOS", "APIP"))


def test_live_mode_cannot_start_without_persistent_storage(monkeypatch):
    monkeypatch.delenv("PIXEL_STUDY_PREVIEW", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    at.secrets["supabase"] = {"url": "https://test.supabase.co", "secret_key": "invalid"}
    at.run()
    assert not at.exception
    assert at.button(key="start").disabled
    assert "store" not in at.session_state


def test_uncertain_save_retains_votes_and_retry_does_not_duplicate(monkeypatch):
    at = app(monkeypatch)

    class UncertainStore(PreviewStore):
        failed = False

        def save_batch(self, payload):
            receipt = super().save_batch(payload)
            if not self.failed:
                self.failed = True
                raise StorageError("Response lost after commit")
            return receipt

    at.session_state.store = UncertainStore()
    finish_batch(at)
    assert at.session_state.person.phase == "submitting"
    assert at.session_state.person.completed == 0
    assert len(at.session_state.person.batch.answers) == 10
    batch_id = at.session_state.person.batch.batch_id
    at.button(key="retry").click().run()
    assert not at.exception
    assert at.session_state.person.phase == "milestone"
    assert at.session_state.person.completed == 10
    assert at.session_state.person.batch.batch_id == batch_id
    assert len(at.session_state.store.submissions) == 1


def test_each_push_button_records_and_immediately_advances(monkeypatch):
    at = app(monkeypatch)
    assert len([button for button in at.button if button.key.startswith("vote_")]) == 2
    assert not any(button.label == "Tie" for button in at.button)
    for index, label in enumerate(("Left image", "Right image")):
        button = vote_button(at, label)
        assert not button.disabled
        button.click().run()
        assert not at.exception
        assert at.session_state.person.batch.index == index + 1
        assert at.session_state.person.batch.answers[index].side == {
            "Left image": "left", "Right image": "right"}[label]
    assert at.session_state.person.completed == 0
    assert len(at.session_state.store.submissions) == 0


def test_comparison_transitions_follow_navigation_and_use_equal_cards(monkeypatch):
    at = app(monkeypatch)
    initial = " ".join(item.value for item in at.markdown)
    assert "animation:comparison_enter_0 280ms" in initial
    assert initial.count('<figure class="image-card" ') == 2
    vote_button(at, "Left image").click().run()
    following = " ".join(item.value for item in at.markdown)
    assert "animation:comparison_enter_1 280ms" in following
    at.button(key="back").click().run()
    returned = " ".join(item.value for item in at.markdown)
    assert "animation:comparison_enter_0 280ms" in returned
    assert returned.count('<figure class="image-card is-selected" ') == 1


def test_google_drive_storage_submits_one_complete_batch_from_live_app(monkeypatch):
    monkeypatch.delenv("PIXEL_STUDY_PREVIEW", raising=False)
    submissions = []

    def send(url, **kwargs):
        envelope = kwargs["json"]
        payload = json.loads(envelope["payload"])
        submissions.append(payload)

        class Receipt:
            status_code = 200

            def json(self):
                return {"ok": True, "batch_id": payload["p_batch"]["batch_id"], "row_count": 10,
                        "submission_sha256": hashlib.sha256(envelope["payload"].encode()).hexdigest()}

        return Receipt()

    monkeypatch.setattr(requests, "post", send)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    at.secrets["storage"] = {"backend": "google_drive"}
    at.secrets["google_drive"] = {"web_app_url": "https://script.google.com/macros/s/test-deployment/exec",
                                  "shared_secret": "local-tests-only-" + "x" * 32}
    at.run()
    assert not at.exception and not at.button(key="start").disabled
    at.button(key="start").click().run()
    finish_batch(at)
    assert at.session_state.person.phase == "milestone"
    assert len(submissions) == 1 and len(submissions[0]["p_votes"]) == 10
    assert all(vote["winner"] != "tie" for vote in submissions[0]["p_votes"])


def test_saved_batch_has_two_second_locked_stats_transition_then_main_action(monkeypatch):
    at = app(monkeypatch)
    finish_batch(at, unlock=False)
    person = at.session_state.person
    assert person.completed == 10 and person.phase == "milestone"
    assert len(at.session_state.store.submissions) == 1  # Already saved before celebration.
    assert 0 < person.celebration_ready_at - time.monotonic() <= 2.0
    assert not at.button  # No Continue, Finish, vote, or Back during this phase.
    visible = " ".join(item.value for item in at.markdown)
    assert 'class="counting-total"' in visible
    assert "--count-from:0;--count-to:10" in visible
    deadline = person.celebration_ready_at
    at.run()
    assert at.session_state.person.celebration_ready_at == deadline
    assert not at.button
    unlock_milestone(at)
    assert at.button(key="continue").label == "En faire 10 autres"
    assert at.button(key="finish").label == "Terminer"
    assert at.session_state.person.celebration_ready_at is None
    assert at.session_state.person.celebrated_batch == person.batch.batch_id
    assert len(at.session_state.store.submissions) == 1
    # A normal rerun must not re-lock an already celebrated batch.
    at.run()
    assert at.button(key="continue").label == "En faire 10 autres"


def test_next_batch_gets_its_own_counter_animation(monkeypatch):
    at = app(monkeypatch)
    finish_batch(at)
    at.button(key="continue").click().run()
    finish_batch(at, unlock=False)
    assert not at.button
    visible = " ".join(item.value for item in at.markdown)
    assert "--count-from:10;--count-to:20" in visible
    assert at.session_state.person.completed == 20
    assert len(at.session_state.store.submissions) == 2
