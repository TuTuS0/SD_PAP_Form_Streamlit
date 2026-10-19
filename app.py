"""Run with: streamlit run app.py"""
from __future__ import annotations

import base64
import html
import io
import os
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps
from streamlit.errors import StreamlitSecretNotFoundError

from i18n import LANGUAGES, translate
from score_share import render_score_share
from storage import PreviewStore, StorageError, create_store
from study import BATCH_SIZE, DatasetError, Participant, PoolTooSmall, earned_achievements, load_pool, next_achievement

ROOT = Path(__file__).resolve().parent
CHOICES = {"Gauche": "left", "Droite": "right"}

st.set_page_config(page_title=translate(st.session_state.get("language", "fr"), "page_title"),
                   page_icon="✦", layout="centered", initial_sidebar_state="collapsed")


def t(key: str, **values) -> str:
    return translate(st.session_state.get("language", "fr"), key, **{"size": BATCH_SIZE, **values})


def change_language():
    # Keep a separate, non-widget key: the selector is only present on welcome.
    st.session_state.language = st.session_state.language_choice


def styles():
    # CSS-only st.html occupies no space in the page.
    st.html(ROOT / "ui.css")


def brand(preview: bool):
    """Keep the entry point without adding a header above the comparison."""


@st.cache_resource(show_spinner=False)
def dataset(manifest_mtime: int):
    return load_pool(ROOT / "image_manifest.csv", ROOT)


@st.cache_data(show_spinner=False, max_entries=256)
def blinded_image(relative_path: str, content_hash: str) -> str:
    """Identical metadata-free canvases; no method paths reach the client."""
    with Image.open(ROOT / relative_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image = ImageOps.contain(image, (768, 768), method=Image.Resampling.NEAREST)
        canvas = Image.new("RGBA", (768, 768), (245, 246, 250, 255))
        canvas.alpha_composite(image, ((768 - image.width) // 2, (768 - image.height) // 2))
        buffer = io.BytesIO()
        canvas.convert("RGB").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def stats_footer(person: Participant, pool=()):
    earned = earned_achievements(person.completed)
    last_size = len(person.batch.comparisons) if person.batch else BATCH_SIZE
    badges = []
    for threshold, name, icon in earned:
        fresh = person.phase == "milestone" and person.completed - last_size < threshold <= person.completed
        title = f'{name} · {threshold} {t("comparisons")}' + (f' · {t("new")}' if fresh else "")
        badges.append(f'<span class="achievement{" new" if fresh else ""}" title="{title}" role="img" aria-label="{title}">{icon}</span>')
    latest = earned[-1][1] if earned else ""
    unlocked = any(person.completed - last_size < threshold <= person.completed for threshold, _, _ in earned)
    rank = t("new_rank", name=latest) if unlocked and person.phase == "milestone" else latest
    if person.phase == "finished":
        rank = ""  # The current grade has its own prominent place beside the final score.
    upcoming = next_achievement(person.completed)
    next_text = t("next_achievement", remaining=upcoming[0] - person.completed, name=upcoming[1]) if upcoming else t("all_achievements")
    note = ""
    if pool and not ({p.reference.sha256 for p in pool} - {item.pair.reference.sha256 for item in person.history}):
        note = f'<p class="pool-note">{t("pool_note")}</p>'
    batches = person.completed_batches
    st.markdown(f'<footer class="stats-footer"><div class="totals"><span><strong>{person.completed}</strong> {t("comparisons")}</span>'
                f'<span class="separator">·</span><span><strong>{batches}</strong> {t("batch" if batches == 1 else "batches")}</span></div>'
                f'<div class="earned">{"".join(badges)}<span>{rank}</span></div>'
                f'<p class="next-achievement">{next_text}</p>{note}</footer>', unsafe_allow_html=True)


def welcome(person, pool, ready, message=""):
    with st.container(key="welcome_actions"):
        st.markdown(f'<div class="welcome-copy"><p class="eyebrow">{t("page_title")}</p>'
                    f'<h1>{t("question")}</h1><p>{t("intro")}</p></div>', unsafe_allow_html=True)
        if st.button(t("start"), type="primary", width="stretch", disabled=not ready, key="start"):
            person.start_batch(pool)
            st.rerun()
        if message:
            st.markdown(f'<p class="small-note">{html.escape(message)}</p>', unsafe_allow_html=True)
    with st.container(key="welcome_bottom"):
        st.markdown(f'<footer class="welcome-footer">{t("welcome_time")}<br>'
                    f'{t("welcome_submission")}</footer>', unsafe_allow_html=True)
        st.radio("Langue / Language", options=list(LANGUAGES),
                 index=list(LANGUAGES).index(st.session_state.language), format_func=LANGUAGES.get,
                 horizontal=True, label_visibility="collapsed", key="language_choice", on_change=change_language)


@st.fragment
def evaluate(person: Participant, version: str):
    if person.phase != "evaluation":
        st.rerun()
    batch = person.batch
    size = len(batch.comparisons)
    item = batch.comparisons[batch.index]
    previous = batch.answers.get(batch.index)
    animation = f"comparison_enter_{batch.index}"
    st.markdown(f'<style>@keyframes {animation} {{from {{opacity:0;transform:translateY(8px)}}'
                f'to {{opacity:1;transform:translateY(0)}}}}'
                f'.st-key-evaluation_scene {{animation:{animation} 280ms ease both;}}</style>', unsafe_allow_html=True)
    with st.container(key="evaluation_screen"):
        answered = len(batch.answers)
        st.markdown(f'<div class="evaluation-top"><span>{t("image_number", number=batch.index + 1, size=size)}</span>'
                    f'<span class="batch-label">{t("batch_number", number=batch.number)}</span></div>'
                    f'<div class="batch-progress" role="progressbar" aria-label="{t("batch_progress", number=batch.number)}" '
                    f'aria-valuemin="0" aria-valuemax="{size}" aria-valuenow="{answered}">'
                    f'<i style="width:{answered * 100 / size:.2f}%"></i></div>', unsafe_allow_html=True)
        with st.container(key="evaluation_scene"):
            st.markdown(f'<h1 class="evaluation-question">{t("question")}</h1>', unsafe_allow_html=True)
            if item.left.prompt:
                with st.popover(t("view_prompt"), type="tertiary"):
                    st.write(item.left.prompt)
            try:
                images = [blinded_image(asset.path, asset.sha256) for asset in (item.left, item.right)]
            except (OSError, ValueError):
                st.markdown(f'<p class="small-note">{t("image_error")}</p>', unsafe_allow_html=True)
                if st.button(t("retry"), width="stretch", key="reload_images"):
                    st.rerun()
                return
            cards = []
            for label, side, image in zip((t("left_image"), t("right_image")), ("left", "right"), images):
                selected = previous and previous.side == side
                cards.append(f'<figure class="image-card{" is-selected" if selected else ""}" data-side="{side}" '
                             'style="flex:1 1 0%;min-width:0;width:0;margin:0;aspect-ratio:1;box-sizing:border-box">'
                             f'<img src="data:image/png;base64,{image}" alt="{label}" decoding="async" '
                             'style="display:block;width:100%;height:100%;object-fit:contain" /></figure>')
            # Keep the left/right geometry in the markup as well as the theme:
            # it must remain horizontal even before the stylesheet has loaded.
            st.markdown('<div class="pair-grid" style="display:flex;flex-direction:row;flex-wrap:nowrap;'
                        'align-items:stretch;direction:ltr;gap:var(--gap,12px);'
                        'padding-block:var(--question-space,56px) var(--comparison-space,20px)">'
                        + ''.join(cards) + '</div>', unsafe_allow_html=True)
            # A native non-wrapping row keeps Gauche under the left image and
            # Droite under the right image, including narrow phone screens.
            with st.container(key="vote_controls", horizontal=True, wrap=False, gap="small"):
                for label, side in CHOICES.items():
                    st.button(t(side), width="stretch", key=f"vote_{side}_{batch.batch_id}_{batch.index}",
                              on_click=cast_vote, args=(person, side, batch.batch_id, batch.index, version))
        with st.container(key="evaluation_footer"):
            st.button(t("back"), type="tertiary", disabled=batch.index == 0, key="back",
                      on_click=previous_comparison, args=(person,))
    if batch.index + 1 < size:
        upcoming = batch.comparisons[batch.index + 1]
        for asset in (upcoming.left, upcoming.right):
            try:
                blinded_image(asset.path, asset.sha256)
            except (OSError, ValueError):
                pass


def cast_vote(person: Participant, side: str, batch_id: str, index: int, version: str):
    batch = person.batch
    if person.phase != "evaluation" or batch is None or batch.batch_id != batch_id or batch.index != index:
        return
    person.answer(side)
    if index == len(batch.comparisons) - 1:
        person.prepare_submission(version)
    else:
        batch.index += 1


def previous_comparison(person: Participant):
    if person.phase == "evaluation" and person.batch and person.batch.index > 0:
        person.batch.index -= 1


def submit(person, store):
    with st.container(key="saving_actions"):
        status = st.empty()
        status.markdown('<div class="saving-copy" role="status"><span class="saving-dot"></span>'
                        f'<h1>{t("saving")}</h1></div>', unsafe_allow_html=True)
        try:
            receipt = store.save_batch(person.pending_submission)
            person.confirm_submission(receipt)
            st.rerun()
        except StorageError:
            status.markdown(f'<div class="saving-copy"><h1>{t("connection_error")}</h1></div>', unsafe_allow_html=True)
            st.markdown(f'<p class="small-note">{t("votes_retained", size=len(person.batch.comparisons))}</p>', unsafe_allow_html=True)
            if st.button(t("retry"), type="primary", width="stretch", key="retry"):
                st.rerun()


def questionnaire(person: Participant):
    # Native button groups keep keyboard selection and rerun as soon as an answer changes.
    with st.container(key="questionnaire_actions"):
        st.markdown(f'<div class="questionnaire-copy"><h1>{t("questionnaire_title")}</h1>'
                    f'<p>{t("questionnaire_intro")}</p></div>', unsafe_allow_html=True)
        labels = {True: t("yes"), False: t("no")}
        artistic = st.pills(t("is_artistic"), options=[True, False], default=None, required=True,
                            format_func=labels.get, width="stretch", key="profile_artistic")
        knows = st.pills(t("knows_pixel_art"), options=[True, False], default=None, required=True,
                         format_func=labels.get, width="stretch", key="profile_knows_pixel_art")
        sensitive = None
        if knows is True:
            # A single inner block lets CSS unfold its actual height without a fixed limit.
            with st.container(key="questionnaire_followup"):
                with st.container(key="questionnaire_followup_content"):
                    sensitive = st.pills(t("sensitive_to_pixel_art"), options=[True, False],
                                         default=None, required=True, format_func=labels.get,
                                         width="stretch", key="profile_sensitive_to_pixel_art")
        complete = artistic is not None and knows is not None and (knows is False or sensitive is not None)
        if st.button(t("save_questionnaire"), type="primary", width="stretch",
                     disabled=not complete, key="save_questionnaire"):
            person.prepare_user_submission(artistic, knows, sensitive)
            st.rerun()


def submit_user(person: Participant, store):
    with st.container(key="saving_actions"):
        status = st.empty()
        status.markdown(f'<div class="saving-copy" role="status"><h1>{t("saving")}</h1></div>', unsafe_allow_html=True)
        try:
            receipt = store.save_user(person.pending_user_submission)
            person.confirm_user_submission(receipt)
            st.rerun()
        except StorageError:
            status.markdown(f'<div class="saving-copy"><h1>{t("connection_error")}</h1></div>', unsafe_allow_html=True)
            st.markdown(f'<p class="small-note">{t("questionnaire_retained")}</p>', unsafe_allow_html=True)
            if st.button(t("retry"), type="primary", width="stretch", key="retry_user"):
                st.rerun()


def wait_for_milestone(person: Participant):
    """Release any timer left in an active session by the previous UI."""
    person.celebration_ready_at = None


def milestone_animation(person: Participant):
    """Celebrate each saved batch once, without delaying either action."""
    wait_for_milestone(person)
    if person.celebrated_batch != person.batch.batch_id:
        st.balloons()
        person.celebrated_batch = person.batch.batch_id


def milestone(person: Participant, pool):
    milestone_animation(person)
    with st.container(key="milestone_actions"):
        st.markdown(f'<div class="milestone-copy"><span class="done-mark" aria-hidden="true">✓</span>'
                    f'<h1>{t("batch_completed", number=person.batch.number)}</h1></div>', unsafe_allow_html=True)
        if st.button(t("continue"), type="primary", width="stretch", key="continue"):
            person.start_batch(pool)
            st.rerun()
        if st.button(t("finish"), type="tertiary", width="stretch", key="finish"):
            person.finish()
            st.rerun()
    stats_footer(person, pool)


def finished(person, pool):
    earned = earned_achievements(person.completed)
    rank = ""
    if earned:
        _, name, icon = earned[-1]
        rank = (f'<div class="final-rank"><span class="final-rank-icon" aria-hidden="true">{icon}</span>'
                f'<div><span class="final-rank-label">{t("earned_rank")}</span>'
                f'<strong>{name}</strong></div></div>')
    with st.container(key="finished_actions"):
        st.markdown(f'<section class="finished-screen"><h1>{t("thanks")}</h1>{rank}'
                    f'<p class="final-score"><strong>{person.completed}</strong>'
                    f'<span>{t("pixel_comparisons")}</span></p>'
                    f'<p class="final-invitation">{t("share_invitation")}</p></section>', unsafe_allow_html=True)
        render_score_share(person.completed, st.session_state.language)
        if st.button(t("continue"), type="secondary", width="stretch", key="resume"):
            person.start_batch(pool)
            st.rerun()
    stats_footer(person)


def main():
    if st.session_state.get("language") not in LANGUAGES:
        st.session_state.language = "fr"
    styles()
    preview = os.environ.get("PIXEL_STUDY_PREVIEW") == "1"
    brand(preview)
    if "person" not in st.session_state:
        st.session_state.person = Participant()
    person = st.session_state.person
    if not isinstance(person, Participant):
        # Hot reload can retain an instance of the previous module's class.
        # Preserve its ID, votes and active batch while upgrading its methods.
        person = Participant(**{key: value for key, value in vars(person).items()
                                if key in Participant.__dataclass_fields__})
        st.session_state.person = person
    # Keep an existing active session usable after this UI update.
    if not hasattr(person, "celebration_ready_at"):
        person.celebration_ready_at = None
    if "pool" not in st.session_state:
        try:
            manifest = ROOT / "image_manifest.csv"
            pool, version = dataset(manifest.stat().st_mtime_ns)
            st.session_state.pool = pool
            st.session_state.dataset_version = version
        except (OSError, DatasetError, PoolTooSmall):
            welcome(person, (), False, t("study_preparing"))
            return
    if "store" not in st.session_state:
        if preview:
            st.session_state.store = PreviewStore()
        else:
            try:
                st.session_state.store = create_store(st.secrets)
            except (StreamlitSecretNotFoundError, KeyError, StorageError):
                welcome(person, st.session_state.pool, False, t("study_closed"))
                return
    pool = st.session_state.pool
    # Supabase and local previews collect the profile once, after the first receipt.
    # Older sessions already beyond their first batch keep their current flow.
    if (person.phase == "milestone" and person.batch.number == 1
            and person.user_answers is None and hasattr(st.session_state.store, "save_user")):
        person.begin_questionnaire()
    if person.phase == "welcome":
        welcome(person, pool, True)
    elif person.phase == "evaluation":
        evaluate(person, st.session_state.dataset_version)
    elif person.phase == "submitting":
        submit(person, st.session_state.store)
    elif person.phase == "questionnaire":
        questionnaire(person)
    elif person.phase == "submitting_user":
        submit_user(person, st.session_state.store)
    elif person.phase == "milestone":
        milestone(person, pool)
    elif person.phase == "finished":
        finished(person, pool)


if __name__ == "__main__":
    main()
