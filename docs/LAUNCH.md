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

1. Show a prior asset and its real recorded model cost.
2. Explain why this is not a folder: Recall checks shared cross-tool memory before a provider call and preserves the request, exact bytes, recipe, intent, lineage, integrity, and economics.
3. Open Proof and show that SHA-256 matches the B2 object and the Genblaze manifest is present.
4. State the honest split: exact Retrieve returns stored B2 bytes; paid recipe replay may differ.
5. Type a semantically related need and let Reuse Gate pause the paid action.
6. Hold on the live comparison between generating again and `$0.00` new model cost for exact retrieval.
7. Retrieve the original and show Saved by Recall and Paid Calls Avoided increase.
8. Show one existing controlled fork and its parent link.
9. Show the approved Object Lock state, then close on: Recall proves what the team did not need to generate.

## Honest impact framing

- Do not inflate the ledger by repeatedly retrieving the same asset solely for the recording.
- Lead with three real values together: total saved, paid calls avoided, and real savings divided by generation spend. The ratio gives a small public demo economic weight without inventing dollars.
- To seed a richer library, use `recall-relay` to capture actual historical outputs and their real caller-reported provider costs. Do not enter fictional costs.
- If you show a team-scale projection, label it as a projection and keep it separate from the live savings ledger.
## Devpost submission

- [ ] Public working URL
- [ ] Public GitHub repository containing this source and README, or if private, access granted to the `b2genblaze` testing account
- [ ] Explicit provider/model list: Google Gemini `gemini-3.1-flash-image`; Gemini embeddings if enabled
- [ ] Explicit B2 explanation: assets, manifests, catalog, events, Object Lock copies
- [ ] Explicit Genblaze explanation: Pipeline, custom provider, fallback models, canonical manifests, lineage
- [ ] Public video below three minutes with live functionality on-screen
- [ ] Multiple-submission differentiation stated clearly: Recall is pre-generation spend control and cross-tool memory; Trueprint is post-creation authenticity analysis
- [ ] Optional: submit the documented ObjectStorageSink/B2 reproduction as constructive Genblaze feedback

## Rules-specific access check

- The Recall and Trueprint submissions must remain unique and substantially different under Rules line 280. Use the differentiation sentence above in the Devpost copy and demo narration.
- The current Recall repository is public. If that changes, grant `b2genblaze` access before submission.
- Confirm the public app remains available free of charge through the judging period and that the demo quota allows the complete judge test flow.
## Honest limitations to state

- Retrieval is bit-exact because it serves the stored B2 object. A provider replay is a new paid model run and may not be bit-exact for nondeterministic providers.
- Native Genblaze ObjectStorageSink is feature-gated because the current provider/local-file output combination produced a reproducible B2 401; Recall still persists the original output and raw manifest directly to B2. The detailed reproduction is in `GENBLAZE_SINK_ISSUE.md`.
