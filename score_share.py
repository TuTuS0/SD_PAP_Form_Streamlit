"""User-initiated sharing; only the completed count and invitation reach the client."""
from __future__ import annotations

import os
from pathlib import Path

from streamlit.components.v2 import component

from i18n import translate
from study import BATCH_SIZE

ASSETS = Path(__file__).resolve().parent / "share_assets"
_share_component = component(
    "pixel_score_share",
    html=(ASSETS / "share.html").read_text(encoding="utf-8"),
    css=(ASSETS / "share.css").read_text(encoding="utf-8"),
    js=(ASSETS / "share.mjs").read_text(encoding="utf-8"),
)


def render_score_share(completed: int, language: str):
    _share_component(
        key="score_share",
        data={
            "title": translate(language, "share_title"),
            "text": translate(language, "share_message", count=completed, size=BATCH_SIZE),
            "share_label": translate(language, "share_button"),
            "copy_label": translate(language, "share_copy"),
            "messages_label": translate(language, "share_messages"),
            "sms_label": translate(language, "share_sms"),
            "choose_app": translate(language, "share_choose_app"),
            "close_label": translate(language, "share_close"),
            "preview_label": translate(language, "share_preview"),
            "copied": translate(language, "share_copied"),
            "manual": translate(language, "share_manual"),
            "public_url": os.environ.get("PIXEL_STUDY_PUBLIC_URL", ""),
        },
    )
