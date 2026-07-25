# Recall launch and judge checklist

## Before public sharing

- [ ] Create a restricted B2 key scoped to `recall-production`; place it in Railway.
- [ ] Confirm `GET /api/ready` reports B2 reachable and provider configured.
- [ ] Configure `RECALL_API_KEYS` for integrations and a small public demo quota.
- [ ] Set `RECALL_CORS_ORIGINS` to the deployed domain.
- [ ] Confirm B2 Object Lock is enabled before demonstrating approval.
- [ ] Generate 6 to 10 showcase assets, including a fork and an approved final.
- [ ] Test generate, Reuse Gate, retrieve, fork, proof receipt, approval, light theme, and mobile layout from the public URL.

## Three-minute demo sequence

1. Show a prior asset and its `$0.067` cost.
2. Type a semantically related need; let Reuse Gate pause the paid action.
3. Retrieve the exact B2 original and show the avoided cost increase.
4. Open Proof: SHA-256 matches, manifest is in B2, and lineage is visible.
5. Fork one controlled variation and show its parent link.
6. Approve the final and explain B2 Object Lock retention.
7. Close on the savings dashboard and architecture: B2 holds the economic memory, Genblaze holds the reproducible route.

## Devpost submission

- [ ] Public working URL
- [ ] Public GitHub repository containing this source and README
- [ ] Explicit provider/model list: Google Gemini `gemini-3.1-flash-image`; Gemini embeddings if enabled
- [ ] Explicit B2 explanation: assets, manifests, catalog, events, Object Lock copies
- [ ] Explicit Genblaze explanation: Pipeline, custom provider, fallback models, canonical manifests, lineage
- [ ] Public video below three minutes with live functionality on-screen
- [ ] Optional: submit the documented ObjectStorageSink/B2 reproduction as constructive Genblaze feedback

## Honest limitations to state

- Retrieval is bit-exact because it serves the stored B2 object. A provider replay is a new paid model run and may not be bit-exact for nondeterministic providers.
- Native Genblaze ObjectStorageSink is feature-gated because the current provider/local-file output combination produced a reproducible B2 401; Recall still persists the original output and raw manifest directly to B2. The detailed reproduction is in `GENBLAZE_SINK_ISSUE.md`.