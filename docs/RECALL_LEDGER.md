# Recall Ledger and Intent Firewall

## The decision record

Recall creates a `recall-reuse-receipt/v1` for every reuse assessment before a paid generation is permitted. The receipt is written to `recall/ledger/` in B2 with the configured Object Lock retention policy. It contains a salted HMAC commitment to the requested prompt?not the prompt?plus the candidate asset hash, match evidence, requested intent, policy result, avoided cost, and the previous receipt hash.

`GET /api/v1/receipts/{receipt_id}/verify` recomputes the canonical receipt hash and validates its link to the previous receipt. This proves that a particular policy decision has not changed after being written. It does not claim a third-party identity signature or full C2PA compliance.

## Intent Firewall

Similarity is only a candidate signal. On a request with structured intent, Recall blocks reuse when the closest candidate has a conflicting `campaign`, `brand`, `format`, `license`, or `language`. It also blocks reuse for a required brand, format, license, or language when the legacy candidate was never profiled. The caller sees the specific blocker and can intentionally generate a tracked new asset.

New generations persist their intent profile alongside their B2 asset, Genblaze manifest, hash, and lineage.

## Demo moment

1. Ask for an existing asset with matching intent: Recall offers free retrieval and a verifiable receipt.
2. Repeat with a different brand or license: Recall reports a high similarity but blocks reuse, explaining why.
3. Open the receipt verifier: it shows a valid canonical hash and chained predecessor, with B2 retention configured.
