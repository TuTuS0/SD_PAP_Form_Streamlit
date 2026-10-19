# Pixel Art Preference Study

A Streamlit app for anonymous, blinded pixel-art comparisons, designed first for
phones and also for desktop browsers. Participants submit **exactly 15 votes per
batch**, then freely choose another batch or Finish. There is no batch limit.
Every batch follows **48 → 64 → 128**, repeated five times, with five comparisons
at each resolution.
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

Preview mode holds responses only in session memory. It never writes research
votes. For live collection, unset this variable
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
fail validation. A full batch requires five distinct reference images at each
of 48, 64 and 128, with 15 distinct reference hashes across the batch. Insufficient
coverage prevents starting a partial or incorrectly ordered batch.

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

The receiver requires exactly 15 votes under the new sampler, checks resolution
order, identifiers and winners,
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
   test project. Confirm one `pixel_batches` row and 15 `pixel_votes` rows.

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
Publishable/anon keys are deliberately insufficient. All three tables have RLS
enabled, browser roles have no table permissions, and the RPC is executable only
by `service_role`. The function uses `SECURITY INVOKER`. No public view exposes
research results. See [database functions](https://supabase.com/docs/guides/database/functions).

**One batch is one transaction.** The RPC validates exactly 15 ordered votes,
shared identifiers, sequential batches, valid winners, and repeat metadata. It
inserts the batch header and all votes together or rolls back everything. A
participant lock serializes simultaneous submissions. Reusing the same batch ID
with the identical payload returns the original receipt; a changed payload is
rejected. If a network response is lost after commit, Retry safely uses the
original frozen payload. The milestone appears only after confirmation.

If saving fails, all current responses remain in the active session, and previous
batches stay saved. Local CSV files are never used for persistence.

### First-batch questionnaire and user table (Supabase)

After the first batch has been saved, a short questionnaire asks whether the
participant considers themselves artistic and knows what pixel art is. Both
questions require an explicit Yes/No. Answering Yes to the second immediately
reveals a third Yes/No question: whether they are sensitive to pixel art.
Participants save these answers before continuing or finishing. The questionnaire
never appears after later batches, including when resuming from the final screen.
The same flow is available in local preview mode.

Run the complete updated [schema.sql](schema.sql) in the SQL editor of the
**same Supabase project as the app** before using this version. It preserves
existing batches and votes, creates `public."user"`, and links batches to users
through `participant_id`. The table has one row per anonymous participant:

| Column | Meaning |
| --- | --- |
| `participant_id` | Same anonymous UUID as in `pixel_batches` and `pixel_votes` |
| `is_artistic` | Considers themselves artistic: `true` / `false` |
| `knows_pixel_art` | Knows what pixel art is: `true` / `false` |
| `sensitive_to_pixel_art` | `true` / `false` if known; `NULL` otherwise |
| `questionnaire_answered_at` | When the questionnaire was answered |
| `created_at` | When the user row was created |

Rows are created with the first saved batch. Earlier participants are backfilled
with unanswered fields set to `NULL`; no answers are inferred. A separate private
`submit_pixel_user` RPC validates and saves the questionnaire once. A lost reply
can be retried with the frozen payload without changing the answers or resubmitting
votes. User rows can be exported from the Supabase table editor and joined to votes
by `participant_id`. Browser roles cannot read or write these rows.

## Sampling and reproducibility

Each batch has its own random ID and seed. The seeded sampler selects 15 distinct
pairs in the exact order **48, 64, 128, 48, 64, 128, …**, five times. Both images
in a comparison have the same target resolution. **SDPAP images are drawn without
replacement across the entire participant session**, including successive batches:
with the balanced repository, all 300 distinct source images appear before any
is reused. Image identity is based on content SHA-256, so identical files with
different names do not circumvent this rule. Every batch contains 15 distinct
source images, including at cycle boundaries.

For the required resolution, the sampler prioritizes the least-exposed reference
images, then unseen competitor pairs. It balances competitors and prompts within
and across batches, then the competitor/resolution stratum. Ties are broken
randomly. Resolution counts are exactly equal in every batch. The full dataset
supports method counts differing by at most one within and across batches in the
tested sequences; each tested batch uses all seven competitors and 15 distinct
prompts. Feasibility checks also handle hashes shared between resolutions without
stranding later slots.
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
still contains 15 different source images. Insufficient distinct images at a
required resolution prevents starting a batch, even if many other pairs exist.

If a custom manifest has unequal resolution coverage, a resolution's reference
pool can exhaust before the other resolutions. Only that exhausted resolution
reuses images in later batches; other strata continue without replacement.
Unequal competitor availability can likewise exhaust a reference's eligible
pairs earlier. Any repeated pair has `is_repeat=true`, `pool_cycle>1` (its appearance
number), and `repeat_reason=pool_exhausted`, so an analyst can exclude it.
The sampler version `balanced-reference-resolution-cycle-v3` records this
protocol. The sampler imposes no participation cap.

Each vote records the requested `participant_id`, `batch_id`, `batch_number`, and
`comparison_number`. `comparison_number` is the participant's lifetime ordinal;
`batch_comparison_number` runs from 1 to 15. Batch submissions also record
`batch_size=15`. Other fields include pair ID, original
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

When upgrading an existing installation, redeploy the updated `google_drive/Code.gs`
to the same Apps Script deployment, or rerun `schema.sql` for Supabase. Existing
10-vote batches remain valid and are never rewritten. An already active 10-vote
batch can finish with its original protocol; the next batch uses 15 and the new
resolution cycle, retaining the participant ID and continuous comparison numbers.
CSV merging accepts both protocols and checks numbering between adjacent batches.

## Participation milestones

| Completed comparisons | Achievement |
| --- | --- |
| 10 | Pixel Explorer |
| 30 | Pixel Enthusiast |
| 50 | Pixel Expert |
| 100 | Pixel Master |
| 200 | Pixel Legend |

The welcome screen has a **Français / English** selector at the bottom (French
by default). The selected language persists throughout the session and applies
to all interface copy, including milestones, statistics, and error/retry messages.
Achievement names and original research prompts retain their original wording.
The selector changes neither the participant ID nor votes or sampling.

The participant interface uses one viewport without page scrolling.
A short question, two identical image frames, and **Gauche / Droite**
are the main content. The single-line question has no branding above it.
**Image 4/15** and **Lot 3** sit above a contrasting progress bar; Back is discreetly
placed at the bottom. Images stay side by side with a visible gap, and the two
buttons align underneath them. The title has a generous gap above the images,
with a smaller gap before the vote buttons. Images resize to fit both the width
and available viewport height, including short landscape screens and phone safe
areas. Duplicate instructions, source labels, and large metric cards have been
removed. If original prompt text is available, it can be
opened on demand with **Voir le prompt**.

After the storage backend confirms a complete batch (and, on the first Supabase
batch, after the questionnaire has been saved), the page immediately shows
a large **En faire 15 autres** button, followed by a smaller **Terminer** control.
Streamlit balloons celebrate the saved batch without blocking either action.
The compact statistics, earned achievement icons, newly unlocked rank, and next
achievement are at the bottom. Balloons play once per batch, including across
normal reruns. Failed storage writes stay on Retry and never show
a successful milestone prematurely.

CSS respects reduced motion by disabling transitions and hiding balloons.
Rewards depend only on completed participation;
the app never scores choices, speed, agreement, or other participants' preferences.
**A tap records the choice and immediately advances; there is no confirmation
step.** Every comparison requires a winner; no tie is offered or accepted by the
participant state machine. The fifteenth tap submits the full batch automatically. Previous comparison
allows a participant to revisit an earlier choice before that submission.

Normal comparisons rerun only a Streamlit fragment, keeping the surrounding page
stable. The next pair is warmed in the server image cache. Both images enter
together with a short 280 ms fade/slide. Neither voting nor milestones have an
artificial waiting timer.
Reduced-motion settings disable the animation. Buttons have tactile press/focus
feedback, and delayed events belonging to the preceding pair are ignored.
The database remains compatible with legacy tie votes already recorded by the
previous protocol; this version never creates new ones.
There are no rankings or streaks.

After **Finish**, a large participation count and **Share my score** action
appear with a ready-made invitation in the selected language. The action opens
the device's native share sheet to choose a messaging app. Without that API, the
main action opens the Messages/SMS composer. Direct **Messages / SMS** and
**WhatsApp** links are also available with the score, invitation, and clean study
link prefilled; the participant chooses the contact and confirms sending in the
selected app. Native sharing and message-prefilling support vary by browser,
device, and installed apps. Copying is a separate, discreet action. If clipboard
permission is denied, a compact dialog provides selectable message text.
Cancelling a share sheet does not trigger copying. Nothing is sent
automatically, and sharing never affects votes, achievements, or saved results.
Only the completed comparison count is shared, without participant identifiers,
image choices, or methods. Each comparison is a pair of pixel arts.
An **En faire 15 autres / Do 15 more** button on the final screen resumes the
same participation with a new batch, preserving the anonymous ID, completed
score, submitted batches, language, and image history. It keeps the 48/64/128
sequence and the existing rules against repeated reference images.

The share link uses the app's current URL without query parameters or fragments.
Optionally set `PIXEL_STUDY_PUBLIC_URL=https://your-study.streamlit.app/` for a
canonical public link, for example when testing behind a proxy or embedding the
app. Clipboard access depends on the browser's capabilities and permissions;
the manual-copy dialog is the fallback when copying is blocked.

## Export results

For Google Drive, download the batch CSVs into a local directory, then merge them:

```bash
python tools/merge_drive_csv.py --input ./downloaded-csvs --output results.csv
```

This retains exactly 15 rows per new batch (10 for legacy batches), removes identical downloaded
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
balancing, exact resolution cycles, non-repetition, non-multiple-of-15 pool sizes,
375-comparison sessions and upgrades from legacy batches,
achievement thresholds, malformed manifests, complete Streamlit flows, Back,
Google Drive request signatures and receipts, CSV merging, and a lost storage
acknowledgement followed by a safe retry.

To test the client-side score sharing and clipboard fallbacks without posting
anything (Node.js required, no installation needed):

```bash
node --test tests/test_score_share.mjs
```

This covers native sharing, cancellation, clipboard permissions, manual copying,
and removal of private URL parameters. Check the final layout and native share
sheet on real target devices before launching the study.

To test the actual Apps Script source against isolated Google API mocks (Node.js
required; no installation or Google account needed):

```bash
node tests/test_google_drive.mjs
```

This checks 15-vote CSVs, resolution order, legacy batches, Unicode/newline/quote preservation, signed requests,
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
payloads. It checks rollback on the fifteenth vote, duplicate submissions, numbering,
pool exhaustion, and denied reads/RPC calls for browser roles. It does not contact
or modify a live Supabase project.
