# Pixel Art Preference Study

A Streamlit app for anonymous, blinded pixel-art comparisons, designed first for
phones and also for desktop browsers. Participants submit **exactly 10 votes per
batch**, then freely choose another batch or Finish. There is no batch limit.
Results can be stored directly as private **CSV files in Google Drive**, one file
per completed batch. Supabase remains an optional alternative.

**Google Drive setup guide (French): [google_drive/SETUP_FR.md](google_drive/SETUP_FR.md).**

## Run locally

Use Python 3.11 or newer (tested with 3.13).

```bash
pip install -r requirements.txt
streamlit run app.py
```

For a working preview without database credentials, explicitly enable preview mode:

```powershell
# Windows PowerShell
$env:PIXEL_STUDY_PREVIEW = "1"
streamlit run app.py
```

```bash
# macOS / Linux
PIXEL_STUDY_PREVIEW=1 streamlit run app.py
```

Preview mode displays a persistent banner and holds responses only in session
memory. It never writes research votes. For live collection, unset this variable
and complete one storage setup below. Live mode never silently falls back to
local files or preview storage.

## Image matching

The existing repository has 2,400 PNGs:

```text
images/{AOP,APIP,FLUX,GPTImage,MYOS,PAP,PIA,SDPAP}/{48,64,128}/{0..99}.png
```

The dataset owner confirmed on 2026-10-18 that equal filename stems represent
the same original prompt across all methods and resolutions. The included
`image_manifest.csv` records that confirmed mapping and each file's SHA-256.
This yields **2,100 distinct comparisons**: 100 prompts × 3 target resolutions ×
7 competitors. Each comparison contains one SDPAP image and one competitor at
the same prompt and target resolution. The original images are never changed.

Target resolution comes from the folder, not the PNG dimensions. Most PNGs have
already been enlarged to 512 × 512; the inspected SDPAP/48 images are 384 × 384.
The app uses the same display canvas for both sides and nearest-neighbor scaling
in Python plus pixelated browser rendering. It does not apply image enhancement.
Images are re-encoded without metadata and sent without method names or paths.

No original prompt text was included in the repository, so the app omits that
panel. To display it, fill `prompt` for each `prompt_id` in the manifest. One row
per ID is sufficient; all nonempty text for the same ID must agree. Do not invent
prompts from the images. Freeze and archive the manifest during data collection.

Manifest columns:

| Column | Meaning |
| --- | --- |
| `method` | Exact directory/method identifier; visible only to the server/admin |
| `resolution` | Target resolution, such as 48, 64, or 128 |
| `prompt_id` | Verified shared original prompt identifier |
| `image_id` | Source image identifier |
| `prompt` | Original text, or blank when unavailable |
| `path` | Relative path inside `images/` |
| `verified` | Only `true` rows can enter the study |
| `sha256` | Source content hash; changed files fail validation |

For a changed dataset, build a **new** manifest (the script refuses to overwrite
curated files):

```bash
python tools/build_manifest.py --output image_manifest.new.csv
```

The default output contains unverified candidates. Correct their shared
`prompt_id`, verify that each match comes from the same source prompt, then set
`verified=true`. If you have independently confirmed the same numeric filename
convention, use `--confirm-filename-matches`. Optionally add
`--prompts prompts.csv`, where that CSV has `prompt_id,prompt` columns. Replace
the app's manifest with the reviewed file before launching a new study version.
Missing competitor images are excluded rather than incorrectly matched.
Duplicate keys, conflicting text, paths outside `images/`, and changed hashes
fail validation. Fewer than 10 verified pairs prevents starting a partial batch.

## Persistent storage: Google Drive CSVs

Follow [the setup guide](google_drive/SETUP_FR.md) to create a private folder,
paste [Code.gs](google_drive/Code.gs) into a Google Apps Script project, and deploy
it as an owner-executed web app. Requests from the Streamlit server carry an HMAC
signature; participants never sign into Google or receive storage credentials.
The folder and CSV files remain private under their existing Drive permissions.

```toml
[storage]
backend = "google_drive"

[google_drive]
web_app_url = "https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec"
shared_secret = "YOUR_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS"
```

The receiver requires exactly 10 votes, checks their identifiers and winners,
then creates one complete CSV while holding a script lock. It never appends to
a global CSV. A retry compares the entire existing file against the same frozen
submission and returns a matching receipt. A conflicting submission is rejected.
Every CSV includes the sampling metadata and a submission fingerprint.

This mode uses the existing Python dependencies. No Supabase project, Google
service account, or Google SDK installation is needed. Real writes require the
owner to authorize/deploy the script and configure Streamlit secrets. Neither
the local test suite nor preview mode writes files into a real Google Drive.
See [Apps Script web apps](https://developers.google.com/apps-script/guides/web)
and [service quotas](https://developers.google.com/apps-script/guides/services/quotas).

Keep the collection folder intact during active sessions. Download copies for
analysis; don't move or edit the receipt files that the retry checks depend on.

## Alternative persistent storage: Supabase

The app uses a server-side REST call and a PostgreSQL function. No browser-side
Supabase SDK, participant account, or spreadsheet-sharing setup is needed.

1. Create a Supabase project in your own account. Choose a plan suitable for your
   study; consult [current plans](https://supabase.com/pricing) for limits.
2. Open the project's SQL editor and run the entire [schema.sql](schema.sql).
3. Obtain the HTTPS project URL and a **secret key** (`sb_secret_...`) in project
   settings. See [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys).
4. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` locally and
   replace both placeholders. This file is ignored by Git. Never put the secret
   in source, browser code, query strings, or the manifest.
5. Disable preview mode, restart the app, and submit one test batch in a separate
   test project. Confirm one `pixel_batches` row and 10 `pixel_votes` rows.

```toml
[supabase]
url = "https://YOUR_PROJECT.supabase.co"
secret_key = "sb_secret_REPLACE_ME"
```

When choosing this alternative in the new secrets template, set
`storage.backend = "supabase"`. Existing secrets containing only `[supabase]`
continue to select Supabase automatically.

The secret remains in Streamlit's Python process. Current secret keys are sent
using `apikey`; legacy `service_role` JWTs are also supported for existing setups.
Publishable/anon keys are deliberately insufficient. Both tables have RLS
enabled, browser roles have no table permissions, and the RPC is executable only
by `service_role`. The function uses `SECURITY INVOKER`. No public view exposes
research results. See [database functions](https://supabase.com/docs/guides/database/functions).

**One batch is one transaction.** The RPC validates exactly 10 ordered votes,
shared identifiers, sequential batches, valid winners, and repeat metadata. It
inserts the batch header and all votes together or rolls back everything. A
participant lock serializes simultaneous submissions. Reusing the same batch ID
with the identical payload returns the original receipt; a changed payload is
rejected. If a network response is lost after commit, Retry safely uses the
original frozen payload. The milestone appears only after confirmation.

If saving fails, all current responses remain in the active session, and previous
batches stay saved. Local CSV files are never used for persistence.

## Sampling and reproducibility

Each batch has its own random ID and seed. The seeded sampler selects 10 distinct
pairs. **SDPAP images are drawn without replacement across the entire participant
session**, including successive batches: all 300 distinct source images appear
before any of them is reused. Image identity is based on content SHA-256, so
identical files with different names do not circumvent this rule. Every batch
also contains 10 distinct source images, including at cycle boundaries.

The sampler first prioritizes the least-exposed reference images, then unseen
competitor pairs for those images. It then balances competitors within/across
batches, resolution within/across batches, prompt within/across batches, and
finally the competitor/resolution stratum. Ties are broken randomly.
Coverage accumulates across batches; sparse or exhausted strata use the best
available balance. The full dataset supports method counts differing by at most
one and resolution counts differing by at most one, both within each batch and
cumulatively, in the tested sequences. Each tested full-pool batch uses all seven
competitors, all three resolutions, and 10 distinct prompts.
This is participant-level balancing, not a centrally allocated quota across all
participants. Independent participants receive independent random seeds.

Each pair receives an independent fair left/right flip. A pair ID is independent
of that flip, so swapping sides does not count as an unseen comparison. The
selected pairs and positions remain fixed through Streamlit reruns and Back.
No vote option is selected by default.

With the full, balanced repository, the first 300 comparisons use all 300 SDPAP
images once. Subsequent source-image cycles use previously unseen competitors
for each image, covering all 2,100 pairs before any exact pair repeats. After
that, a new pair cycle can start. A batch crossing a source-image cycle boundary
still contains 10 different source images. Fewer than 10 distinct source images
prevents starting a batch, even if many competitor pairs exist.

If a custom manifest has unequal competitor availability, a source image's
competitor pool can exhaust earlier than other images' pools. In that case,
the global source-image rule takes priority and the least-used eligible pairing
is revisited. Any repeated pair has `is_repeat=true`, `pool_cycle>1` (its appearance
number), and `repeat_reason=pool_exhausted`, so an analyst can exclude it.
The sampler version `balanced-reference-without-replacement-v2` records this
protocol. The sampler imposes no participation cap.

Each vote records the requested `participant_id`, `batch_id`, `batch_number`, and
`comparison_number`. `comparison_number` is the participant's lifetime ordinal;
`batch_comparison_number` runs from 1 to 10. Other fields include pair ID, original
prompt ID/text, target resolution, left/right methods, image IDs/paths/hashes,
selected side, winning method, UTC answer/submission timestamps, pool
cycle, and repeat flag. Dataset digest, sampling seed, and sampler version are
copied into every vote for self-contained CSV analysis. The batch table also
keeps the immutable request for retry checks and auditability.

The anonymous UUID stays the same across every batch in the **same active
Streamlit session**. Back and reruns preserve current choices. A hard browser
reload, expired session, new tab, or server restart may create a new participant
and discard an unfinished batch. Already submitted batches stay in the selected
persistent storage backend.
No login, tracking cookie, email, name, or cross-device recovery is implemented.

## Participation milestones

| Completed comparisons | Achievement |
| --- | --- |
| 10 | Pixel Explorer |
| 30 | Pixel Enthusiast |
| 50 | Pixel Expert |
| 100 | Pixel Master |
| 200 | Pixel Legend |

The participant interface is in French and uses one viewport without page
scrolling. A short question, two identical image frames, and **Gauche / Droite**
are the main content. Batch progress stays at the top; Back and the accumulated
total are discreetly placed at the bottom. Images resize to fit both the width
and available viewport height, including short landscape screens and phone safe
areas. Duplicate instructions, source labels, large metric cards, and decorative
celebrations have been removed. If original prompt text is available, it can be
opened on demand with **Voir le prompt**.

After the storage backend confirms a complete batch, a counter increases from
the previous total to the new total for **two seconds**. No voting, Continue,
Finish, or Back control is rendered during that period. A timer fragment checks
the gate without blocking the Python server; the animated counter lives outside
that fragment and is not redrawn on each timer tick. Once finished, the timer
is removed and the page shows a large **En faire 10 autres** button, followed by
**Terminer**. The compact statistics, earned achievement icons, newly unlocked
rank, and next achievement are at the bottom. The same batch never replays its
animation on a normal rerun. Failed storage writes stay on Retry and never show
a successful milestone prematurely.

CSS respects reduced motion: the same two-second gate remains, with static
final values in place of animation. Rewards depend only on completed participation;
the app never scores choices, speed, agreement, or other participants' preferences.
**A tap records the choice and immediately advances; there is no confirmation
step.** Every comparison requires a winner; no tie is offered or accepted by the
participant state machine. The tenth tap submits the full batch automatically. Previous comparison
allows a participant to revisit an earlier choice before that submission.

Normal comparisons rerun only a Streamlit fragment, keeping the surrounding page
stable. The next pair is warmed in the server image cache. Both images enter
together with a short 280 ms fade/slide. Normal voting has no waiting timer;
the two-second pause is used only after a successfully saved batch.
Reduced-motion settings disable the animation. Buttons have tactile press/focus
feedback, and delayed events belonging to the preceding pair are ignored.
The database remains compatible with legacy tie votes already recorded by the
previous protocol; this version never creates new ones.
There are no rankings or streaks.

## Export results

For Google Drive, download the batch CSVs into a local directory, then merge them:

```bash
python tools/merge_drive_csv.py --input ./downloaded-csvs --output results.csv
```

This retains exactly 10 rows per unique batch, removes identical downloaded
copies, rejects conflicting submissions, preserves original values/text, and
sorts results by participant and comparison number. It refuses to overwrite an
existing output. The individual Drive files already are complete CSV exports.

For Supabase, administrators can select `pixel_votes` in the table editor and use its CSV
export. Or export all rows with the included paginated command:

```bash
python tools/export_results.py --output results-2026-10-18.csv
```

The command reads the local secrets file, refuses to overwrite an existing
export, and fetches every page. `--secrets /path/to/secrets.toml` can select a
different credentials file. Keep exported files private; `results*.csv` is
ignored by Git. Participants never see an export or aggregate preferences.

## Deploy on Streamlit Community Cloud

1. Commit the app files, manifest, and existing `images/` directory to your GitHub
   repository. Keep `.streamlit/secrets.toml`, `.venv`, test tools, and exports out
   of Git. The correct dependency filename is **requirements.txt**; the original
   empty `requirement.txt` is not used.
2. Create an app from that repository in Streamlit Community Cloud. Since this
   directory is the Git repository root, select **app.py** as the entry point.
3. Select Python 3.13 in the deployment settings.
4. Paste the populated secrets TOML into the app's secrets settings. Never enable
   `PIXEL_STUDY_PREVIEW` for research collection.
5. Configure either the Google Drive receiver or the Supabase schema, check the
   image manifest, and deploy. Follow [Streamlit deployment instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app).
6. Verify a complete test submission against a test folder/database before sharing the
   live study URL, and check a phone browser as well as desktop.

The application does not collect personal information itself. Hosting providers
may retain operational logs under their own policies. Supply any institutionally
required consent/research information before recruiting participants.

## Validation

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover actual repository matching, seeded randomization, cumulative
balancing, non-repetition, non-multiple-of-10 pool sizes, 250-comparison sessions,
achievement thresholds, malformed manifests, complete Streamlit flows, Back,
Google Drive request signatures and receipts, CSV merging, and a lost storage
acknowledgement followed by a safe retry.

To test the actual Apps Script source against isolated Google API mocks (Node.js
required; no installation or Google account needed):

```bash
node tests/test_google_drive.mjs
```

This checks 10-vote CSVs, Unicode/newline/quote preservation, signed requests,
script locking, conflicting retries, and a lost reply after file creation. The
real Google authorization, deployment, permissions, and network connection must
still be checked with a test folder using the setup guide.

For the optional isolated PostgreSQL transaction tests (Node.js required):

```bash
npm install --prefix .test-tools --no-save --ignore-scripts @electric-sql/pglite@0.5.10
node tests/test_database.mjs
```

An optional final argument selects a Python interpreter. This test uses an
in-memory PostgreSQL engine, the real SQL schema, and application-generated
payloads. It checks rollback on the tenth vote, duplicate submissions, numbering,
pool exhaustion, and denied reads/RPC calls for browser roles. It does not contact
or modify a live Supabase project.
