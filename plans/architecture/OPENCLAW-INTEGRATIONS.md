# OpenClaw Integration Architecture

> **Status**: Future work -- implement after Phase 1 (shared infrastructure) is complete.
> OpenClaw integration is additive. Nothing in Phase 1 needs to change to support it.
>
> **OpenClaw repo**: `/Users/franco_lu/desktop/openclaw`

---

## 1. Why OpenClaw?

OpenClaw is a multi-agent personal AI framework (Node.js) with capabilities that complement
the hedge fund's Python/FastAPI core:

| Capability | Hedge Fund Use Case |
|---|---|
| Multi-channel messaging (Slack, Telegram, Discord, etc.) | Risk alerts, trade approvals, daily briefs delivered to fund managers |
| Approval gates (human-in-the-loop) | Trade confirmation for large positions before execution |
| Session-scoped agents with persistent memory | Research analysts that remember past analyses and theses |
| Subagent spawning + session messaging | Multi-agent debate for trade decisions |
| Cron scheduling | Morning briefs, earnings monitors, periodic risk scans |
| Browser automation (Playwright) | Alternative data scraping (SEC filings, sentiment, supply chain) |
| Plugin architecture | Custom financial tools without forking OpenClaw core |
| Per-agent tool policies and model selection | Value agents get fundamentals tools + Opus; screening agents get Haiku |

## 2. Integration Principle

OpenClaw sits **alongside** FastAPI as a separate process. It consumes the hedge fund's
REST API and PostgreSQL event log. It does not replace or modify any core module.

```
+=========================================================================+
|                      SINGLE FASTAPI PROCESS                             |
|  (Portfolio, Trade Execution, Data Platform, Event Log, Branches)       |
|                                                                         |
|  REST API: /api/v1/...                                                  |
|  PostgreSQL: state tables + events table                                |
+============================+============================================+
                             |
                    HTTP (localhost)
                             |
+============================+============================================+
|                      OPENCLAW GATEWAY (Node.js)                         |
|                                                                         |
|  +-------------------+  +-------------------+  +---------------------+  |
|  | hedge-fund-tools  |  | Agents            |  | Channels            |  |
|  | (plugin)          |  |                   |  |                     |  |
|  |                   |  | risk-notifier     |  | Slack               |  |
|  | portfolio.summary |  | fund-controller   |  | Telegram            |  |
|  | orders.submit     |  | morning-brief     |  | Discord             |  |
|  | risk.status       |  | earnings-watch    |  | WebChat             |  |
|  | regime.current    |  | equities-moderator|  |                     |  |
|  | ...               |  | ...               |  |                     |  |
|  +-------------------+  +-------------------+  +---------------------+  |
+=========================================================================+
```

**Key architectural properties:**

- FastAPI owns all business logic, state, and persistence. OpenClaw never writes to the database directly.
- OpenClaw agents call FastAPI endpoints via the `hedge-fund-tools` plugin. Same interface as any other API client.
- Communication is one-directional by default (OpenClaw polls FastAPI). For real-time push, add PostgreSQL `LISTEN/NOTIFY` or a webhook endpoint on FastAPI (Phase 3+).
- OpenClaw can be stopped/restarted without affecting trading operations.
- If OpenClaw is down, the fund continues operating -- it just loses the notification/conversational layer.

---

## 3. Integration #1: Human-in-the-Loop (Recommended Starting Point)

**Priority**: High -- lowest risk, highest immediate value. Additive only.

### 3.1 Risk Alert Notifications

When the Global Risk Manager (Phase 3) or Branch Risk Manager detects a limit breach,
the alert reaches fund managers via their preferred channel.

```
Global Risk Manager detects drawdown breach
    |
    +-- logs RiskAlertEvent to PostgreSQL events table (existing behavior)
    |
    +-- FastAPI webhook endpoint notifies OpenClaw Gateway
         |
         +-- agent:risk-notifier
              |-- Formats alert for human consumption
              |-- Sends to #risk-alerts Slack channel
              |-- Sends to fund manager's Telegram
              |-- If level=CRITICAL:
              |    +-- Triggers approval gate
              |    +-- "Aggregate drawdown hit 8% (limit: 10%). Halt all trading?"
              |    +-- Waits for human response
              |    +-- On approval: calls PUT /api/v1/config/mode to pause
              |    +-- On override: logs override reasoning to event log
              |-- If level=WARNING:
                   +-- Informational only, no gate
```

### 3.2 Trade Approval for Large Positions

Configurable threshold: any trade above $X or Y% of branch AUM requires human sign-off.

```
Branch Module produces trade decision
    |
    +-- calls trade_execution_service.submit_order()
         |
         +-- Order Validator checks: does this exceed approval threshold?
              |
              +-- Below threshold: execute normally (paper/live adapter)
              |
              +-- Above threshold: set status=PENDING_APPROVAL
                   |
                   +-- FastAPI notifies OpenClaw Gateway
                        |
                        +-- agent:trade-approver
                             |-- Sends to fund manager's Slack:
                             |   "Equities branch wants to BUY 500 shares NVDA ($612K).
                             |    Confidence: 78%. Reasoning: [agent reasoning].
                             |    This is 6.1% of branch AUM. Approve?"
                             |
                             |-- Approval gate waits for response
                             |-- On approve: calls POST /api/v1/orders/{id}/approve
                             |-- On reject: calls POST /api/v1/orders/{id}/reject
                             |-- On modify: "Reduce to 300 shares" -> resubmit
                             |-- Timeout (configurable): auto-reject + alert
```

**FastAPI changes required** (minimal):
- Add `PENDING_APPROVAL` to `OrderStatus` enum
- Add `/api/v1/orders/{id}/approve` and `/api/v1/orders/{id}/reject` endpoints
- Add a webhook/notification mechanism (simple HTTP POST to OpenClaw, or PostgreSQL NOTIFY)
- Add approval threshold to branch configuration

### 3.3 Daily P&L Digest

Cron-scheduled OpenClaw agent generates a morning brief.

```
OpenClaw cron: daily at 6:30 AM ET
    |
    +-- agent:morning-brief
         |-- Calls GET /api/v1/fund/summary
         |-- Calls GET /api/v1/portfolios/{branch_id} for each branch
         |-- Queries recent events (trades, risk alerts) from past 24h
         |-- Formats a digest:
         |
         |   "Good morning. Fund NAV: $1.02M (+0.3% yesterday).
         |    Equities: +0.5% (NVDA +2.1%, AAPL -0.3%)
         |    Crypto: flat (BTC -0.1%)
         |    3 trades executed yesterday. No risk alerts.
         |    Earnings today: AAPL (after close), MSFT (after close)."
         |
         +-- Sends to #daily-brief Slack channel
         +-- Sends to fund manager's Telegram
```

---

## 4. Integration #2: Conversational Fund Control

**Priority**: Medium -- builds on Integration #1. Turns the fund into a conversational system.

### 4.1 Fund Controller Agent

A natural language interface to the hedge fund, accessible via any OpenClaw channel.

```
Fund Manager (via Telegram/Slack/WebChat)
    |
    +-- OpenClaw Gateway
         |
         +-- agent:fund-controller
              |
              Tools (hedge-fund-tools plugin):
              |-- portfolio.summary    -> GET /api/v1/fund/summary
              |-- portfolio.branch     -> GET /api/v1/portfolios/{branch_id}
              |-- portfolio.positions  -> GET /api/v1/portfolios/{branch_id}/positions
              |-- orders.list          -> GET /api/v1/orders?branch_id=&status=
              |-- orders.submit        -> POST /api/v1/orders (with approval gate)
              |-- trades.recent        -> GET /api/v1/trades?since=
              |-- risk.alerts          -> GET /api/v1/risk-alerts?resolved=false
              |-- regime.current       -> internal Market Regime Analyzer call
              |-- config.mode          -> PUT /api/v1/config/mode (with approval gate)
              |-- snapshots.history    -> GET /api/v1/fund/snapshots
```

**Example interactions:**

```
Manager: "How's the fund doing?"
Agent:   "NAV is $1.02M (+0.3% today). Equities up 0.5%, crypto flat.
          No active risk alerts. Market regime: risk-on."

Manager: "Show me the equities positions"
Agent:   "Equities branch has 12 positions:
          NVDA: 200 shares ($136K, +2.1% today)
          AAPL: 150 shares ($42K, -0.3% today)
          ..."

Manager: "Pause crypto trading"
Agent:   [APPROVAL GATE] "This will pause the crypto branch. All pending
          orders will be cancelled. Confirm?"
Manager: "Yes"
Agent:   "Crypto branch paused. 2 pending orders cancelled."

Manager: "What would happen if we increased equities allocation by 15%?"
Agent:   "Current equities allocation: $400K (39% of AUM).
          +15% would bring it to $460K (45% of AUM).
          This would reduce available capital for other branches by $60K.
          Crypto would drop from $200K to $165K.
          Note: Global risk limit is 50% max per branch -- 45% is within limits."
```

### 4.2 hedge-fund-tools Plugin

OpenClaw plugin that wraps the FastAPI REST API as agent tools.

```typescript
// extensions/hedge-fund-tools/src/index.ts

import { AgentTool } from "@openclaw/plugin-sdk";

const HEDGE_FUND_API = process.env.HEDGE_FUND_API_URL || "http://localhost:8000";

export const portfolioSummaryTool: AgentTool = {
  name: "portfolio.summary",
  description: "Get aggregate fund summary: total AUM, NAV, branch breakdown, exposure",
  input: {},
  async execute(params, ctx) {
    const res = await fetch(`${HEDGE_FUND_API}/api/v1/fund/summary`);
    return res.json();
  },
};

export const portfolioBranchTool: AgentTool = {
  name: "portfolio.branch",
  description: "Get portfolio details for a specific branch including positions and P&L",
  input: {
    branch_id: { type: "string", description: "Branch ID (e.g., 'equities', 'crypto')" },
  },
  async execute({ branch_id }, ctx) {
    const res = await fetch(`${HEDGE_FUND_API}/api/v1/portfolios/${branch_id}`);
    return res.json();
  },
};

export const ordersSubmitTool: AgentTool = {
  name: "orders.submit",
  description: "Submit a trade order. REQUIRES APPROVAL before execution.",
  input: {
    branch_id: { type: "string" },
    symbol: { type: "string" },
    side: { type: "string", enum: ["buy", "sell", "short", "cover"] },
    quantity: { type: "number" },
    order_type: { type: "string", default: "market" },
  },
  async execute(params, ctx) {
    // OpenClaw's approval gate triggers here
    const res = await fetch(`${HEDGE_FUND_API}/api/v1/orders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },
};

// ... additional tools for trades, risk alerts, config, etc.
```

---

## 5. Integration #3: OpenClaw Agents as Research Analysts

**Priority**: Medium-Low -- more invasive, replaces LangGraph within branches. Evaluate after
Phase 2 (Equities Branch) is working with LangGraph.

### 5.1 Concept

Each research analyst persona becomes a persistent OpenClaw agent with its own session,
memory, and tool policy. Instead of LangGraph orchestrating stateless agent runs, OpenClaw
manages long-lived analyst agents that accumulate knowledge over time.

```
Equities Branch Module (Python)
    |
    +-- On scheduled analysis run:
         |
         +-- Calls OpenClaw Gateway to run analyst agents in parallel
         |    |
         |    +-- agent:buffett:AAPL    (Value: fundamentals, margin of safety)
         |    +-- agent:lynch:AAPL      (GARP: PEG ratio, growth at reasonable price)
         |    +-- agent:wood:AAPL       (Growth: TAM, disruption, innovation)
         |    +-- agent:graham:AAPL     (Deep value: net-net, balance sheet)
         |
         +-- Each agent:
         |    1. Recalls past analyses from memory ("Last quarter I was bearish because...")
         |    2. Fetches current data via hedge-fund-tools (prices, fundamentals, news)
         |    3. Produces signal: {direction, confidence, reasoning}
         |    4. Stores updated thesis in memory for next run
         |
         +-- Branch collects all signals
         +-- Strategy Engine aggregates (unchanged from current design)
```

### 5.2 Benefits Over LangGraph

| Aspect | LangGraph (Current) | OpenClaw Agents |
|---|---|---|
| Memory | Stateless per run; must re-derive context | Persistent per agent; remembers past analyses |
| Audit trail | LangGraph traces (custom setup) | Full session transcripts per agent, queryable |
| Model per agent | Configurable but requires custom wiring | Native: `modelId` in agent config |
| Tool scoping | Manual tool filtering | `toolPolicy.allowlist` per agent |
| Debugging | LangGraph Studio (if available) | Session transcripts + WebChat replay |
| Cross-run learning | Not built-in | Memory retrieval surfaces relevant past reasoning |

### 5.3 Trade-offs

- **Latency**: Cross-process call (Python -> Node.js -> LLM -> Node.js -> Python) adds ~50-100ms per hop vs. in-process LangGraph
- **Complexity**: Two runtimes to deploy and monitor instead of one
- **Coupling**: Branch modules become dependent on OpenClaw Gateway availability
- **Fallback**: Need a degraded mode if OpenClaw is down (skip analysis or use cached signals)

### 5.4 Recommendation

Start with LangGraph in Phase 2. Evaluate switching to OpenClaw agents after the equities
branch is stable and you can A/B test the two approaches. The branch module's external
contract (receives tickers + allocation, emits trade requests) stays identical either way.

---

## 6. Integration #4: Multi-Agent Debate for Trade Decisions

**Priority**: Low -- experimental. Try after Integration #3 is working.

### 6.1 Concept

Instead of independent parallel analysis followed by simple aggregation, agents debate
each other's positions before the Strategy Engine produces a final signal.

```
agent:equities-moderator (OpenClaw)
    |
    +-- Phase 1: Independent Analysis
    |    +-- Spawns agent:buffett  -> "BEARISH on AAPL. P/E of 32 is too high for 8% growth."
    |    +-- Spawns agent:lynch    -> "BULLISH on AAPL. PEG of 1.8 is attractive with services growth."
    |    +-- Spawns agent:graham   -> "NEUTRAL. Balance sheet is fortress, but price offers no margin of safety."
    |
    +-- Phase 2: Structured Debate (via sessions.send between agents)
    |    +-- Moderator to Buffett: "Lynch argues PEG is attractive. Respond."
    |    +-- Buffett: "PEG ignores that services growth is decelerating.
    |    |             Q3 services revenue growth was 14% vs 16% prior quarter."
    |    +-- Moderator to Lynch: "Buffett points to decelerating services growth. Counter?"
    |    +-- Lynch: "Deceleration is macro-driven, not structural. Installed base of
    |    |           2.2B devices is still growing. Services take rate has room to expand."
    |    +-- (2-3 rounds max to prevent loops)
    |
    +-- Phase 3: Synthesis
         +-- Moderator reads all transcripts
         +-- Produces final signal with weighted reasoning:
              {
                direction: "NEUTRAL",
                confidence: 55,
                reasoning: "Analysts split. Strong balance sheet and services moat,
                            but valuation leaves limited upside. Small position warranted.",
                dissent: "Buffett remains bearish on valuation grounds."
              }
```

### 6.2 Why This Requires OpenClaw

- **`sessions.send`**: Agents message each other through their sessions
- **Subagent spawning**: Moderator spawns analysts, collects results
- **Session transcripts**: Full debate is persisted and auditable
- **Iteration limits**: OpenClaw's max iteration config prevents infinite debate loops

### 6.3 Open Questions

- Does debate actually improve signal quality vs. independent analysis + weighted average?
- How many debate rounds are optimal? (Likely 2-3 before diminishing returns)
- Should the moderator use a stronger model (Opus) than the debaters (Sonnet)?
- How to measure: run both approaches on historical data, compare Sharpe ratios

---

## 7. Integration #5: Browser Automation for Alternative Data

**Priority**: Low -- nice-to-have. Independent of other integrations.

### 7.1 Concept

OpenClaw's Playwright-based browser tool enables agents to scrape unstructured data sources
that lack APIs. These become additional data adapters for the Data Platform module.

### 7.2 Candidate Data Sources

| Source | Data Type | Branch Use Case |
|---|---|---|
| SEC EDGAR | 10-K, 10-Q, 8-K filings | Equities: deep fundamental analysis |
| Earnings call transcripts | Management commentary | Equities: sentiment, guidance signals |
| Reddit (r/wallstreetbets, r/cryptocurrency) | Retail sentiment | Crypto, meme stocks |
| GitHub activity | Open source project health | Equities (tech sector): developer adoption |
| Job postings (LinkedIn, Indeed) | Hiring trends | Equities: leading indicator of growth/contraction |
| Port/shipping data (MarineTraffic) | Supply chain activity | Commodities: demand signals |
| Google Trends | Search interest | Cross-branch: consumer sentiment proxy |

### 7.3 Implementation Pattern

```typescript
// extensions/hedge-fund-tools/src/scrapers/sec-filing.ts

export const secFilingTool: AgentTool = {
  name: "sec.filing",
  description: "Fetch and parse an SEC filing (10-K, 10-Q, 8-K) for a given ticker",
  input: {
    ticker: { type: "string", description: "Stock ticker (e.g., AAPL)" },
    filing_type: { type: "string", enum: ["10-K", "10-Q", "8-K"] },
  },
  async execute({ ticker, filing_type }, ctx) {
    // 1. Navigate to EDGAR full-text search
    // 2. Find most recent filing of requested type
    // 3. Extract text content
    // 4. Return structured sections (risk factors, MD&A, financials)
  },
};
```

**Data flow**: OpenClaw agent scrapes data -> returns structured result -> branch module
receives it alongside API-sourced data -> research agents analyze both.

### 7.4 Alternative: Python-Side Scraping

Browser scraping could also live in the Data Platform module as a Python adapter
(using Playwright for Python or Selenium). The advantage of putting it in OpenClaw is
that the scraping agent can reason about what to look for, handle CAPTCHAs, and adapt
to page structure changes -- it's an agent task, not a static scraper.

---

## 8. Integration #6: Cron-Driven Agent Workflows

**Priority**: Medium -- independent of other integrations. Easy to add incrementally.

### 8.1 Scheduled Tasks

| Task | Schedule | Agent | Delivery |
|---|---|---|---|
| Morning market brief | Daily 6:30 AM ET | `agent:morning-brief` | Slack #daily-brief, Telegram |
| Earnings calendar monitor | Daily 8:00 PM ET | `agent:earnings-watch` | Slack #earnings |
| Portfolio risk scan | Every 4 hours | `agent:risk-scanner` | Slack #risk-alerts (if issues found) |
| Weekly allocation review | Sunday 6:00 PM ET | `agent:allocator-review` | Slack #allocations |
| Weekend macro digest | Saturday 9:00 AM ET | `agent:macro-digest` | Telegram |
| Sentiment pulse (crypto) | Hourly | `agent:crypto-sentiment` | Slack #crypto (if significant shift) |

### 8.2 Separation from APScheduler

APScheduler (in FastAPI) handles **trading cadence** -- triggering branch analysis and
execution cycles. OpenClaw cron handles **communication and monitoring** -- generating
reports, scanning for issues, and delivering insights to humans.

They don't overlap:
- APScheduler: "Run equities branch analysis at market open" (no human output)
- OpenClaw cron: "Summarize yesterday's performance and send to Slack" (human-facing)

---

## 9. Implementation Roadmap

Implement after Phase 1 is complete and working. Each integration is independent --
pick based on current needs.

### Step 1: Foundation (do first, enables all integrations)

- [ ] Stand up OpenClaw Gateway alongside FastAPI (Docker Compose addition)
- [ ] Create `hedge-fund-tools` OpenClaw plugin with read-only tools (portfolio, orders, trades, risk)
- [ ] Configure at least one channel (Slack or Telegram)
- [ ] Create `agent:fund-reader` with read-only access to verify the integration works

### Step 2: Notifications (Integration #1 -- high value, low risk)

- [ ] Add `PENDING_APPROVAL` to OrderStatus enum
- [ ] Add approve/reject endpoints to Trade Execution module
- [ ] Add webhook/NOTIFY mechanism from FastAPI to OpenClaw
- [ ] Create `agent:risk-notifier` with channel delivery
- [ ] Create `agent:trade-approver` with approval gates
- [ ] Create `agent:morning-brief` cron job

### Step 3: Conversational Control (Integration #2)

- [ ] Add write-capable tools to `hedge-fund-tools` plugin (submit orders, adjust config)
- [ ] Create `agent:fund-controller` with full tool access + approval gates on writes
- [ ] Test via WebChat first, then connect to Telegram/Slack

### Step 4: Evaluate Agent Runtime (Integration #3)

- [ ] After Phase 2 equities branch is stable with LangGraph
- [ ] Prototype one analyst agent in OpenClaw (e.g., Buffett)
- [ ] Compare signal quality, latency, debuggability vs. LangGraph version
- [ ] Decide: migrate all analysts to OpenClaw, keep LangGraph, or hybrid

### Step 5: Experimental (Integrations #4-6)

- [ ] Multi-agent debate (if Integration #3 shows promise)
- [ ] Browser scraping for alternative data
- [ ] Additional cron workflows as needs arise

---

## 10. FastAPI Changes Required

Across all integrations, the changes to the FastAPI core are minimal:

| Change | Integration | Scope |
|---|---|---|
| `PENDING_APPROVAL` order status | #1 | Add enum value + approve/reject endpoints |
| Webhook/NOTIFY to OpenClaw | #1 | Small: HTTP POST or PG NOTIFY on event insert |
| Approval threshold in branch config | #1 | Add field to branch `config` JSONB column |
| None | #2-#6 | OpenClaw reads existing API; no FastAPI changes |

The hedge fund's core architecture (modular monolith, event log, module interfaces,
repository pattern) is unchanged by any OpenClaw integration.
