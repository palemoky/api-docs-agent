# api-docs-agent

API documentation goes stale because updating it is a separate act of discipline from
changing the code. This agent removes the discipline requirement: on every commit it
reads the diff, decides whether the public HTTP contract actually changed, and — only
when it did — opens a pull request with the documentation edit already written.

A human still approves. The agent proposes; the merge button decides.

```
commit ──► pre-filter ──► analyse ──► generate ──► validate ──► pull request ──► notify
           (free,         (1 call)    (1 call     (schema-      (human review)   (console /
            regex)                     per doc)    checked)                       email / Feishu)
```

## What it is not

It does not write documentation from scratch, it does not run as a long-lived service,
and it does not merge anything. It answers one narrow question well — *did this commit
make the docs wrong, and if so, what is the minimal edit?* — and puts the answer where
your team already reviews changes.

## Design

Four extension points, each a small abstract class plus a registry. Adding an
implementation never requires touching the pipeline.

| Extension point | Contract | Ships with |
|---|---|---|
| `LLMProvider` | `complete()`, `complete_json()` | `anthropic`, `openai` (any OpenAI-compatible endpoint), `litellm` (100+ vendors), `echo` |
| `DocAdapter` | `prompt_guidance()`, `validate()`, `summarize()` | `openapi`, `markdown` |
| `Notifier` | `send()` | `console`, `email`, `feishu` |
| `ReviewTarget` | `default_branch()`, `create_pull_request()` | `github` |

Three decisions worth calling out:

**A deterministic pre-filter runs before any model call.** Glob matching picks candidate
files, then regexes look for HTTP-surface idioms in the *changed lines only*. Most commits
stop here and cost nothing. `api-docs-agent inspect` shows you exactly what the filter
sees so you can tune it against your codebase for free.

**Analysis and generation are separate calls.** The first is cheap and answers only
"does anything need to change?". Most runs that reach a model stop there. Generation runs
once per affected document and returns the complete file, so it can be diffed and checked.

**Nothing reaches a pull request unvalidated.** The OpenAPI adapter parses the result,
checks the structural markers, resolves every `$ref`, and runs `openapi-spec-validator`
when it is installed. A failed check goes back to the model *with the errors* — twice —
and if it still fails, the run ends quietly rather than proposing a broken spec.

## Install

```bash
uv tool install 'api-docs-agent[anthropic,openapi]'   # or: pip install ...
```

Extras are the vendor SDKs: `anthropic`, `openai` (any OpenAI-compatible endpoint),
`litellm` (everything else), and `openapi` for full spec validation.

## Try it in five minutes

The repository ships a demo FastAPI service and its spec.

```bash
git clone https://github.com/Palemoky/api-docs-agent && cd api-docs-agent
uv sync --all-extras
export ANTHROPIC_API_KEY=...

# 1. Change the API: add an endpoint to examples/demo-service/src/app.py
git commit -am "feat: add DELETE /users/{user_id}"

# 2. See what the free pre-filter thinks — no model call
uv run api-docs-agent inspect -c examples/demo-service/.api-docs-agent.yml

# 3. Run the full analysis and print the proposed spec diff, without pushing
uv run api-docs-agent check -c examples/demo-service/.api-docs-agent.yml --dry-run --show-diff
```

Drop `--dry-run` (with `GITHUB_TOKEN` set) and it pushes a branch and opens the PR.

## Contributing

The project is managed with [uv](https://docs.astral.sh/uv/). `uv.lock` is committed,
so every contributor and CI job resolves the same dependency graph.

```bash
uv sync --all-extras   # create .venv from the lockfile
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy
```

Dev tooling lives in a PEP 735 dependency group rather than an extra, so it is not
installable from the published package. CI runs `uv sync --frozen`, which fails if
`uv.lock` has drifted from `pyproject.toml` — a dependency change cannot land without
its lockfile.

## Configure

`api-docs-agent init` writes a starter `.api-docs-agent.yml`. Secrets stay in the
environment via `${VAR}` interpolation, so the file itself is safe to commit.

```yaml
llm:
  provider: anthropic
  model: claude-opus-5
  api_key: ${ANTHROPIC_API_KEY}

docs:
  sources:
    - path: docs/openapi.yaml
      type: openapi
      description: The public REST API.
  style_guide: |
    Every schema property needs a description. Keep summaries under 60 characters.

detect:
  watch_paths: ["src/**/*.py"]
  ignore_paths: ["**/tests/**"]

github:
  token: ${GITHUB_TOKEN}
  labels: [documentation, automated]
  reviewers: [alice]

notifiers:
  - type: console
  - type: feishu
    options:
      webhook_url: ${FEISHU_WEBHOOK_URL}
```

### Using a different model

Nothing in the pipeline imports a vendor SDK — swapping models is configuration:

```yaml
# DeepSeek
llm: { provider: openai, model: deepseek-chat, base_url: https://api.deepseek.com/v1, api_key: "${DEEPSEEK_API_KEY}" }

# A local Ollama or vLLM server
llm: { provider: openai, model: qwen2.5-coder:32b, base_url: http://localhost:11434/v1, api_key: "not-needed" }
```

The `openai` provider negotiates structured output down through JSON-schema mode, JSON
mode, and finally prompt-only parsing, because self-hosted endpoints advertise OpenAI
compatibility at very different levels of fidelity.

For a vendor that is neither Anthropic- nor OpenAI-shaped — Bedrock, Vertex, Gemini,
Cohere — use the `litellm` provider, which turns the vendor into a model string:

```yaml
llm: { provider: litellm, model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0 }
```

There is deliberately no LangChain dependency. The agent makes two single-turn JSON
calls — no chains, no tool loop, no retrieval — so a framework would replace this
30-line abstraction with a larger one without removing any of the work.

Adding a provider that is not OpenAI-shaped is one file:

```python
from api_docs_agent.llm import LLMProvider, register_provider


@register_provider
class MyProvider(LLMProvider):
    name = "my-provider"

    def complete(self, *, system, prompt, max_tokens=None) -> str: ...
```

`complete_json()` is inherited — it prompts for JSON and repairs the usual failures — so
override it only if your backend has native structured output.

## Run it on every commit

Copy `.github/workflows/api-docs-sync.yml` into your repository and add
`ANTHROPIC_API_KEY` to its secrets. It needs `fetch-depth: 0`, because the agent diffs a
commit range rather than reading a snapshot.

## CLI

| Command | What it does |
|---|---|
| `check` | The full pipeline. `--dry-run` stops before pushing; `--show-diff` prints the proposal. |
| `inspect` | Show what the pre-filter selected and why. Never calls a model. |
| `validate` | Check the config and validate every configured documentation file. |
| `providers` | List every registered provider, doc format and notifier. |
| `init` | Write a starter config into the repository. |

## Roadmap

- GitLab and Gitea `ReviewTarget` implementations
- A webhook service wrapping the same pipeline, for teams that cannot use CI
- Framework-aware parsing (FastAPI/Spring route extraction) to replace regex signals
- Incremental spec editing via JSON Patch, for specs too large to regenerate whole

## License

Apache 2.0
