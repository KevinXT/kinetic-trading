# Strategy Copilot — startup build doc (v0.1)

**Mission**  
Build a research-first trading platform where users can **design, test, and understand** strategies with reproducible artifacts and an **AI Copilot** that summarizes, compares, and challenges assumptions—**decision support, not decision making**.

**One-line**  
“Copilot for strategy research (like VS Code Copilot), grounded in your backtests and artifacts.”

---

## 1) Product principles

### 1.1 What we are

- **Strategy Lab**: experiments → artifacts → comparisons → iteration
- **Risk-first**: drawdown, regime behavior, robustness > flashy returns
- **AI as interpreter**: explains, summarizes, asks questions, compares—**never** “buy/sell now”
- **Reproducibility**: every result is traceable to config + data + code version

### 1.2 What we are not

- Not “AI predicts stocks”
- Not an autonomous trading bot (especially v1)
- Not an advice product (“what should I buy?”)

---

## 2) Target users & wedge

### 2.1 Initial wedge user

**Quant-curious retail traders / engineers** who:

- iterate strategies, read Reddit/CT finance, want structure
- are frustrated with spaghetti backtests and “trust me bro” results
- will pay for clarity + organization + risk discipline

### 2.2 Jobs to be done

- “Help me understand what I built.”
- “Help me know if I’m overfitting.”
- “Help me compare my experiments and improve them.”
- “Help me build a strategy library like a real research team.”

---

## 3) Core user loop (the compounding engine)

1. **Define strategy** (YAML / builder)
2. **Run experiment** (pipeline runner)
3. **Generate artifacts** (metrics, curves, risk report)
4. **Copilot interprets** (summary + risks + questions)
5. **Compare to past runs**
6. **Iterate** (change one thing, rerun)

Everything in the product serves this loop.

---

## 4) Product scope roadmap

### Phase A — Foundation (engine + artifacts)

**Goal:** One command runs end-to-end and produces a clean folder of results.

**Deliverables**

- Runner: load resolved config → parse plan → execute tasks
- Plan parser: YAML pipeline → ordered steps
- Artifact writer: consistent output format
- 1–2 baseline strategies
- Backtest harness (minimal but correct)

**Exit criteria**

- You can run a plan (e.g. `python -m trading_platform` or a thin CLI) against `configs/plans/…`
- It creates a run folder with reproducible artifacts

---

### Phase B — Research layer (reports + comparisons)

**Goal:** Results become interpretable and comparable.

**Deliverables**

- Standard metrics: CAGR, Sharpe, max DD, win rate, exposure
- Risk report: drawdowns, tail losses, leverage/position sizing notes
- Strategy report: plain English description of what it does
- Comparison engine: compare run A vs run B → `comparison.md`

**Exit criteria**

- User can answer: “Is this better and why?”

---

### Phase C — Copilot v1 (artifact-grounded AI)

**Goal:** Copilot feels like a quant buddy reviewing your research.

**Copilot abilities (v1)**

- Summarize strategy from config + artifacts
- Explain performance across regimes (basic)
- Flag overfitting signals (parameter count, instability, OOS collapse)
- Compare two runs and produce pros/cons
- Ask next-step questions (suggest experiments, not trades)

**Hard rules**

- Copilot may only cite **your artifacts** (no vibes)
- No “you should buy X”
- Always include uncertainty where appropriate

**Exit criteria**

- “Wow” demo: run a backtest → Copilot explains it better than user can

---

### Phase D — UI (make it feel like a product)

**Goal:** “VS Code for strategy research.”

**UI layout**

- Left: Strategy (YAML / builder)
- Center: Results (charts + metrics)
- Right: Copilot chat + run comparisons
- Bottom: Artifacts explorer (reports, logs, configs)

**Exit criteria**

- A user can complete the core loop without touching terminal

---

### Phase E — Trading integration (only after trust)

**Goal:** Make it real without blowing up risk/liability.

**Order**

1. Paper trading (sandbox)
2. Read-only brokerage view (positions/exposure)
3. Manual execution buttons
4. Automation (final boss, later)

**Exit criteria**

- Execution comes last, behind guardrails + trust

---

## 5) System architecture (intent vs repo layout)

Historical notes referred to `core/pipeline_engine/`. The **current** layout is a monorepo:

- `packages/pipeline_core` — runner, parser, `RunContext`, task registry
- `packages/common` — YAML config loading, shared errors
- `packages/news_data`, `packages/market_data` — integrations & domain
  (`packages/strategy_sdk` is deferred / empty and not part of the default install)
- `apps/trading_platform` — application entrypoints and product glue
- `configs/` — YAML plans and presets

Conceptual modules (may span packages):

- `domain/strategies/` — strategy contracts + implementations
- `research/backtest/` — data → signals → fills → PnL
- `research/reports/` — metrics + risk report + comparison report
- `copilot/` — artifact indexer, prompts, guardrails
- `app/` — UI + API (future)

### Artifact contract (target stable surface)

Every run should output (evolve as the engine grows):

- `config_resolved.yaml`
- `run_metadata.json` (timestamp, git commit, data range)
- `metrics.json`, `equity_curve.csv`, `drawdown.csv` (when backtest exists)
- `risk_report.md`, `strategy_summary.md`, `logs.txt` (when reporting exists)

This powers comparisons + Copilot.

---

## 6) Copilot design spec (v1)

### 6.1 Inputs

- Strategy config (resolved YAML)
- Run artifacts (metrics + curves + reports)
- Optional: last N runs (for comparisons)

### 6.2 Outputs

- Plain-English strategy explanation
- Top 3 strengths / top 3 risks
- “What changed vs last run” (if comparing)
- Next experiment suggestions (1–3)

### 6.3 Guardrails

- No direct recommendations to buy/sell specific tickers
- No certainty language (“guarantee,” “will win”)
- Always ground claims in artifact fields (“Your max DD was X”)

### 6.4 Memory (v1.5+)

- Save: user’s strategy tags, preferred risk level, common failure modes
- Retrieve: similar strategies, historical run patterns, common mistakes

---

## 7) MVP definition

**MVP = research loop + Copilot v1**

Must-have:

- Run engine end-to-end
- Consistent artifacts
- Comparison report
- Copilot summary grounded in artifacts

Nice-to-have: UI polish, more strategies, paper trading.

Not in MVP: full brokerage automation, social/marketplace, “AI trades for you.”

---

## 8) 90-day execution plan (sketch)

- **Days 1–14:** Engine locked — runner, parsing, artifact contract
- **Days 15–30:** Backtest + reports — metrics, equity curve, risk markdown, comparison
- **Days 31–60:** Copilot v1 — index + summary/compare prompts + guardrails
- **Days 61–90:** UI v0 — dashboard, run selector, Copilot panel, artifact viewer

---

## 9) Demo script (hiring / Reddit)

1. Open strategy YAML  
2. Run backtest → artifacts  
3. Show risk report + equity curve  
4. Ask Copilot: explain strategy, biggest hidden risk, compare to last run  
5. Copilot: strengths/risks, regime notes, next experiment ideas  

---

## 10) Pricing / positioning (early)

**Positioning:** “AI-assisted strategy research and risk interpretation—**not** automated trading.”

Early pricing idea: free tier with limited runs; paid for comparisons + Copilot memory + export.

---

## 11) Non-negotiables

- Risk-first language
- No “get rich” marketing
- Transparent limitations
- Reproducibility > flash
- Copilot stays a research partner

---

*Converted from legacy `Idea.txt`; paths updated to match the monorepo.*
