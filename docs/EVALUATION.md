# Live reuse evaluation

Recall includes a small, reproducible production smoke evaluation in [`../evals/reuse_evaluation.json`](../evals/reuse_evaluation.json). On 2026-07-25, four read-only calls to `/api/v1/reuse-check` placed the expected archived item first: two near-verbatim requests and two natural paraphrases. The results were 4/4 top-1, with semantic scores from 0.903 to 0.966.

This is intentionally not presented as a broad benchmark. The demo library is small; the point is to make the claimed behavior directly inspectable. Re-run it with your own library by POSTing `{"prompt":"...","tags":[]}` to the public endpoint and compare the returned first `gen_id` with a labeled expected asset. Recall never retrieves automatically: the gate recommends a match and leaves reuse, a tracked fork, or a paid new generation to the user.
