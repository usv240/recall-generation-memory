# Devpost submission copy: Recall

## Project name

Recall

## Tagline

Generate once. Reuse forever.

## Project URL

https://recall-production-production.up.railway.app/app

## Repository

https://github.com/usv240/recall-generation-memory

## Short description

Recall is a cross-tool generation memory and spend-control layer for creative teams. Before any connected model runs, Reuse Gate checks private shared history. Recall can return the exact B2 original with no new model call, or preserve a deliberate new run with its Genblaze recipe, lineage, integrity hash, intent, and cost.

## Inspiration

Generative-media teams repeatedly pay to recreate work they already made because the original asset, prompt, model settings, and version history are scattered across tools. A regenerated image is often different from the one a team approved. We wanted a better default: preserve what was paid for, retrieve it exactly, and only generate when the need is genuinely new.

## Why this is not just saved files or a DAM

A folder only helps after someone remembers where a file was saved. A DAM makes existing files searchable. Neither normally intercepts a generation request before the provider charges the team, nor connects the approved bytes to provider settings, prompt intent, lineage, integrity, reuse decisions, and avoided model cost.

Recall does. Its provider-neutral Relay can sit in front of any model callback, and its Reuse Gate checks exact, lexical, semantic, and policy-safe matches before generation. A retrieval returns the original B2 bytes. A fork creates a deliberate lineage child. A recipe replay is honestly labeled as a new paid, best-effort run that may differ.
## What it does

Recall turns each generation into a durable, inspectable B2 record:

- Semantic Reuse Gate checks the existing library before a paid generation.
- Exact Download serves the original B2 object with no model call and records the avoided cost.
- Fork creates a controlled variation linked to its parent Genblaze run.
- Proof shows the stored B2 SHA-256, manifest presence, lineage, and per-asset economics.
- Approve and lock creates a B2 Object Lock-protected final asset.
- A job API keeps slow provider generation asynchronous and observable.

## How we built it

FastAPI serves a lightweight responsive web workspace and versioned API. A Genblaze Pipeline orchestrates the Google Gemini image provider, captures canonical provenance metadata, records parent-run lineage, and supports fallback configuration. Recall stores the image bytes, raw Genblaze manifest, readable recipe, catalog record, event ledger, embedding index, and approved Object Lock copy in Backblaze B2.

Google Gemini embeddings power semantic recall. They remain an internal B2 catalog index and are never exposed through the public API. Railway hosts the application; B2 remains the durable system of record.

## B2 and Genblaze usage

**Backblaze B2 is essential, not an afterthought.** Recall uses it for durable media bytes, provenance sidecars, catalog records, event/savings ledger, semantic index, pre-signed retrieval, and immutable approved finals via Object Lock.

**Genblaze is essential, not a wrapper.** Recall uses its Pipeline model, provider abstraction, provenance manifests, canonical hashes, parent-run lineage, fallback configuration, and B2/S3 integration patterns. The product exposes those capabilities as an understandable creator workflow rather than hiding them behind a one-off provider call.

## Providers and models

- Google Gemini: `gemini-3.1-flash-image` for image generation
- Google Gemini: `gemini-embedding-001` for optional semantic matching
- Genblaze SDK for orchestration and provenance
- Backblaze B2 for durable storage and Object Lock

## Substantially different from Trueprint

Recall and Trueprint solve different problems at different points in the media lifecycle. Recall is a pre-generation spend-control and cross-tool memory product: it prevents redundant provider calls, retrieves approved originals, and measures reuse economics. Trueprint is a post-creation authenticity product: it analyzes whether media and its claims can be trusted. Recall optimizes whether a team should generate; Trueprint evaluates what already exists. They have different users, core workflows, product outcomes, demos, and success metrics even though both use B2 and Genblaze infrastructure.
| Dimension | Recall | Trueprint |
| --- | --- | --- |
| Trigger | Before a provider generation call | After media already exists |
| Primary user | Creative teams and model integrators | Publishers, reviewers, and authenticity investigators |
| Core decision | Reuse, fork, or deliberately generate | Trust, challenge, or investigate a media claim |
| Main outcome | Avoided model cost plus exact retrieval | Authenticity assessment plus supporting evidence |
| Hero workflow | Reuse Gate, Relay, retrieval, and savings ledger | Competing analysis, provenance review, and verdict |
| Success metric | Paid calls safely avoided | Claims accurately verified or challenged |
## Challenges we ran into

The native Genblaze ObjectStorageSink path returned a B2 401 after a provider successfully produced a local-file asset, while direct B2 persistence with the same bucket and credentials worked. Recall handles this safely by preserving the exact bytes and raw Genblaze manifest directly in B2, and includes a documented reproduction for Genblaze feedback. We also had to separate exact retrieval from model replay: exact B2 retrieval is the reliable, free default; a new model run is honestly treated as a paid variation.

## Accomplishments we are proud of

- A live semantic match prevents a paid generation before it happens.
- Exact B2 retrieval has measured real savings rather than a fake dashboard number.
- The public workspace shows a real fork with Genblaze parent lineage.
- An approved final is protected using B2 Object Lock.
- Each asset has an integrity receipt that re-hashes the stored B2 object.
- The public API never exposes raw embedding vectors.

## What we learned

Production-minded generative media needs more than a provider call. The valuable object is the combination of bytes, provenance, lineage, integrity, economics, and retrieval behavior. Storing all of that together makes generation history reusable instead of disposable.

## What's next

Team workspaces and scoped API keys for customer integrations; larger semantic indexes; multimodal video/audio workflows; and a fully repaired native ObjectStorageSink path once the provider/local-file B2 issue is resolved.

## Repository access

The submitted repository is public. If it is changed to private before judging, grant the Backblaze testing account `b2genblaze` access before submitting, as required by the rules.
## Judge test instructions

1. Open the workspace URL.
2. Inspect a library asset and click **Proof**.
3. Verify its B2 hash and manifest receipt.
4. Run a related prompt through Reuse Gate. Show the live comparison between another model call and `$0.00` model cost for exact B2 retrieval, then retrieve the original and show the avoided-cost increase.
5. Click **Fork**, make a controlled prompt edit, and create a lineage-linked variation.
6. Click **Approve and lock** to demonstrate the Object Lock workflow.
7. Finish on Saved by Recall and Paid Calls Avoided to show that the retrieval was economically recorded.
