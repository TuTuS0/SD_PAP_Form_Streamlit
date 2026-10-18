"""Server-only persistence for complete, idempotent batches."""

from __future__ import annotations

import copy
import csv
import hashlib
import hmac
import io
import json
import re
from urllib.parse import urlparse

import requests


class StorageError(RuntimeError):
    pass


class GoogleDriveCSVStore:
    """Send signed batches to the owner's Apps Script; one private CSV per batch.

    No Google credentials or upload requests reach the participant's browser.
    The receiver serializes writes and verifies the full CSV when retrying.
    """

    def __init__(self, web_app_url: str, shared_secret: str):
        if (not isinstance(web_app_url, str)
                or not re.fullmatch(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec", web_app_url)
                or "YOUR_" in web_app_url):
            raise StorageError("Configure the deployed Google Apps Script /exec URL.")
        if not isinstance(shared_secret, str) or len(shared_secret) < 32 or shared_secret.startswith("REPLACE"):
            raise StorageError("Configure a random Google Drive shared secret of at least 32 characters.")
        self._url = web_app_url
        self._secret = shared_secret

    def save_batch(self, payload: dict) -> str:
        try:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            signature = hmac.new(self._secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
            response = requests.post(self._url, json={"payload": canonical, "signature": signature},
                                     timeout=(5, 50), allow_redirects=False)
            # ContentService returns a one-time Google-hosted URL. Follow with
            # GET only: never forward the signed request to a redirect target.
            if response.status_code in {301, 302, 303}:
                location = response.headers.get("Location", "")
                parsed = urlparse(location)
                if parsed.scheme != "https" or parsed.hostname != "script.googleusercontent.com" or parsed.username:
                    raise StorageError("Google Drive returned an unexpected redirect.")
                response = requests.get(location, timeout=(5, 30), allow_redirects=False)
            if response.status_code != 200:
                raise StorageError("Google Drive did not confirm the batch.")
            receipt = response.json()
            if (not isinstance(receipt, dict) or receipt.get("ok") is not True
                    or receipt.get("batch_id") != payload["p_batch"]["batch_id"]
                    or receipt.get("row_count") != 10 or receipt.get("submission_sha256") != digest):
                raise StorageError("Google Drive did not confirm the complete batch.")
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise StorageError("The batch could not be confirmed in Google Drive.") from exc
        return payload["p_batch"]["batch_id"]


def create_store(settings):
    """Select one backend explicitly; never fail over after a failed write."""
    backend = settings.get("storage", {}).get("backend")
    if backend is None:
        # Preserve existing installations with only a [supabase] section.
        backend = "supabase" if "supabase" in settings and "google_drive" not in settings else "google_drive"
    try:
        if backend == "google_drive":
            config = settings["google_drive"]
            return GoogleDriveCSVStore(config["web_app_url"], config["shared_secret"])
        if backend == "supabase":
            config = settings["supabase"]
            return SupabaseStore(config["url"], config["secret_key"])
    except (KeyError, TypeError) as exc:
        raise StorageError("Persistent vote storage is not configured.") from exc
    raise StorageError("Choose google_drive or supabase as the storage backend.")


class SupabaseStore:
    def __init__(self, url: str, secret_key: str):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
            raise StorageError("Use the HTTPS project URL from Supabase settings.")
        if not (secret_key.startswith("sb_secret_") or secret_key.startswith("eyJ")):
            raise StorageError("Configure a server-side Supabase secret key.")
        self._url = url.rstrip("/") + "/rest/v1"
        self._headers = {"apikey": secret_key, "Content-Type": "application/json"}
        # New sb_secret keys are not JWTs and must not be sent as Bearer tokens.
        if secret_key.startswith("eyJ"):
            self._headers["Authorization"] = f"Bearer {secret_key}"

    def save_batch(self, payload: dict) -> str:
        try:
            response = requests.post(self._url + "/rpc/submit_pixel_batch", headers=self._headers,
                                     json=payload, timeout=(5, 25), allow_redirects=False)
            response.raise_for_status()
            receipt = response.json()
        except (requests.RequestException, ValueError) as exc:
            # Never surface response bodies/headers/credentials to participants.
            raise StorageError("The batch could not be confirmed by the study database.") from exc
        if receipt != payload["p_batch"]["batch_id"]:
            raise StorageError("The database returned an unexpected submission receipt.")
        return receipt


class PreviewStore:
    """Explicit local preview only; never a fallback for failed live writes."""

    def __init__(self):
        self.submissions: dict[str, dict] = {}

    def save_batch(self, payload: dict) -> str:
        batch_id = payload["p_batch"]["batch_id"]
        previous = self.submissions.get(batch_id)
        if previous is not None and previous != payload:
            raise StorageError("This batch ID has already been used for another payload.")
        self.submissions[batch_id] = copy.deepcopy(payload)
        return batch_id


def export_csv(url: str, secret_key: str) -> str:
    """Paginate the private vote table for the administrator's offline analysis."""
    store = SupabaseStore(url, secret_key)
    output = io.StringIO(newline="")
    writer = None
    offset = 0
    while True:
        try:
            response = requests.get(store._url + "/pixel_votes", headers=store._headers,
                                    params={"select": "*", "order": "participant_id,batch_number,batch_comparison_number",
                                            "limit": 500, "offset": offset},
                                    timeout=(5, 25), allow_redirects=False)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("Unexpected export result")
        except (requests.RequestException, ValueError) as exc:
            raise StorageError("CSV export failed; check the server credentials and database.") from exc
        if not rows:
            break
        if writer is None:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
        writer.writerows(rows)
        offset += len(rows)
        # Continue until an empty page, also when a project has a smaller row cap.
    return output.getvalue()
