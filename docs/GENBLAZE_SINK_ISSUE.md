# Genblaze ObjectStorageSink follow-up

## Resolved verification ? 2026-07-25

An earlier provider/local-file sink attempt returned B2 HTTP 401. Recall retained the direct-B2 archival fallback while this was investigated. After upgrading to the current package set (`genblaze 0.4.4`, `genblaze-core 0.3.7`, `genblaze-s3 0.3.6`), a no-provider diagnostic created a local `Asset` and uploaded it with:

```python
ObjectStorageSink(S3StorageBackend.for_backblaze(...), prefix="recall/diagnostics/native-sink", key_strategy=KeyStrategy.HIERARCHICAL).put_asset(asset)
```

The upload succeeded against the live `recall-production` B2 bucket and returned a B2 object URL and SHA-256. Native sink mode is now enabled for new Recall Genblaze runs. The direct B2 persistence remains a defense-in-depth copy path so a provider output is never discarded if a sink failure recurs.

Re-run `scripts/native_sink_diagnostic.py` with scoped B2 credentials to reproduce the non-provider test.
