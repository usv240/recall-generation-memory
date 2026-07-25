# Recall evidence and product decisions

## What the research says

- Canto and Ascend2's 2026 survey of 434 content/creative professionals reports that fragmented asset operations cause wasted budget (39%) and duplicated work (38%). Recall therefore treats reuse as a pre-generation decision, not an archival afterthought. Source: https://www.canto.com/press-releases/cantos-state-of-digital-content-report-new-research-reveals-key-digital-content-trends-for-2026/
- Huddart's *Journal of Digital Media Management* review identifies visual similarity search and duplicate detection as core AI-DAM capabilities. Recall begins with transparent lexical matching, then can add embeddings only after it can measure false positives. Source: https://henrystewartpublications.com/wp-content/uploads/2025/02/Artificial-Intelligence-Powered-Digital-Asset-Management-Kristina-Huddart.pdf
- Morra and Lamberti's near-duplicate benchmark cautions that false alarms become important at scale. Recall's near matches are advisory; users explicitly choose reuse, reproduce, or generate. Source: https://arxiv.org/abs/1907.02821

## Platform decisions

- Genblaze supplies replayable, hash-verified manifests, parent-linked runs, fallback chains, B2/S3 sinks, and a runtime ModelRegistry. Recall persists the full manifest sidecar and exposes a replay recipe; provider prices remain explicitly configured instead of invented. Source: https://github.com/backblaze-labs/genblaze
- B2 Object Lock is an opt-in, irreversible retention mechanism. Recall only attempts lock after the user explicitly approves an asset; production setup needs an Object-Lock-enabled bucket/key with retention permissions. Source: https://www.backblaze.com/docs/cloud-storage-object-lock
- The hackathon scores real-world utility, production readiness, meaningful B2 orchestration, and meaningful Genblaze use equally. Every remaining feature must strengthen one of these four legs. Source: https://backblaze-generative-media.devpost.com/