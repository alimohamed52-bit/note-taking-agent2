# Run sheet — every command

All commands run from the project root: `c:\Users\khale\Desktop\Ali-Ai-Projecy`

---

## 0. One-time setup

```powershell
# create + activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1
#   (Command Prompt instead of PowerShell:  .venv\Scripts\activate.bat)
#   (macOS / Linux:                          source .venv/bin/activate)

# install dependencies
pip install -r requirements.txt

# create your .env with the Groq API key
Copy-Item .env.example .env
notepad .env        # set  GROQ_API_KEY=gsk_...
```

Every new terminal: `cd` to the project and run `.venv\Scripts\Activate.ps1` again.

---

## 1. Website (browser chat + live notes panel)  ← main one

```powershell
python -m note_agent.web
```

Then open: **http://127.0.0.1:5000**

Options:

```powershell
python -m note_agent.web --port 8000          # different port
python -m note_agent.web --db demo.db          # separate database file
python -m note_agent.web --user alice          # switch user (note isolation)
python -m note_agent.web --host 0.0.0.0        # reach it from another device on your wifi
```

Extra pages while the server runs:
- `http://127.0.0.1:5000/note/1`      one note in full
- `http://127.0.0.1:5000/api/notes`   all notes as JSON

Stop the server: `Ctrl + C`

---

## 2. Terminal chat (CLI)

```powershell
python -m note_agent.cli
```

Options: `--user alice`   `--db demo.db`   `--model openai/gpt-oss-120b`
In-chat commands: `/notes`  `/reset`  `/quit`

---

## 3. Streamlit chat (alternative browser UI)

```powershell
streamlit run streamlit_app.py
```

Opens automatically at http://localhost:8501

---

## 4. Evaluation harness (15 scenarios, pass/fail)

```powershell
python -m eval.run
```

Options:

```powershell
python -m eval.run --only delete               # run a subset by name
python -m eval.run --model qwen/qwen3.8-27b     # try another model
```

Writes a full trace to `eval\report.json`.

---

## 5. Offline unit tests (no API key needed)

```powershell
python tests\test_storage_and_tools.py
```

---

## 6. See which Groq models your key can use

```powershell
python -m note_agent.list_models
```

Set one with `GROQ_MODEL=<id>` in `.env`, or pass `--model` to the CLI / eval.

---

## Quick reference

| I want to... | Command | URL |
|---|---|---|
| Chat in the browser | `python -m note_agent.web` | http://127.0.0.1:5000 |
| Chat in the terminal | `python -m note_agent.cli` | — |
| Chat via Streamlit | `streamlit run streamlit_app.py` | http://localhost:8501 |
| Run the eval suite | `python -m eval.run` | — |
| Run unit tests | `python tests\test_storage_and_tools.py` | — |
| List available models | `python -m note_agent.list_models` | — |
