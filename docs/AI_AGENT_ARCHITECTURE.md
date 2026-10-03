# AI Agent Architecture

## 1. Principles

1. **Agents are advisory.** Their only output is a schema-validated **proposal** or **artifact** written to the research store.
2. **Capability, not instruction.** Each agent gets an explicit tool allowlist. Dangerous capabilities (orders, risk limits, lifecycle transitions, kill reset, live unlock, secrets) **do not exist as tools**, so they cannot be invoked whatever the prompt says.
3. **Model-agnostic.** A `ModelProvider` interface (`complete(messages, schema) → JSON`) supports any hosted or local model. Model id, parameters and prompt template hash are recorded on every `AgentRun`.
4. **Evidence-bound.** Numbers in agent outputs must come from tool results and are verified after generation (§6).
5. **Failure is safe.** Malformed output, a tool error, a timeout or budget exhaustion leads to `AgentRun.status = FAILED` and no side effects.

## 2. Runtime

```
AgentRuntime (research-plane process, no broker creds, no secrets in env)
  ├── ToolRegistry      — tools declared with input/output schemas + permission tags
  ├── PermissionPolicy  — per-agent allowlist; checked on every call
  ├── ContextBuilder    — assembles SYSTEM / USER / TOOL / DATA channels (§5)
  ├── OutputValidator   — Pydantic schema; reject on mismatch (1 repair retry max)
  ├── EvidenceVerifier  — re-fetches every cited metric (§6)
  └── AgentRun record   — inputs hash, model, prompt hash, tool calls, output, status, cost
```

Tool permission tags: `read:market`, `read:research`, `read:journal`, `write:proposal`, `run:experiment` (goes through the budgeted runner), `write:draft_strategy`. **There are no tags for** `trade`, `risk`, `lifecycle`, `killswitch`, `livelock` or `secrets`.

## 3. Agents

| Agent | Purpose | Tools | Output schema | Cannot |
|---|---|---|---|---|
| **ResearchAgent** | Generate hypotheses; analyse data, experiments, failures, trades | `read:*`, `write:proposal`, `run:experiment` | `HypothesisProposal`, `ResearchFinding` | trade, change risk, access secrets, approve, unlock |
| **StrategyBuilderAgent** | Hypothesis → StrategySpec + code | `read:research`, `write:draft_strategy` | `StrategyCandidate` (§3.1) | register non-DRAFT versions; edit existing versions |
| **ValidationAgent** | Interpret validation results; propose additional tests | `read:research`, `run:experiment` | `ValidationReview` | change gates; mark pass/fail (gates are deterministic) |
| **RedTeamAgent** | Try to kill the strategy | `read:*`, `run:experiment` (stress/robustness only) | `RedTeamReport` (§3.4) | approve; soften findings after the fact |
| **TradeReviewAgent** | Explain individual trades and losses from the journal | `read:journal`, `read:market` | `TradeExplanation` | modify the journal |
| **RegimeAgent** | Interpret regime shifts; propose regime-classifier variants | `read:market`, `read:research`, `write:proposal` | `RegimeAssessment` | change the production classifier |
| **PortfolioResearchAgent** | Correlation, diversification, allocation proposals | `read:research`, `read:journal` | `PortfolioProposal` | allocate capital |
| **OperationsAgent** | Summarise health, alerts, drift; draft incident notes | `read:ops` (read-only health/alerts) | `OpsSummary` | trigger or reset kill switches; restart services |

### 3.1 StrategyCandidate schema

```json
{ "hypothesis_id": "H-0047", "universe": {...}, "timeframe": "1d",
  "features": [{"name": "...", "version": 1, "params": {}}],
  "entry": [...], "exit": {...}, "risk": {...}, "invalidation": [...],
  "test_plan": {"stages": [...], "param_grid": {...}},
  "failure_modes": ["..."] }
```

The output is validated against the StrategySpec JSON Schema, then by static analysis of the generated `strategy.py` (AST allowlist: no `import os/socket/subprocess`, no `eval/exec`, no file I/O), then by sandbox contract tests. Any failure rejects the candidate.

### 3.4 RedTeamReport schema

A checklist with a verdict, evidence ids and severity for each item: look-ahead, leakage, survivorship, selection bias, data snooping (from the budget ledger), overfitting (PBO, DSR), parameter fragility, sample size, unrealistic execution, regime dependency, cost sensitivity, liquidity, hidden correlation. Several of these are **computed deterministically first** (budget counts, PBO, DSR, fragility score, cost breakeven). The agent adds argument on top and cannot override the computed values. The overall verdict is `KILL | SERIOUS_CONCERNS | MINOR_CONCERNS | NO_ISSUES_FOUND`. A `KILL` blocks promotion until a human reviews it.

## 4. Self-Approval Prevention

- Lifecycle gates read stored evidence. An agent's opinion is never a gate input except for a red-team `KILL`, which can only *block*.
- An `AgentRun` cannot write an `Approval`. Approvals require the operator token, which exists only in the UI session and is not in the agent process environment.
- A strategy's builder run id and red-team run id are recorded. The same agent run cannot be both.

## 5. Prompt-Injection Defence

Context is assembled in separate, labelled channels:

| Channel | Content | Trust |
|---|---|---|
| SYSTEM | role, rules, output schema | trusted (versioned templates) |
| USER | operator request | trusted operator |
| TOOLS | tool results (structured JSON) | trusted *structure*, untrusted *string fields* |
| MARKET_DATA / RESEARCH_DATA / STRATEGY_DATA | numbers; external text quoted in delimited blocks with provenance | **untrusted** |

Measures:
- External text (news, social, web, company descriptions) is wrapped in delimited blocks with a source tag. The system prompt tells the model to treat it as data.
- The **real protection is §2**: even a fully hijacked agent can only produce a schema-valid proposal in DRAFT. It can trade nothing, approve nothing and change no limit.
- Social text never enters the trading plane. Strategies see numeric features only.
- Agent outputs are rendered in the UI as escaped text (no HTML), which prevents a second-order XSS.

## 6. Evidence Verification

Every numeric claim must be emitted as structured data:
`{"metric": "sharpe", "value": 0.84, "source": "WF-000012", "scope": "stitched_oos"}`

`EvidenceVerifier` re-reads the metric from the database. On a mismatch beyond rounding, or a nonexistent source id, the artifact is rejected and marked `FABRICATION_DETECTED`. Free-text numbers that are not backed by a claim are stripped or flagged. The UI shows each claim with a link to its evidence.

## 7. Failure Behaviour

| Failure | Behaviour |
|---|---|
| Schema mismatch | 1 repair attempt with validator errors; then FAILED |
| Tool permission denied | Logged as a security event; the run continues without the tool; a repeated attempt fails the run |
| Budget exhausted | Runner refuses; the agent must report `BUDGET_EXHAUSTED` |
| Model timeout / provider down | FAILED; nothing written |
| Evidence mismatch | FAILED; `FABRICATION_DETECTED` alert |
