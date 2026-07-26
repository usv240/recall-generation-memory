# Recall

**Generate once. Reuse forever.**

Recall is a shared memory and spend-control layer for generative media. Before another model runs, Recall checks whether the team already has something that safely fits the request. A good match can be downloaded as the exact original from Backblaze B2 for zero new model cost. A genuinely new request continues through Genblaze and becomes reusable memory for the next person.

Imagine that your team created and approved the perfect campaign image last week. Today, someone describes the same need with different words. The original file, prompt, provider settings, approval, and cost are scattered across tools, so they generate again. The team pays twice and receives different pixels.

Recall changes that default.

## Try the live product

| Destination | Link |
| --- | --- |
| Product overview | [Open Recall](https://recall-production-production.up.railway.app/) |
| Working application | [Open the Recall workspace](https://recall-production-production.up.railway.app/app) |
| Service health | [Check health](https://recall-production-production.up.railway.app/api/health) |
| Production readiness | [Check B2 and provider readiness](https://recall-production-production.up.railway.app/api/ready) |
| API documentation | [Explore the REST API](https://recall-production-production.up.railway.app/docs) |
| Python package | [Install recall-relay from PyPI](https://pypi.org/project/recall-relay/) |

The hosted workspace is intentionally usable without an account. Paid public generations are limited by IP so judges can test the real provider path without exposing an unlimited model budget. Search, proof inspection, and exact B2 downloads remain available without another model call.

## The idea in one minute

Every request ends in one of three honest choices:

1. **Download exact** returns the original stored bytes from B2. No model runs.
2. **Create a tracked fork** makes a deliberate variation and preserves the parent relationship.
3. **Generate something new** pays for a new Genblaze run because the need is genuinely different.

Recall never silently substitutes an old asset. It recommends, explains, and leaves the final choice with the person making the request.

## Why this is more than a folder or a DAM

A folder stores a file after someone remembers to save it. A digital asset manager helps organize files after they exist. Both are useful, but neither normally sits in front of a generation request before the provider charges the team.

Recall connects the whole decision:

- what the person asked for
- which provider and model produced the media
- the exact B2 bytes and SHA-256 hash
- the Genblaze recipe and canonical manifest
- brand, campaign, format, license, and language intent
- parent and child variations
- approval and retention state
- why Recall recommended reuse or generation
- how much model cost was actually avoided

The result is active generation memory, not a passive gallery.

## A quick judge walkthrough

You can understand the core product without spending provider credits:

1. Open the [workspace](https://recall-production-production.up.railway.app/app).
2. Choose a library item and select **Proof**.
3. Confirm that the B2 asset hash is verified and inspect its Genblaze record and lineage.
4. Enter a request similar to an existing asset and select **Check library, then generate**.
5. Pause on the comparison between another model call and the exact B2 download at zero new model cost.
6. Select **Download exact original** and confirm that the original file downloads.
7. Watch **Saved by Recall** and **Paid calls avoided** update from a real reuse event.
8. Select **Fork**, edit the loaded prompt, and notice that the primary action becomes **Create tracked fork**. This is a new paid variation with a parent link, not a copy.
9. Inspect an approved item to see the B2 Object Lock retention state.

Exact download and recipe replay are intentionally different. Download returns bit-for-bit stored media. Replay is a new paid model run and may produce a different result when the provider is nondeterministic.

## Architecture

~~~mermaid
flowchart LR
    U[Creator or connected app] --> R[Recall Reuse Gate]
    R -->|Safe match| D[Exact B2 download]
    R -->|New need| G[Genblaze pipeline]
    G --> P[Generative media provider]
    P --> G
    G --> B[(Backblaze B2)]
    B --> D
    R --> C[Policy decision receipt]
    C --> B
    B --> L[Library, proof, lineage, savings]
    D --> U
    L --> U
~~~

There are two main paths:

- **Reuse path:** Recall searches memory, checks intent, records a verifiable decision receipt, and downloads the exact B2 object.
- **Generation path:** Genblaze orchestrates the provider call, Recall seals the output hash into the provenance record, and the asset plus its metadata become durable B2 memory.

Backblaze B2 is the system of record. Genblaze is the generation and provenance route. Recall is the decision layer that connects them to a human workflow.

## What we built

| Capability | What it means for a user | How it works |
| --- | --- | --- |
| Reuse Gate | Check before paying again | Exact, lexical, tag, and optional semantic matching run before generation |
| Intent Firewall | Similar does not automatically mean safe | Brand, campaign, format, license, and language conflicts block reuse |
| Exact download | Get the approved original, not a new approximation | Recall streams the stored B2 bytes with a useful filename; Proof re-hashes the asset with SHA-256 |
| Tracked forks | Change only what needs to change | A new Genblaze run keeps its parent generation and parent run lineage |
| Native image and video generation | Use memory for both fast images and costly clips | Google and GMI handle images; Genblaze's GMI video provider creates short clips that are archived directly to B2 |
| Automatic provider fallback | Finish safely when an image route fails | Recall tries the selected second provider and stores requested route, failure evidence, attempted providers, and the successful route |
| Honest recipe replay | Rerun saved settings without promising identical output | Replay is labeled as paid and best effort |
| Proof records | Inspect what happened and verify it | B2 hash, manifest verification, lineage, cost, and storage evidence are exposed together |
| Approval and retention | Protect a final creative decision | Approved media is copied to a B2 Object Lock retention path |
| Savings ledger | See whether reuse is producing value | Avoided cost uses the original recorded cost and increments only on explicit reuse |
| CSV economics export | Share evidence with finance or operations | A prompt-free, spreadsheet-safe ledger includes spend, reuse, savings, lineage, fallback, verification, and Object Lock status |
| Recall Ledger | Prove what Recall checked before generation | Chained receipts contain policy evidence and salted prompt commitments |
| Feedback calibration | Correct a bad suggestion | Wrong-match feedback blocks the same request and candidate pair in that workspace |
| Private Vaults | Keep teams and archives isolated | Each workspace receives a B2 prefix and a one-time workspace credential |
| Relay SDK | Put Recall in front of any provider | The provider callback stays in the user's process and only completed bytes are captured |
| External capture | Preserve work created outside Recall | Authenticated image, video, or audio bytes are signature-checked, hashed, and archived |
| Exact byte deduplication | Avoid storing identical external media twice | SHA-256 detects an existing asset before another B2 copy is written |
| Asynchronous jobs | Keep slow media generation observable | Jobs are queued, bounded, retryable, workspace-scoped, and persisted in B2 |
| Evidence export | Make the implementation inspectable | A machine-readable bundle includes integrity, lineage, recipe, and storage evidence |
| Light and dark themes | Make the workspace comfortable and accessible | Responsive UI, clear action language, keyboard-friendly controls, and theme persistence |

## How Recall decides whether something matches

Recall uses a layered and explainable process:

1. **Exact prompt match:** Normalized equality produces an exact score of 1.0.
2. **Lexical match:** Token overlap and shared labels catch close wording.
3. **Semantic match:** Optional Gemini embeddings identify paraphrases through cosine similarity.
4. **Intent check:** Even a strong semantic match is rejected when structured intent conflicts.
5. **Feedback check:** Previously rejected request and candidate pairs are not suggested again.
6. **Human choice:** Recall recommends an action. It never retrieves or generates without an explicit choice.

The live semantic threshold is conservative and near matches remain suggestions. This matters because a visually or linguistically similar asset can still be wrong for a brand, license, locale, or campaign.

## How Backblaze B2 is used

B2 is not a final upload destination added after the interesting work. It is Recall's durable economic memory.

Recall stores:

~~~text
recall/
  assets/{generation_id}/                  exact generated or captured media
  genblaze-manifests/{generation_id}.json  raw Genblaze provenance
  manifests/{generation_id}.json           readable generation recipe
  index/runs/{generation_id}.json          searchable catalog record
  index/jobs/{job_id}.json                  durable job state
  index/events/{event_id}.json              generation and reuse economics
  index/feedback/{feedback_id}.json         workspace calibration
  ledger/{receipt_id}.json                  chained reuse decisions
  approved/{generation_id}/                 Object Lock protected finals
  workspaces/{workspace_id}/...             isolated private vaults
~~~

B2 serves five distinct roles:

- durable storage for generated media
- a catalog and event store for a small production system
- provenance and recipe storage
- exact media delivery through signed or server-mediated downloads
- retention protection for approved outputs and decision receipts

The storage adapter paginates B2 listings, scopes workspace keys under fixed prefixes, verifies content hashes, and fails closed when Object Lock was requested but could not be configured.

## How Genblaze is used

Genblaze is responsible for more than calling a model:

- a Genblaze **Pipeline** orchestrates generation
- provider adapters connect Google Gemini and GMI Cloud image generation plus GMI Cloud video generation
- provider-specific fallback models remain within a route, while Recall can visibly continue an image job across Google and GMI when the selected route fails
- retries are recorded as part of the job path
- the raw Genblaze manifest is stored beside every orchestrated asset
- Recall fills output hash, byte size, and media type into the manifest when needed
- the canonical manifest hash is recomputed and verified
- parent run identifiers preserve lineage for forks and reruns
- the native Genblaze ObjectStorageSink path is verified end to end against private B2, with authenticated retrieval and workspace-scoped prefixes

The live deployment uses Google **gemini-3.1-flash-image**, GMI Cloud **gpt-image-2-generate**, and GMI Cloud **seedance-2-0-260128** for short video. Optional semantic search uses **gemini-embedding-001**. Provider and model names are stored with each generation instead of being hidden behind generic labels.

The public library also contains a real 5-second Seedance video proof: **gen_54827c05a1de**, 794,960 bytes, B2 SHA-256 **8c5262c480450571d9a9b8f129faa50992a6d7258a1d0ec54fc8e9eca1040200**, and a canonically verified Genblaze manifest. GMI did not return a trustworthy price for that run, so Recall leaves it unpriced.

## Recall Relay: use your own model and keep the memory

The [recall-relay](https://pypi.org/project/recall-relay/) package makes Recall useful outside the hosted workspace.

~~~bash
pip install recall-relay
recall-relay doctor
~~~

Relay checks a private Recall workspace before your application calls its provider. On a safe hit, the provider callback is never invoked. On a miss, your callback generates the media locally and Relay captures the completed result.

~~~python
from recall_relay import RecallRelay

relay = RecallRelay(
    "https://your-recall.example",
    workspace_id="ws-your-workspace",
    workspace_key="save-the-one-time-key",
)

result = relay.generate_with(
    "A cobalt launch image on warm ivory paper",
    generator=lambda prompt: call_your_provider(prompt),
    provider="your-provider",
    model="your-model",
    media_type="image/png",
    tags=["launch", "cobalt"],
    intent={"brand": "Acme", "format": "1:1"},
    cost_usd=0.42,
)

print(result.status)
~~~

Provider keys remain in the caller's process. Recall receives completed media only after a safe miss. Relay currently accepts PNG, JPEG, WebP, MP4, MP3, and WAV with server-side byte-signature validation.

See [Relay documentation](docs/RELAY.md) and the [Python package guide](sdk/python/README.md) for Gemini, OpenAI, CLI, and custom-provider examples.

## Run Recall locally

### Prerequisites

- Docker Desktop, or Python 3.12
- a Backblaze B2 bucket and bucket-restricted application key for full B2 behavior
- a Google or GMI Cloud API key for live image generation, or both for cross-provider routing

Recall can start with local storage when B2 credentials are absent. That is useful for interface development, but B2 readiness, signed delivery, and Object Lock require real B2 configuration.

### Option 1: Docker

Clone the repository and create your local environment file:

~~~bash
git clone https://github.com/usv240/recall-generation-memory.git
cd recall-generation-memory
cp .env.example .env
~~~

On PowerShell, use:

~~~powershell
git clone https://github.com/usv240/recall-generation-memory.git
Set-Location recall-generation-memory
Copy-Item .env.example .env
~~~

Edit **.env**, then start Recall:

~~~bash
docker compose up --build
~~~

Open http://127.0.0.1:8080/app.

### Option 2: Python

~~~bash
git clone https://github.com/usv240/recall-generation-memory.git
cd recall-generation-memory
python -m venv .venv
~~~

Activate the environment on macOS or Linux:

~~~bash
source .venv/bin/activate
~~~

Activate it on PowerShell:

~~~powershell
.\.venv\Scripts\Activate.ps1
~~~

Install and run:

~~~bash
python -m pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
~~~

Open http://127.0.0.1:8000/app.

## Configure the full end-to-end path

Start with [.env.example](.env.example). Never commit **.env**.

### 1. Backblaze B2

Create a bucket such as **recall-production**. Use a bucket-restricted application key with only the capabilities Recall needs. Restrict its filename prefix to **recall/** when possible.

~~~dotenv
B2_KEY_ID=your-restricted-key-id
B2_APP_KEY=your-application-key
B2_BUCKET=recall-production
B2_REGION=us-east-005
B2_LOCK_DAYS=30
~~~

If you want approval retention, enable Object Lock for the bucket and grant the restricted key the corresponding retention capabilities.

### 2. Generation provider

~~~dotenv
RECALL_PROVIDER=google
GOOGLE_API_KEY=your-google-api-key
GOOGLE_MODEL_IMAGE=gemini-3.1-flash-image
GMI_API_KEY=your-gmi-api-key
GMI_MODEL_IMAGE=gpt-image-2-generate
GMI_MODEL_VIDEO=seedance-2-0-260128
RECALL_MODEL=gemini-3.1-flash-image
RECALL_MODEL_COST_USD=0.067
RECALL_GOOGLE_MODEL_COST_USD=0.067
RECALL_GMI_MODEL_COST_USD=your-effective-gmi-image-cost
RECALL_GMI_VIDEO_COST_USD=your-effective-gmi-video-cost
RECALL_NATIVE_SINK=true
~~~

Configure either provider or both. Images can use Google or GMI and can continue on the other route after a recorded failure. Built-in video uses the official Genblaze GMI video adapter with a short 5-second, 480p default. The workspace shows only compatible routes, and every generation, fallback, rerun, or fork records the provider and model it actually used. Set the provider-specific cost variables to the effective price of each output tier. **RECALL_MODEL_COST_USD** remains the backward-compatible price for the default provider. Recall never borrows one provider's price for another. If no trustworthy cost is available, the output remains unpriced and does not create fictional savings.

### 3. Production controls

~~~dotenv
RECALL_API_KEYS=replace-with-a-long-integration-key
RECALL_CORS_ORIGINS=https://your-domain.example
RECALL_RECEIPT_SECRET=replace-with-a-long-random-secret
RECALL_ALLOW_PUBLIC_GENERATE=false
RECALL_PUBLIC_GENERATIONS_PER_HOUR=3
RECALL_PUBLIC_REUSE_CHECKS_PER_HOUR=120
~~~

For a public judging deployment, public generation can remain enabled with a small quota. For a private production deployment, disable it and distribute scoped integration or workspace credentials.

### 4. Verify readiness

~~~bash
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/ready
~~~

Health confirms that the process is alive. Readiness confirms that B2 is reachable and the generation provider is configured.

## Use the REST API

The full OpenAPI document is available at **/openapi.json**, with interactive documentation at **/docs**.

Set a base URL:

~~~bash
export RECALL_URL=https://recall-production-production.up.railway.app
~~~

### Check for reusable work

~~~bash
curl -X POST "$RECALL_URL/api/v1/reuse-check" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A cobalt product tile on an ivory desk",
    "provider": "google",
    "tags": ["hero", "launch"],
    "intent": {"brand": "Recall", "format": "1:1"}
  }'
~~~

The response recommends **reuse** or **generate**, explains blockers, returns ranked candidates, and includes a verifiable decision receipt.

### Queue a generation

~~~bash
curl -X POST "$RECALL_URL/api/v1/jobs/generate" \
  -H "Content-Type: application/json" \
  -H "X-Recall-Key: $RECALL_API_KEY" \
  -d '{
    "prompt": "A cobalt product tile on an ivory desk",
    "provider": "google",
    "tags": ["hero", "launch"],
    "intent": {"brand": "Recall", "format": "1:1"}
  }'
~~~

Poll the returned **poll** URL until the job is **completed** or **failed**. For image resilience, send **fallback_provider: gmi** with a Google request, or **fallback_provider: google** with a GMI request. The stored routing record shows both attempts if fallback fires.

### Queue a short video

~~~bash
curl -X POST "$RECALL_URL/api/v1/jobs/generate"   -H "Content-Type: application/json"   -H "X-Recall-Key: $RECALL_API_KEY"   -d '{
    "prompt": "A slow camera orbit around a cobalt product on an ivory table",
    "modality": "video",
    "provider": "gmi",
    "tags": ["video", "launch"]
  }'
~~~

Recall searches only existing videos before this call. A miss uses the configured GMI video model with a 5-second, 480p default, then stores the video, Genblaze manifest, hash, recipe, lineage, and economics in B2.

### Export the savings ledger

~~~bash
curl -OJ "$RECALL_URL/api/v1/exports/savings.csv"
~~~

The CSV excludes prompts and embedding vectors. It is spreadsheet-safe and includes cost, exact downloads, avoided cost, lineage, fallback, manifest verification, native B2 sink use, and Object Lock status.

### Download and account for an exact reuse

~~~bash
curl -X POST "$RECALL_URL/api/v1/gen/GENERATION_ID/reproduce"
curl -OJ "$RECALL_URL/api/v1/gen/GENERATION_ID/download"
~~~

The first request records the explicit reuse and avoided cost. The second returns the original media as an attachment.

### Verify an asset

~~~bash
curl "$RECALL_URL/api/v1/gen/GENERATION_ID/verify"
curl "$RECALL_URL/api/v1/gen/GENERATION_ID/evidence"
curl "$RECALL_URL/api/v1/gen/GENERATION_ID/lineage"
~~~

### Create a private workspace

~~~bash
curl -X POST "$RECALL_URL/api/v1/workspaces" \
  -H "X-Recall-Key: $RECALL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label": "My creative vault"}'
~~~

Save the returned workspace key immediately. Recall stores only its SHA-256 and never returns the raw key again. Private requests include both **X-Recall-Workspace** and **X-Recall-Workspace-Key**.

## Security and production behavior

Recall includes practical controls for a public generative-media application:

- bucket-restricted B2 credentials
- private workspace prefixes and hashed workspace keys
- integration API keys and origin-scoped Relay headers
- public generation and reuse-check rate limits
- bounded active and queued jobs
- retry and stale-job recovery
- strict parameter lengths and tag limits
- allowlisted MIME types with actual byte-signature checks
- configurable capture and generated-media size ceilings
- HMAC prompt commitments instead of raw prompts in reuse receipts
- security headers, restrictive CORS configuration, and no public embedding vectors
- a non-root Docker user
- fail-closed Object Lock writes
- event handling that does not turn an already archived paid generation into a retryable failure

For sensitive teams, self-host Recall with a dedicated B2 bucket and a restricted application key. Provider secrets should remain in Relay or the calling application, never in browser code.

## Verify the repository

Run the complete test suite:

~~~bash
python -m pytest -q
~~~

The tests cover retrieval, integrity, manifests, reuse policy, feedback, workspaces, quotas, jobs, retries, evidence, the Relay, media validation, B2 pagination, Object Lock failure behavior, and frontend regressions.

A small reproducible semantic smoke evaluation is documented in [docs/EVALUATION.md](docs/EVALUATION.md). It is intentionally presented as a narrow production check, not a broad benchmark.

## Repository map

~~~text
backend/app/       FastAPI API, Genblaze pipeline, policies, storage, security
frontend/          landing page, workspace, proof certificate, light and dark themes
sdk/python/        published recall-relay package and CLI
tests/             end-to-end and regression tests
evals/             transparent retrieval evaluation cases
docs/              launch, evidence, Relay, Vault, Ledger, and demo guides
scripts/           operational and validation helpers
Dockerfile         non-root production container
railway.toml       Railway health-check deployment configuration
~~~

## Honest boundaries

- Built-in generation supports images through Google Gemini and GMI Cloud plus short video through the official Genblaze GMI Cloud adapter. Relay capture also supports selected image, video, and audio formats.
- Exact download is bit-for-bit because it serves the stored B2 object. Provider replay is a new paid run and may differ.
- External captures preserve provider, model, caller-reported cost, and byte integrity, but are not represented as Genblaze-generated runs.
- Recall Ledger proves canonical receipt integrity and chain continuity. It does not claim third-party identity signatures or full C2PA compliance.
- Genblaze ObjectStorageSink originally returned an unsigned URL for the private B2 object. Recall now recognizes only its configured B2 host and bucket, retrieves that object through authenticated storage, enforces workspace boundaries, archives the canonical copy, and verifies the resulting manifest. The diagnosis and proof are documented in [docs/GENBLAZE_SINK_ISSUE.md](docs/GENBLAZE_SINK_ISSUE.md).
- The public library is intentionally small. Published matching results should not be interpreted as a large-scale benchmark.

## Why we believe this matters

Fragmented creative operations lead to duplicated work and wasted budget. Recall moves reuse from an archival task to a decision made before another provider call. It also treats near matches conservatively because false positives become more damaging as a library grows.

The research and platform decisions behind the product are collected in [docs/EVIDENCE.md](docs/EVIDENCE.md), including work on duplicate creative effort, AI-assisted asset management, near-duplicate retrieval, Genblaze provenance, and B2 Object Lock.

## Built for the Backblaze Generative Media Hackathon

Recall directly addresses all four judging dimensions:

| Judge dimension | Recall evidence |
| --- | --- |
| Real-world utility | Prevents redundant paid generations and retrieves approved originals across tools |
| Production readiness | Live deployment, health and readiness checks, quotas, jobs, retries, private workspaces, prompt-free CSV economics export, tests, SDK, and documented security controls |
| B2 storage and orchestration | Image and video assets, manifests, catalog, jobs, receipts, events, evidence, exact delivery, workspace isolation, and Object Lock finals |
| Genblaze use | Native image and video pipelines, provider abstraction, visible cross-provider fallback, canonical manifests, verification, retries, and lineage |

Recall is substantially different from Trueprint, the team's other submission. Recall acts before generation to decide whether a team should reuse, fork, or pay for new media. Trueprint acts after media exists to evaluate authenticity and claims. Their users, workflows, outcomes, and success metrics are different.

For the final submission and three-minute walkthrough, see [docs/LAUNCH.md](docs/LAUNCH.md), [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md), and [docs/DEVPOST_SUBMISSION.md](docs/DEVPOST_SUBMISSION.md).

## License

Recall is available under the [MIT License](LICENSE).
