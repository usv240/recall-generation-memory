# Recall Vault

Recall Vault turns the hosted app into a multi-workspace architecture without mixing user archives. The original public archive remains the demo workspace. A private workspace is created with `POST /api/v1/workspaces` using an existing Recall integration key. The response contains a workspace ID and a random workspace key once.

All private workspace objects are stored below:

```text
recall/workspaces/{workspace_id}/assets/...
recall/workspaces/{workspace_id}/index/...
recall/workspaces/{workspace_id}/ledger/...
```

Requests need both `X-Recall-Workspace` and `X-Recall-Workspace-Key`. The middleware validates the stored SHA-256 of the workspace key before selecting the scoped B2 store. No generation, library, receipt, feedback, asset, or presigned URL lookup can cross that scope.

For high-sensitivity teams, run the Relay against a self-hosted Recall service and a dedicated B2 bucket/key. B2 supports keys restricted by bucket and prefix; that is the recommended production boundary.

## Feedback calibration

`POST /api/v1/reuse-feedback` accepts `correct_reuse`, `too_similar`, `never_suggest`, or `always_eligible` for a receipt. The two negative verdicts block the same prompt commitment/candidate pair in that workspace. This starts with a conservative, explainable correction loop; it does not pretend an uncalibrated global similarity score is enough.
