# Incident Response Agent

This project is an on-call incident-response assistant. An engineer enters a production alert, and the agent searches Hindsight for similar past incidents before recommending a likely cause and fix. Engineers can record whether the suggestion worked. That feedback is saved to memory and can inform later responses.

**Memory is the core of this project** — the comparison view exists specifically to make Hindsight's effect on the agent's answers visible.

## Architecture

```text
Streamlit UI (app.py) -> agent.py -> Groq LLM
                             |
                             v
                         memory.py -> Hindsight Cloud
```

The UI compares two answers for the same alert. One uses only the alert text. The other asks Hindsight for related incident records, then gives those records to the model as evidence. The primary Groq model is `openai/gpt-oss-120b`; if repeated requests fail, the agent falls back to `openai/gpt-oss-20b`.

## How memory is used

- **Seeding:** `load_memory.py` reads the 15 incidents in `seed_data.py` and calls `memory.retain_incident()` for each. That helper stores each incident in the Hindsight bank named `incidents` using `Hindsight.retain()`. Stable incident IDs make re-running the loader replace the corresponding records.
- **Before a memory-informed suggestion:** `agent.py` calls `memory.recall_similar()` with the alert. That helper calls `Hindsight.recall()` in the `incidents` bank. The relevant returned records are included in the model prompt. The without-memory suggestion receives only the alert text.
- **When an engineer submits feedback:** `app.py` calls `memory.retain_outcome()` after the engineer selects whether the fix worked. That helper calls `Hindsight.retain()` to save the alert, service and title, outcome, notes, and resolution time. The saved feedback can be recalled for a later similar alert. Repeating an outcome with the same alert and note is skipped.

## How memory changes the answer

Without memory, the agent can only make a general assessment from the alert in front of it. It does not know this application's past incidents or which fixes engineers tried.

With memory, the agent can cite relevant past incident evidence, including known fixes, failed approaches, and engineer feedback. It weighs recent feedback when it matches the alert and leaves out weak matches. The evidence section is limited to three items; the expander shows the recalled records. Confidence reflects how closely the evidence matches.

## Requirements

- Python 3.10 or newer
- A Groq API key
- A Hindsight Cloud API endpoint and API key

## Setup and run

Run these commands from the project folder. On Windows PowerShell, create and activate a virtual environment first if you do not already have one:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the dependencies:

```powershell
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then fill in the credentials and Hindsight Cloud API endpoint:

```powershell
Copy-Item .env.example .env
```

Set these values in `.env`:

```env
HINDSIGHT_URL=https://your-hindsight-cloud-api-url
HINDSIGHT_API_KEY=your-hindsight-api-key
GROQ_API_KEY=your-groq-api-key
```

Load the 15 sample incidents into Hindsight, then start the app:

```powershell
python load_memory.py
streamlit run app.py
```

To clear the incidents bank and load exactly the 15 seed incidents again, stop the app and run:

```powershell
python reset_memory.py
```

The reset removes all records in the `incidents` bank before loading the sample incidents. Start the app again with `streamlit run app.py`.

## Demo flow

1. Choose a sample alert or paste an alert into the text area.
2. Select **Compare suggestions** to see the generic answer beside the memory-informed answer.
3. Review the evidence and expand **Recalled past incidents** for the records returned by Hindsight.
4. Enter the time to resolve, optionally add notes, then select **Fix worked** or **Fix did not work**.
5. Submit a similar alert to see whether Hindsight recalls the newly saved outcome.

## Tech stack

- Python
- Streamlit
- Groq with `openai/gpt-oss-120b` as the primary model
- Hindsight Cloud via the `hindsight-client` Python package
