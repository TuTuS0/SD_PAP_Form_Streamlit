from pathlib import Path
import re
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from streamlit.testing.v1 import AppTest

from storage import PreviewStore, StorageError
from study import BATCH_SIZE

ROOT = Path(__file__).resolve().parents[1]


def app(monkeypatch, language="fr"):
    monkeypatch.setenv("PIXEL_STUDY_PREVIEW", "1")
    result = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert not result.exception
    if language != "fr":
        result.radio(key="language_choice").set_value(language).run()
        assert not result.exception
    result.button(key="start").click().run()
    assert not result.exception
    return result


def finish_batch(at, choice="Left image"):
    for _ in range(BATCH_SIZE):
        assert not at.radio
        assert not any(button.key == "next" for button in at.button)
        vote_button(at, choice).click().run()
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
        assert person.completed == number * BATCH_SIZE
        assert person.participant_id == participant_id
        assert len(at.session_state.store.submissions) == number
        visible = " ".join(item.value for item in at.markdown)
        assert f"Lot {number} terminé" in visible
        assert "Pixel Explorer" in visible
        if number == 2:
            assert "Pixel Enthusiast" in visible and "Nouveau" in visible
        if number < 3:
            at.button(key="continue").click().run()
        assert not at.get("bidi_component")  # Sharing is only offered after Finish.
    at.button(key="finish").click().run()
    assert not at.exception
    assert at.session_state.person.phase == "finished"
    assert at.session_state.person.completed == 45
    shared = json.loads(at.get("bidi_component")[0].proto.json)
    assert "J’ai évalué 45 paires de pixel arts" in shared["text"]
    assert "compare 15 paires d’images" in shared["text"]
    assert shared["share_label"] == "Partager mon score"
    assert participant_id not in json.dumps(shared)
    assert not any(method in shared["text"] for method in ("SDPAP", "GPTImage", "MYOS", "APIP"))
    at.run()
    assert len(at.session_state.store.submissions) == 3


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


@pytest.mark.parametrize("language", ["fr", "en"])
def test_uncertain_save_retains_votes_and_retry_does_not_duplicate(monkeypatch, language):
    at = app(monkeypatch, language)

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
    assert len(at.session_state.person.batch.answers) == BATCH_SIZE
    assert not at.get("balloons")
    assert not any(button.key in {"continue", "finish"} for button in at.button)
    if language == "en":
        visible = " ".join(item.value for item in at.markdown)
        assert "Connection interrupted" in visible
        assert "Your 15 choices are kept in this session." in visible
        assert at.button(key="retry").label == "Try again"
    batch_id = at.session_state.person.batch.batch_id
    at.button(key="retry").click().run()
    assert not at.exception
    assert at.session_state.person.phase == "milestone"
    assert at.session_state.person.completed == BATCH_SIZE
    assert at.session_state.person.batch.batch_id == batch_id
    assert len(at.session_state.store.submissions) == 1
    assert len(at.get("balloons")) == 1
    assert not at.button(key="continue").disabled
    assert not at.button(key="finish").disabled


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
                return {"ok": True, "batch_id": payload["p_batch"]["batch_id"], "row_count": len(payload["p_votes"]),
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
    assert len(submissions) == 1 and len(submissions[0]["p_votes"]) == BATCH_SIZE
    assert [vote["target_resolution"] for vote in submissions[0]["p_votes"]] == [48, 64, 128] * 5
    assert all(vote["winner"] != "tie" for vote in submissions[0]["p_votes"])


def test_saved_batch_shows_actions_immediately_and_celebrates_once(monkeypatch):
    at = app(monkeypatch)
    finish_batch(at)
    person = at.session_state.person
    assert person.completed == BATCH_SIZE and person.phase == "milestone"
    assert len(at.session_state.store.submissions) == 1  # Already saved before celebration.
    assert len(at.get("balloons")) == 1
    visible = " ".join(item.value for item in at.markdown)
    assert '<strong>15</strong> comparaisons' in visible
    assert at.button(key="continue").label == "En faire 15 autres"
    assert at.button(key="finish").label == "Terminer"
    assert at.session_state.person.celebration_ready_at is None
    assert at.session_state.person.celebrated_batch == person.batch.batch_id
    assert len(at.session_state.store.submissions) == 1
    # Reruns keep actions available without replaying the celebration or saving again.
    at.run()
    assert at.button(key="continue").label == "En faire 15 autres"
    assert not at.get("balloons")
    assert len(at.session_state.store.submissions) == 1


def test_next_batch_gets_its_own_balloons_and_immediate_actions(monkeypatch):
    at = app(monkeypatch)
    finish_batch(at)
    at.button(key="continue").click().run()
    finish_batch(at)
    assert len(at.get("balloons")) == 1
    assert not at.button(key="continue").disabled
    assert not at.button(key="finish").disabled
    visible = " ".join(item.value for item in at.markdown)
    assert '<strong>30</strong> comparaisons' in visible
    assert at.session_state.person.completed == 30
    assert len(at.session_state.store.submissions) == 2


def test_welcome_language_switch_updates_copy_without_replacing_participant(monkeypatch):
    monkeypatch.setenv("PIXEL_STUDY_PREVIEW", "1")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    participant_id = at.session_state.person.participant_id
    assert at.radio(key="language_choice").options == ["Français", "English"]
    assert at.button(key="start").label == "Commencer"
    for language, question, start, footer in (
        ("en", "Which pixel art do you prefer?", "Start", "Anonymous"),
        ("fr", "Quel pixel art préfères-tu ?", "Commencer", "Anonyme"),
    ):
        at.radio(key="language_choice").set_value(language).run()
        assert not at.exception
        assert at.button(key="start").label == start
        visible = " ".join(item.value for item in at.markdown)
        assert question in visible and footer in visible
        assert at.session_state.person.participant_id == participant_id
        assert at.session_state.person.phase == "welcome"
        assert not at.session_state.store.submissions


def test_english_persists_across_votes_reruns_batches_and_finish(monkeypatch):
    at = app(monkeypatch, "en")
    participant_id = at.session_state.person.participant_id
    for number in (1, 2):
        at.run()  # The welcome-only widget has been removed; its language must persist.
        assert not at.exception
        assert at.session_state.language == "en"
        assert not at.radio
        visible = " ".join(item.value for item in at.markdown)
        assert "Which pixel art do you prefer?" in visible
        assert f"Batch {number}" in visible
        assert "Image 1/15" in visible
        assert at.button(key="back").label == "← Back"
        assert vote_button(at, "Left image").label == "Left"
        assert vote_button(at, "Right image").label == "Right"
        finish_batch(at, "Right image")
        visible = " ".join(item.value for item in at.markdown)
        assert f"Batch {number} completed" in visible
        assert f"<strong>{number * BATCH_SIZE}</strong> comparisons" in visible
        assert ("to Pixel Enthusiast" if number == 1 else "to Pixel Expert") in visible
        assert at.button(key="continue").label == "Do 15 more"
        assert at.button(key="finish").label == "Finish"
        assert at.session_state.person.participant_id == participant_id
        if number == 1:
            assert "New: Pixel Explorer" in visible
            at.button(key="continue").click().run()
    for payload in at.session_state.store.submissions.values():
        assert len(payload["p_votes"]) == BATCH_SIZE
        assert all(vote["selected_side"] == "right" for vote in payload["p_votes"])
        assert all(vote["winner"] == vote["right_method"] for vote in payload["p_votes"])
    at.button(key="finish").click().run()
    assert not at.exception
    assert at.session_state.person.phase == "finished"
    assert "Thank you!" in " ".join(item.value for item in at.markdown)
    shared = json.loads(at.get("bidi_component")[0].proto.json)
    assert "I evaluated 30 pairs of pixel art" in shared["text"]
    assert "compare 15 pairs of images" in shared["text"]
    assert shared["share_label"] == "Share my score"
    assert participant_id not in json.dumps(shared)


def test_unavailable_study_message_also_switches_language(monkeypatch):
    monkeypatch.delenv("PIXEL_STUDY_PREVIEW", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    at.secrets["supabase"] = {"url": "https://test.supabase.co", "secret_key": "invalid"}
    at.run()
    at.radio(key="language_choice").set_value("en").run()
    assert not at.exception
    assert at.button(key="start").label == "Start" and at.button(key="start").disabled
    assert "Participation will open soon." in " ".join(item.value for item in at.markdown)


def test_fifteen_comparisons_follow_the_resolution_cycle_and_only_then_submit(monkeypatch):
    at = app(monkeypatch)
    resolutions = []
    for index in range(15):
        batch = at.session_state.person.batch
        resolutions.append(batch.comparisons[batch.index].left.resolution)
        assert at.session_state.person.completed == 0
        assert not at.session_state.store.submissions
        visible = " ".join(item.value for item in at.markdown)
        assert f"Image {index + 1}/15" in visible
        assert 'aria-valuemax="15"' in visible
        assert f'aria-valuenow="{index}"' in visible
        vote_button(at, "Left image").click().run()
        assert not at.exception
    assert resolutions == [48, 64, 128] * 5
    assert at.session_state.person.completed == 15
    assert len(at.session_state.store.submissions) == 1


def test_hot_reload_keeps_an_active_legacy_batch_then_starts_the_new_protocol(monkeypatch):
    at = app(monkeypatch)
    person = at.session_state.person
    participant_id = person.participant_id
    person.batch.comparisons = person.batch.comparisons[:10]
    at.session_state.person = SimpleNamespace(**vars(person))  # Simulate an old class retained by Streamlit.
    at.run()
    for _ in range(10):
        vote_button(at, "Left image").click().run()
        assert not at.exception
    assert at.session_state.person.completed == 10
    at.button(key="continue").click().run()
    batch = at.session_state.person.batch
    assert batch.number == 2 and len(batch.comparisons) == 15
    assert [item.left.resolution for item in batch.comparisons] == [48, 64, 128] * 5
    assert at.session_state.person.participant_id == participant_id
    finish_batch(at)
    assert at.session_state.person.completed == 25
    assert len(at.session_state.store.submissions) == 2


@pytest.mark.parametrize("language,label", [("fr", "En faire 15 autres"), ("en", "Do 15 more")])
def test_final_screen_can_resume_with_the_same_participant_and_vote_history(monkeypatch, language, label):
    at = app(monkeypatch, language)
    participant_id = at.session_state.person.participant_id
    finish_batch(at)
    references = {item.pair.reference.sha256 for item in at.session_state.person.history}
    at.button(key="finish").click().run()
    assert at.button(key="resume").label == label
    shared = json.loads(at.get("bidi_component")[0].proto.json)
    assert shared["messages_label"] == ("Envoyer par message" if language == "fr" else "Send a message")
    at.button(key="resume").click().run()
    assert not at.exception
    person = at.session_state.person
    assert person.phase == "evaluation" and person.completed == 15
    assert person.participant_id == participant_id and at.session_state.language == language
    assert person.batch.number == 2 and person.batch.index == 0
    assert [item.left.resolution for item in person.batch.comparisons] == [48, 64, 128] * 5
    assert not references & {item.pair.reference.sha256 for item in person.batch.comparisons}
    assert len(person.history) == 15 and len(at.session_state.store.submissions) == 1
    assert not at.get("bidi_component")
    finish_batch(at)
    assert at.session_state.person.completed == 30
    assert len(at.session_state.store.submissions) == 2
    at.button(key="finish").click().run()
    shared = json.loads(at.get("bidi_component")[0].proto.json)
    assert "30" in shared["text"]
