# Genblaze ObjectStorageSink private B2 follow-up

## Resolved status, 2026-07-26

The tested package set is `genblaze 0.4.4`, `genblaze-core 0.3.7`, and `genblaze-s3 0.3.6`.

The first diagnostic proved that `ObjectStorageSink.put_asset(...)` could upload a local Genblaze `Asset` to the live Recall B2 bucket. A full provider pipeline still failed after upload because the sink replaced the local asset URL with an unsigned private-B2 URL. A normal HTTP read of that URL returned 401.

Recall now handles that boundary explicitly:

1. Genblaze generates the asset and `ObjectStorageSink` writes it to B2.
2. Recall accepts the returned storage URL only when its scheme, host, bucket, and workspace prefix match the configured archive.
3. Recall reads the object through its authenticated B2 store instead of weakening bucket privacy.
4. Recall applies the media limit, writes the canonical Recall asset and manifests, recomputes the hash, and verifies the Genblaze manifest.

A live end-to-end generation completed with:

- generation: `gen_55b5c557d743`
- provider: `google`
- model: `gemini-3.1-flash-image`
- `native_b2_sink: true`
- `manifest_verified: true`
- archived SHA-256: `b198514ada1bc8e3f5dfce8aaa5a7a024e7101819f51bf25954dc479cbb0b85c`

Workspace sink prefixes and authenticated reads are covered by regression tests. The no-provider diagnostic remains in `scripts/native_sink_diagnostic.py` for maintainers who want to reproduce the storage boundary independently.