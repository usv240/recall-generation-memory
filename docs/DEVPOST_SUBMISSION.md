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

Recall is a reusable generation memory for creative teams. It archives every AI-generated asset, its Genblaze provenance recipe, lineage, integrity hash, and cost on Backblaze B2. Before a team pays to generate again, Recall finds the original or a semantic match, retrieves the exact B2 bytes for free, and records the avoided generation cost.

## Inspiration

Generative-media teams repeatedly pay to recreate work they already made because the original asset, prompt, model settings, and version history are scattered across tools. A regenerated image is often different from the one a team approved. We wanted a better default: preserve what was paid for, retrieve it exactly, and only generate when the need is genuinely new.

## What it does

Recall turns each generation into a durable, inspectable B2 record:

- Semantic Reuse Gate checks the existing library before a paid generation.
- Exact Retrieve serves the original B2 object with no model call and records the avoided cost.
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

## Judge test instructions

1. Open the workspace URL.
2. Inspect a library asset and click **Proof**.
3. Verify its B2 hash and manifest receipt.
4. Click **Retrieve** to retrieve the exact original and add avoided cost.
5. Click **Fork**, make a controlled prompt edit, and create a lineage-linked variation.
6. Click **Approve and lock** to demonstrate the Object Lock workflow.
7. Enter a related prompt to see the Semantic Reuse Gate before generating.