# recall-relay

`recall-relay` is a small, dependency-free Python client that checks a private Recall workspace before calling Gemini image generation. On a safe reuse match it returns the stored asset; on a miss it calls Gemini with the key in your local process, then captures the resulting bytes in Recall.

## Install

```bash
pip install recall-relay
```

For local development:

```bash
pip install -e sdk/python
```

## Configure

```bash
export RECALL_URL=https://recall-production-production.up.railway.app
export RECALL_WORKSPACE_ID=ws-...
export RECALL_WORKSPACE_KEY=save-the-one-time-key
export GEMINI_API_KEY=your-google-key
```

## Use

```bash
recall-relay doctor
recall-relay gemini "Editorial cobalt glass product image" --tag launch --intent brand=Recall --intent format=1:1 --output hero.png
```

The CLI never prints credentials. `gemini` calls Google only after Recall's private workspace returns a safe miss.

## OpenAI image generation

Set `OPENAI_API_KEY` locally and run:

```bash
recall-relay openai "Editorial cobalt glass product image" --model gpt-image-1 --output hero.png
```

## Publish to PyPI

The repository includes a secure GitHub Trusted Publishing workflow at `.github/workflows/release.yml`. Configure PyPI to trust `usv240/recall-generation-memory`, workflow `release.yml`, environment `pypi`, then publish a GitHub Release. No long-lived PyPI token is needed. Until then, the GitHub `pip install` command above installs the exact public source.

## Any provider: the adoption API

Use `generate_with()` to add Recall in front of any image, video, or audio provider. The provider call and its credentials stay in your application; Recall only receives completed media after a safe miss.

```python
from recall_relay import RecallRelay

relay = RecallRelay("https://your-recall.example", workspace_id="ws-...", workspace_key="...")

result = relay.generate_with(
    "A launch visual for our cobalt collection",
    generator=lambda prompt: call_your_provider(prompt),  # returns bytes
    provider="your-provider",
    model="your-model",
    media_type="image/png",
    tags=["launch", "cobalt"],
    intent={"brand": "Acme", "format": "1:1"},
)
```

On `reused`, `call_your_provider` is never invoked. On `generated_and_captured`, Recall stores the output, byte hash, provider/model record, and reuse receipt for the next request.