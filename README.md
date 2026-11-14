# College Assistant

A small Streamlit demo with two halves that share one live SQLite database:

1. **A UI to manage college data** - add records, browse them, delete them.
2. **An AI agent** that answers questions about that same database using tools, so students
   can watch the agent write SQL and read the results.

There is no build step and no external service other than the Groq API.

---

## START HERE - the 3-step version

Send the whole folder to the other person. They only need this:

### Step 1 - Install Python (one time)

Download Python 3.10 or newer from https://www.python.org/downloads/ and **tick
"Add Python to PATH"** in the installer. Nothing else to install.

Check it worked - open a terminal and run:

```bash
python --version        # Windows
python3 --version       # macOS / Linux
```

### Step 2 - Get a free Groq API key (one time)

1. Sign up at https://console.groq.com/keys
2. Click **Create API Key** and copy the key (it starts with `gsk_`).

### Step 3 - Run the start script

**Windows** - double click **`start.bat`**

**macOS / Linux** - open a terminal in the folder and run:

```bash
./start.sh
```

That is all. The script creates the virtual environment, installs the three packages,
picks a free port and opens the app. First run takes a minute or two; after that it
takes about 3 seconds.

When it prints the URL, paste it into the browser (usually <http://localhost:8501>).
The first run also creates a `.env` file - paste the Groq key next to `GROQ_API_KEY=`
inside it, save, and run the start script again to switch the AI agent on.

To stop the server, close the terminal window (or press `Ctrl+C` in it).
To start it again, just run the start script again. **Nothing breaks, nothing is lost** -
your data stays in `college.db` and the packages stay in `.venv`.

> **Send these 7 files:** `app.py`, `requirements.txt`, `README.md`, `start.bat`,
> `start.sh`, `.env.example`, `.gitignore`
> Do **not** send `.env` (it holds your private key) or `.venv` / `college.db`
> (they are rebuilt automatically). Zip the folder with "Send to compressed file" and
> remember that zip tools sometimes skip dotfiles - check that `.env.example` is inside
> before sending.

---

## 1. What you need

- Python 3.10 or newer
- A free Groq API key from https://console.groq.com/keys

Only three packages are used: `streamlit`, `groq`, `pandas`.

---

## 2. Setup by hand (only if you prefer, or if the script is blocked)

The start script does all of the following for you. Use these steps manually if you
would rather see what is happening, or if your organisation blocks batch/shell scripts.

### Create a virtual environment

**Windows (PowerShell)**

```powershell
cd path\to\College-Assistant
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
cd path/to/College-Assistant
python3 -m venv venv
source venv/bin/activate
```

> If PowerShell blocks the activate script, run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once.

### Install the dependencies

```bash
pip install -r requirements.txt
```

### Set your API key

The key and the model are read from environment variables only. Easiest option - copy
`.env.example` to `.env` and fill it in (the app reads `.env` at startup on its own):

```bash
copy .env.example .env        # Windows
cp .env.example .env          # macOS / Linux
```

```
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-120b
```

Or set them in the shell:

**Windows (PowerShell)**

```powershell
$env:GROQ_API_KEY = "gsk_your_key_here"
$env:GROQ_MODEL   = "openai/gpt-oss-120b"
```

**macOS / Linux**

```bash
export GROQ_API_KEY="gsk_your_key_here"
export GROQ_MODEL="openai/gpt-oss-120b"
```

> A real shell variable always wins over `.env`, handy for switching keys without editing files.

### Run it

```bash
streamlit run app.py
```

The app opens at http://localhost:8501. On the first run it creates `college.db` and fills it
with fixed demo data, so there is nothing else to do.

**Without a key the app still works.** The Add Data and View Data tabs are fully functional and
the Ask tab shows a short message explaining how to set the key.

### What the start script does for you

| Step | First run | Every run after that |
| --- | --- | --- |
| Find Python 3.10+ | check | check |
| Create `.venv` | create it | reuse it |
| Install `requirements.txt` | install (~1 min) | skipped unless `requirements.txt` changed |
| Create `.env` from `.env.example` | create it | left alone |
| Pick a port | 8501, or the next free one | 8501, or the next free one |
| Start the server | start | start |

It is safe to run the script twice: the second run reuses the environment, skips the install,
and if a server is still holding the port it moves to the next free port automatically instead
of crashing. Pressing `Ctrl+C` or closing the window stops the server and leaves your data
in `college.db` intact.

---

## 3. The three tabs

### Add Data

Pick a table, fill the form, press **Add record**.

- Foreign keys are dropdowns that show names, not IDs - for example
  `CSE (Dr. Rajesh Kumar)` or `Ritika Bansal (ritika.bansal@college.edu)`.
- Everything is validated before it is saved: required fields, unique email, age 16-70,
  year 1-4, credits 1-10, and `amount_paid <= amount_total`.
- Errors come back as a plain sentence such as *"That email is already in use."* - never a
  stack trace.

### View Data

Pick a table to see a dataframe and a row count. Names are joined in, so you see
`CSE` instead of `department_id = 1`, and the fees table gets a **balance** column
(`amount_total - amount_paid`) and a **Paid / Pending** status.

To delete, choose the row ID, tick the confirmation box, then press **Delete row**.
Deleting a record that other records still point at is blocked, with a message that
explains which records to remove first.

### Ask the Database

A Groq tool-calling agent with exactly two tools:

| Tool | What it does |
| --- | --- |
| `get_schema()` | Returns the live table and column definitions with row counts. |
| `run_sql(query)` | Runs **one** read-only `SELECT`/`WITH` query, returns at most 50 rows. |

Under the hood, `run_sql` is locked down in three layers:

- the query must start with `SELECT` or `WITH`;
- `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `PRAGMA`, `VACUUM`
  and friends are rejected (comments and string literals are stripped first, so they
  cannot be used to smuggle a second statement past the check);
- the query runs on a separate **read-only** SQLite connection opened with the
  `file:...?mode=ro` URI, so even a bug cannot modify the data.

If a query is unsafe or fails, the error text is handed **back to the agent** as the tool
result so it can correct itself and try again. The loop stops when the model answers
without a tool call, or after 5 steps.

Expand **Agent steps** under any answer to see exactly which tool ran, which SQL was used
and how many rows came back.

Data added through the UI is visible to the agent immediately - there is no caching on
database reads anywhere in the app.

---

## 4. Demo script (5 minutes)

**0:00 - Start cold, to show there is no setup.**
Delete `college.db` (or press **Reset demo data**), then run `streamlit run app.py`.
Point out that the file is recreated and filled automatically: 5 departments, 40 students,
10 faculty, 12 courses, 80 enrollments, and a fees row for every student.

**0:45 - Tab 1: add a student.**
Table = `students`. Fill the form, pick CSE from the Department dropdown, press **Add record**.
Green banner confirms the save. The dropdown list now contains the new student - proof the
write went through.

**1:15 - Tab 3: ask the pre-seeded question.**
Click the sidebar button **"How many students are in CSE?"**. Answer: 8.
Open **Agent steps**: the agent called `get_schema`, then `run_sql` with a `COUNT(*)` join
across two tables. That is the whole point of the demo - the number came from the database,
not from the model.

**2:00 - Tab 2: show the data and the guardrails.**
View `fees` - the **balance** and **Pending** columns are computed live.
Try deleting a student who still has enrollments: it is refused with a friendly explanation.

**2:45 - Close the loop: add a student, then re-ask.**
Go back to Add Data, add one more CSE student. Ask **"How many students are in CSE?"** again.
The answer is now 9. The agent sees UI writes with zero delay. This is the moment that
usually convinces the room.

**3:30 - Make the agent look smart.**
Ask: *"Which faculty teaches the most courses?"* - it groups courses per faculty.
Then ask: *"What is the weather in Hyderabad today?"* - it politely declines, because the
question is not about college data.

**4:15 - Show the read-only guardrail.**
Ask it to change a record, e.g. *"Delete all the students."* The agent tries a write, the
tool returns *"only read-only SELECT or WITH queries are allowed"*, the agent gives up
politely, and **Agent steps** shows exactly what was blocked. The data is untouched.

**4:45 - Resilience, if there is time.**
- Ask a question that needs two steps, e.g. *"How many female students are in 3rd year?"*
- Press **Reset demo data** to prove the demo can be repeated all day.

**Backup answers if a question stalls** - press **Reset demo data** first, then the fixed
seed data guarantees these:

| Question | Answer |
| --- | --- |
| How many students are in CSE? | 8 |
| How many female students are in 3rd year? | 1 |
| How many students are from Hyderabad? | 9 |
| How many students have pending fees? | 27 |
| Which faculty teaches the most courses? | Dr. Rajesh Kumar, 3 courses |
| Which course is taught by Prof. Kavitha Reddy? | Python Programming |

> These numbers assume the freshly seeded database. If you have been adding students during a
> rehearsal, press **Reset demo data** (or add one more CSE student and say the count is 9) so
> the number on screen matches what you announce.

---

## 5. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Windows says "cannot be run because it is not a recognized command" for `python` | Python is not on PATH. Reinstall it and tick "Add Python to PATH". |
| PowerShell blocks `start.bat` / `Activate.ps1` | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once, then start again. |
| `start.bat` reports "No free port found" | Close other Streamlit apps, or run `start.bat 8600` to pick a specific port. |
| *"GROQ_API_KEY is not set"* on the Ask tab | Paste the key into the `.env` file, save, and run the start script again. The other tabs work regardless. |
| *"The AI service is busy right now (rate limit reached)"* | Groq free-tier limit. The app already retried once; wait a few seconds and ask again. |
| *"Could not reach the AI service (network problem)"* | Check the internet connection, then ask again. |
| *"The GROQ_API_KEY looks invalid"* | The key is wrong or revoked. Replace it and restart. |
| *"Sorry, I couldn't answer that. Try rephrasing."* | The agent used all 5 steps without an answer - usually a question that needs a join the model could not get right first time. |
| Database looks wrong | Press **Reset demo data** in the sidebar. |
| Port 8501 already in use | The start script already handles this by using the next free port. Manually: `streamlit run app.py --server.port 8502` |
| Want a different model | `export GROQ_MODEL="llama-3.3-70b-versatile"` |

---

## 6. Files

```
app.py            the whole app: config, database, seed data, tools, agent, UI
start.bat         one click start (Windows) - venv, install, free port, run
start.sh          the same for macOS / Linux
requirements.txt  pinned versions of streamlit, groq, pandas
.env.example      template for GROQ_API_KEY and GROQ_MODEL
.gitignore        ignores .env, college.db, venv, .venv, __pycache__
README.md         this file
.env              created on first run, holds your key (git-ignored)
.venv             created on first run (git-ignored)
college.db        created automatically on first run (git-ignored)
```

`.env`, `.venv` and `college.db` are git-ignored, so no key, no environment and no demo
data is ever committed or needs to be shared.
