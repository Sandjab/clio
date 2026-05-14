# Prompt template — detect_churn

Substitute `{{state.x}}` placeholders from `state.json` before sending.

---

## Task

You are executing STEP `detect_churn` (MODE: judgment).

### Inputs (from state)

- `{{state.rows}}` (List<int>)

### Required output

Produce a JSON object with one key: `report` (churn_report).

Respond with **only** the JSON object — no prose, no markdown fences.
