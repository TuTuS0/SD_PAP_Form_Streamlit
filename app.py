"""Run with: streamlit run app.py"""
from __future__ import annotations

import base64
import html
import io
import os
import time
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps
from streamlit.errors import StreamlitSecretNotFoundError

from storage import PreviewStore, StorageError, create_store
from study import BATCH_SIZE, DatasetError, Participant, PoolTooSmall, earned_achievements, load_pool, next_achievement

ROOT = Path(__file__).resolve().parent
CHOICES = {"Gauche": "left", "Droite": "right"}
MILESTONE_SECONDS = 2.0

st.set_page_config(page_title="Pixel Art Preference Study", page_icon="✦", layout="centered", initial_sidebar_state="collapsed")


def styles():
    # CSS-only st.html occupies no space in the page.
    st.html(ROOT / "ui.css")


def brand(preview: bool):
    mode = '<span class="preview-label" title="Aperçu : aucune réponse n’est enregistrée">APERÇU</span>' if preview else ""
    st.markdown('<header class="app-header"><span class="brand"><span class="brand-mark" aria-hidden="true">'
                + '<i></i>' * 4 + '</span>Pixel Lab</span>' + mode + '</header>', unsafe_allow_html=True)


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
    badges = []
    for threshold, name, icon in earned:
        fresh = person.phase == "milestone" and person.completed - BATCH_SIZE < threshold <= person.completed
        title = f"{name} · {threshold} comparaisons" + (" · Nouveau" if fresh else "")
        badges.append(f'<span class="achievement{" new" if fresh else ""}" title="{title}" role="img" aria-label="{title}">{icon}</span>')
    latest = earned[-1][1] if earned else ""
    unlocked = any(person.completed - BATCH_SIZE < threshold <= person.completed for threshold, _, _ in earned)
    rank = ("Nouveau : " if unlocked and person.phase == "milestone" else "") + latest
    upcoming = next_achievement(person.completed)
    next_text = f"{upcoming[0] - person.completed} avant {upcoming[1]}" if upcoming else "Tous les succès débloqués"
    note = ""
    if pool and not ({p.reference.sha256 for p in pool} - {item.pair.reference.sha256 for item in person.history}):
        note = '<p class="pool-note">Les images pourront revenir dans de nouveaux duels.</p>'
    st.markdown(f'<footer class="stats-footer"><div class="totals"><span><strong>{person.completed}</strong> comparaisons</span>'
                f'<span class="separator">·</span><span><strong>{person.completed // BATCH_SIZE}</strong> lots</span></div>'
                f'<div class="earned">{"".join(badges)}<span>{rank}</span></div>'
                f'<p class="next-achievement">{next_text}</p>{note}</footer>', unsafe_allow_html=True)


def welcome(person, pool, ready, message=""):
    with st.container(key="welcome_actions"):
        st.markdown('<div class="welcome-copy"><p class="eyebrow">Pixel Art Preference Study</p>'
                    '<h1>Quel pixel art<br>préfères-tu ?</h1><p>Deux images. Ton choix. 10 fois.</p></div>', unsafe_allow_html=True)
        if st.button("Commencer", type="primary", width="stretch", disabled=not ready, key="start"):
            person.start_batch(pool)
            st.rerun()
        if message:
            st.markdown(f'<p class="small-note">{html.escape(message)}</p>', unsafe_allow_html=True)
    st.markdown('<footer class="welcome-footer">Anonyme · 2 à 4 minutes par lot<br>'
                'Enregistré par lots de 10. Tu peux terminer après chaque lot.</footer>', unsafe_allow_html=True)


@st.fragment
def evaluate(person: Participant, version: str):
    if person.phase != "evaluation":
        st.rerun()
    batch = person.batch
    item = batch.comparisons[batch.index]
    previous = batch.answers.get(batch.index)
    animation = f"comparison_enter_{batch.index}"
    st.markdown(f'<style>@keyframes {animation} {{from {{opacity:0;transform:translateY(8px)}}'
                f'to {{opacity:1;transform:translateY(0)}}}}'
                f'.st-key-evaluation_scene {{animation:{animation} 280ms ease both;}}</style>', unsafe_allow_html=True)
    with st.container(key="evaluation_screen"):
        answered = len(batch.answers)
        st.markdown(f'<div class="evaluation-top"><span>Lot {batch.number}</span><span>{batch.index + 1} / 10</span></div>'
                    f'<div class="batch-progress" role="progressbar" aria-label="Progression du lot {batch.number}" '
                    f'aria-valuemin="0" aria-valuemax="10" aria-valuenow="{answered}">'
                    f'<i style="width:{answered * 10}%"></i></div>', unsafe_allow_html=True)
        with st.container(key="evaluation_scene"):
            st.markdown('<h1 class="evaluation-question">Quel pixel art préfères-tu ?</h1>', unsafe_allow_html=True)
            if item.left.prompt:
                with st.popover("Voir le prompt", type="tertiary"):
                    st.write(item.left.prompt)
            try:
                images = [blinded_image(asset.path, asset.sha256) for asset in (item.left, item.right)]
            except (OSError, ValueError):
                st.markdown('<p class="small-note">Les images n’ont pas pu être chargées.</p>', unsafe_allow_html=True)
                if st.button("Réessayer", width="stretch", key="reload_images"):
                    st.rerun()
                return
            cards = []
            for label, side, image in zip(("Image de gauche", "Image de droite"), ("left", "right"), images):
                selected = previous and previous.side == side
                cards.append(f'<figure class="image-card{" is-selected" if selected else ""}" data-side="{side}" '
                             'style="flex:1 1 0%;min-width:0;width:0;margin:0;aspect-ratio:1;box-sizing:border-box">'
                             f'<img src="data:image/png;base64,{image}" alt="{label}" decoding="async" '
                             'style="display:block;width:100%;height:100%;object-fit:contain" /></figure>')
            # Keep the left/right geometry in the markup as well as the theme:
            # it must remain horizontal even before the stylesheet has loaded.
            st.markdown('<div class="pair-grid" style="display:flex;flex-direction:row;flex-wrap:nowrap;'
                        'align-items:stretch;direction:ltr">' + ''.join(cards) + '</div>', unsafe_allow_html=True)
            # A native non-wrapping row keeps Gauche under the left image and
            # Droite under the right image, including narrow phone screens.
            with st.container(key="vote_controls", horizontal=True, wrap=False, gap="small"):
                for label, side in CHOICES.items():
                    st.button(label, width="stretch", key=f"vote_{side}_{batch.batch_id}_{batch.index}",
                              on_click=cast_vote, args=(person, side, batch.batch_id, batch.index, version))
        with st.container(key="evaluation_footer"):
            back, total = st.columns(2)
            back.button("← Retour", type="tertiary", disabled=batch.index == 0, key="back",
                        on_click=previous_comparison, args=(person,))
            total.markdown(f'<div class="total-quiet">{person.total_evaluated} au total</div>', unsafe_allow_html=True)
    if batch.index + 1 < BATCH_SIZE:
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
    if index == BATCH_SIZE - 1:
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
                        '<h1>Enregistrement…</h1></div>', unsafe_allow_html=True)
        try:
            receipt = store.save_batch(person.pending_submission)
            person.confirm_submission(receipt)
            st.rerun()
        except StorageError:
            status.markdown('<div class="saving-copy"><h1>Connexion interrompue</h1></div>', unsafe_allow_html=True)
            st.markdown('<p class="small-note">Tes 10 choix sont conservés dans cette session.</p>', unsafe_allow_html=True)
            if st.button("Réessayer", type="primary", width="stretch", key="retry"):
                st.rerun()


@st.fragment(run_every=0.2)
def wait_for_milestone(person: Participant):
    """Poll only the gate; the animated counter is outside this fragment."""
    if person.phase != "milestone" or person.celebrated_batch == person.batch.batch_id:
        st.rerun()  # Ignore a timer event queued by a screen that has been left.
    if time.monotonic() >= person.celebration_ready_at:
        person.celebrated_batch = person.batch.batch_id
        person.celebration_ready_at = None
        st.rerun()


def milestone_animation(person: Participant):
    """No controls during the two-second counter animation; no server sleep."""
    if person.celebration_ready_at is None:
        person.celebration_ready_at = time.monotonic() + MILESTONE_SECONDS
    st.markdown(f'<section class="celebration-screen" role="status" aria-live="polite" aria-busy="true">'
                f'<p class="eyebrow">Lot {person.batch.number} terminé</p>'
                f'<div class="counting-total" style="--count-from:{person.completed - BATCH_SIZE};'
                f'--count-to:{person.completed}" aria-hidden="true"></div>'
                f'<span class="sr-only">{person.completed} comparaisons enregistrées</span>'
                '<p class="counter-label">comparaisons</p><div class="celebration-track"><i></i></div></section>', unsafe_allow_html=True)
    wait_for_milestone(person)


def milestone(person: Participant, pool):
    if person.celebrated_batch != person.batch.batch_id:
        milestone_animation(person)
        return
    with st.container(key="milestone_actions"):
        st.markdown(f'<div class="milestone-copy"><span class="done-mark" aria-hidden="true">✓</span>'
                    f'<h1>Lot {person.batch.number} terminé</h1></div>', unsafe_allow_html=True)
        if st.button("En faire 10 autres", type="primary", width="stretch", key="continue"):
            person.start_batch(pool)
            st.rerun()
        if st.button("Terminer", type="tertiary", width="stretch", key="finish"):
            person.finish()
            st.rerun()
    stats_footer(person, pool)


def finished(person):
    st.markdown('<section class="finished-screen"><span class="done-mark" aria-hidden="true">✓</span>'
                '<h1>Merci !</h1><p>Ton regard fait avancer la recherche.</p></section>', unsafe_allow_html=True)
    stats_footer(person)


def main():
    styles()
    preview = os.environ.get("PIXEL_STUDY_PREVIEW") == "1"
    brand(preview)
    if "person" not in st.session_state:
        st.session_state.person = Participant()
    person = st.session_state.person
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
            welcome(person, (), False, "L’étude est en préparation.")
            return
    if "store" not in st.session_state:
        if preview:
            st.session_state.store = PreviewStore()
        else:
            try:
                st.session_state.store = create_store(st.secrets)
            except (StreamlitSecretNotFoundError, KeyError, StorageError):
                welcome(person, st.session_state.pool, False, "Les participations ouvriront bientôt.")
                return
    pool = st.session_state.pool
    if person.phase == "welcome":
        welcome(person, pool, True)
    elif person.phase == "evaluation":
        evaluate(person, st.session_state.dataset_version)
    elif person.phase == "submitting":
        submit(person, st.session_state.store)
    elif person.phase == "milestone":
        milestone(person, pool)
    elif person.phase == "finished":
        finished(person)


if __name__ == "__main__":
    main()
