# Recall: 2:45 demo recording script

## 0:00 - 0:20: the expensive failure

"This approved image already cost the team once. If a teammate cannot find it next week, another model call costs again and may return different pixels. A folder only helps if they already know what to look for. Recall checks shared generation memory before the provider runs."

Show Model Spend, Saved by Recall, Paid Calls Avoided, and the real Savings / Spend ratio. The ratio is calculated from actual ledger events, not a projection. Then point to one approved asset.

## 0:20 - 0:45: more than a DAM

"Recall connects the exact original to its prompt, provider recipe, intent, lineage, integrity hash, approval, and reuse history across tools. This is active spend control before generation, not a gallery after generation."

Open the asset and click **Proof**.

## 0:45 - 1:08: exact retrieval versus replay

"The stored B2 bytes re-hash to this SHA-256, and the Genblaze manifest is beside them. Retrieve serves these exact bytes with no new model call. Recipe replay is separate, paid, and best effort because a nondeterministic model may produce something different."

Point to the verified B2 hash, Genblaze manifest, lineage, **Retrieve exact original**, and **Paid recipe replay** controls.

## 1:08 - 1:43: the memorable savings moment

"Now a teammate asks for the same campaign asset in different words. Before Genblaze can call the provider, Reuse Gate finds the existing work and the Intent Firewall checks that brand, campaign, format, license, and language still fit."

Enter a related prompt. Pause on the live comparison:

- Generate again: the candidate's recorded model cost
- Exact B2 retrieval: `$0.00` new model cost

"The paid call is paused. I choose the exact original. No provider runs, Recall records the decision receipt, and the avoided cost increases."

Click **Retrieve exact original** and show Saved by Recall, Paid Calls Avoided, and Savings / Spend update. Keep any team-scale projection verbally separate from these live values.

## 1:43 - 2:08: change only when the need changed

"If the requirement truly changed, Recall does not force the old asset. I fork the known-good recipe, make one deliberate edit, and Genblaze records a new paid child run linked to its parent."

Show an existing fork and its lineage. Avoid spending demo time waiting for a fresh generation.

## 2:08 - 2:28: production proof

"Approved work can be copied to an Object Lock-protected B2 final. The workspace also exposes receipts, evidence exports, scoped private workspaces, an API, and the published Recall Relay package for any provider."

Show the approved state, evidence export, and package name briefly.

## 2:28 - 2:45: close on the unique value

"Backblaze B2 is the durable economic memory. Genblaze is the reproducible provider route. Recall proves what the team did not need to generate. Do not pay twice and get a different result."

End on the live savings comparison and `Generate once. Reuse forever.`
