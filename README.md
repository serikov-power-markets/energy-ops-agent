# Energy operations agent: checked answers, runbook retrieval and a daily report on German power data

A small agent that answers questions about German hourly power data (day-ahead price, wind, solar, load; SMARD, 2023-2025) by calling tools, and a daily report that a workflow engine can call every morning. The point is the control around the language model: tools with fixed arguments, a step limit, an audit trail of every call, and a check that every number in an answer occurs in a tool result. An answer with a number no tool returned is withheld.

## What it does

| Part | File | Notes |
|---|---|---|
| Tools | `src/eoa/tools.py` | data quality (missing or duplicate hours, missing values, solar at night, implausible values), daily summary, unusual hourly prices against the same hour in the previous 28 days (robust z-score), day comparison, price slope per GW of residual load |
| Knowledge base | `kb/*.md`, `tools.search_kb` | six operations runbooks (definitions, alert rules, fault handling, notice rules); TF-IDF retrieval returns the passage with its source and nothing is generated; a question outside the base is answered with a referral |
| Customer notice | `tools.draft_notice` | template text from tool results, always marked as needing approval; the agent sends nothing |
| Agent loop | `src/eoa/agent.py` | `RulePlanner` (deterministic, no key) and `LLMPlanner` (any chat model with function calling, passed in as a callable; `providers.py` has an adapter for the Gemini REST API). Arguments are validated (dates only), unknown tools are refused, tool errors do not stop the loop |
| Grounding check | `agent.check_grounded` | numbers in the answer must match a tool result or its rounding; otherwise the answer is replaced by a refusal that lists the numbers |
| HTTP | `src/eoa/api.py` | `GET /report?day=`, `POST /ask`, `GET /health` (FastAPI) |
| MCP server | `src/eoa/mcp_server.py` | the seven tools as MCP tools, the runbooks as resources (`kb://{name}`), a prompt for a grounded report, and `verify_answer`, which runs the grounding check over the tool calls of the session (see below) |
| Workflow | `n8n/daily_energy_report.json` | schedule trigger (07:00) and a webhook trigger with `?day=`, pick the day, call `/report` with three retries, branch on `alert`, then `POST /outbox` (a local stand-in for mail) or log |

## Results

- **Known event recovered.** 12 December 2024 (dark doldrums): mean price 395 EUR/MWh, maximum 936, wind 1.5 GW on average; 14 hours flagged as unusual.
- **Injected faults found.** Tests drop hours, set a missing value and put solar output at night on a real day; all three are flagged. A clean day has no flags.
- **A naive alarm is too noisy.** At least one unusual hour occurs on 32% of days (223 of 702 days, 2024-2025), so mailing on any flag would mail every third day. The workflow alerts on a data fault or at least 8 unusual hours in a day: 29 of 702 days (4%). No data faults occur in this data set (the SMARD series is clean), so the quality checks are tested only on injected faults. See `results/flag_backtest.json`, `scripts/backtest_flags.py`.
- **A second bug found by running the workflow.** The text of a quiet day's report said 'previous 28 days', a number no tool returned, so the grounding check withheld the whole report; the tests had only covered days with anomalies. The window length is now part of the tool result, and a test runs the report for every day of a month (and a scan of all 1,065 days found none withheld).
- **A bug the tests caught.** The first version decided "no data for this day" by searching the report text for the words "no data", which the sentence "no data problems found" also contains; every day returned 404. The check now uses the tool result.
- **Price slope.** January to March 2025: 3.1 EUR/MWh per GW of residual load (correlation 0.88 over 2,136 hours), a descriptive number.

## Run with a hosted model (20 November 2026)

`providers.gemini_chat` connects the same tools and the same grounding check to a hosted model with function calling (Gemini REST API, free tier). 12 questions, one run each (`scripts/llm_eval.py`, results in `results/llm_eval_*.json`): six on data (summary, price, unusual hours, comparison, quality, customer notice), three on the knowledge base, three traps (tomorrow's price, a day outside the data, "guess from memory").

| Model | Data questions with the reference figure | Endpoint errors | Withheld by the guard | Traps answered with a number |
|---|---|---|---|---|
| gemini-3.1-flash-lite-preview, first run | 6 of 6 | 0 | 1 (false alarm) | 1 (the model made up a date for "tomorrow") |
| same, after two fixes | 6 of 6 | 0 | 0 | 0 |
| gemini-3-flash-preview | 6 of 6 | 6 of 12 (quota) | 0 | not run |

What the run showed:
- **Grounded numbers are not correct claims.** On "what will the price be tomorrow?" the first run called the summary tool for a day it had chosen itself (2025-01-25) and presented a past day as tomorrow's price. Every number was real and passed the check. The fix is in the instruction (the model is told the data period, that there is no forecast tool and that it does not know today's date); the guard cannot catch this kind of error.
- **Two false alarms of the guard were mine.** Thousands separators ("1,453 MW") and list numbering ("3. ...") were read as invented numbers; both are fixed and tested.
- Model names change quickly: `gemini-2.5-flash` was no longer available to this account, `gemini-3.8-flash` answered 503 on every attempt, and `gemini-3-flash-preview` ran out of free quota after six questions. The adapter retries on 429, 500 and 503 and on timeouts.
- Limits: 12 questions, one run each, answers of a model vary between runs.

## MCP server (24 November 2026)

The same tools are available to any client that speaks the Model Context Protocol (official Python SDK, version 2). `python -m eoa.mcp_server` starts it on stdio, `python -m eoa.mcp_server --http --port 8765` over streamable HTTP.

| Item | What it is |
|---|---|
| Tools | `daily_summary`, `data_quality`, `price_anomalies`, `compare_days`, `wind_price_slope`, `search_kb`, `draft_notice`: the registry of `tools.py` with the same argument validation (dates only, length limit) and error results instead of crashes |
| `verify_answer(answer)` | compares every number of a draft answer with the results of the tool calls made in the server session and returns the numbers no tool returned; `reset_session` empties the log |
| Resources | `kb://01_residual_load` and the five other runbooks, with unknown names refused |
| Prompt | `grounded_report(day)`: call three tools, use only their numbers, verify, stop on an error |

Checked by 8 tests (`tests/test_mcp.py`): the tool list and required arguments, a tool result identical to the direct call, an error result for a bad date, `verify_answer` accepting a figure from a tool result and flagging an invented one, runbook reading, and an end-to-end run in which the server starts as a separate process and a client calls it over stdio.

Limits: tested with the SDK's own client, not with a third-party chat application or a language model; the verification log is per server process, not per client connection; a model that skips `verify_answer` is not stopped (the grounding check stays inside the agent loop in `agent.py` for that); no authentication and no write tools, local use only.

## Not done

- `LLMPlanner` has been run against a hosted model (below), but only on 12 questions, one run each, and the instruction text was changed after the first run, so the second result is not an independent test. No comparison of models beyond the six data questions of a second model that hit its quota.
- n8n 2.35.7 (installed with npm in WSL) imported the workflow and ran it through the webhook trigger on two days: 2024-06-01 ended in the branch without alert, 2024-12-12 stored a message to operations (`results/n8n_run.md`). The schedule trigger was configured but not observed to fire. The outbox is a local file, not a mail server.
- Data is a stored SMARD export, not a live feed; tools never write or send anything, and no personal or customer data is involved.
- Research and demonstration project, not an operational monitoring system.

## Run

```bash
pip install -e ".[test]"
pytest                                  # 25 tests (8 for the MCP server)
python scripts/backtest_flags.py
uvicorn eoa.api:app --port 8000         # then GET /report?day=2024-12-12
```

## Data licence

SMARD.de, Bundesnetzagentur (CC BY 4.0): hourly day-ahead price, generation and load for Germany, 2023-2025, stored in `data/`.
