"""
College Assistant - a small Streamlit demo.

Two things live in this single app:
  1. A UI to manage a college database (Add Data / View Data tabs).
  2. An AI agent (Groq tool-calling) that answers questions about the same
     live SQLite database.

Nothing here is cached: every read hits college.db, so a row added through
the UI is visible to the agent on the very next question.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# --------------------------------------------------------------------------- #
# Configuration - API key and model come from environment variables ONLY.
# --------------------------------------------------------------------------- #

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "college.db"
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_ROWS = 50          # hard cap on rows a tool call may return
MAX_AGENT_STEPS = 5    # hard cap on tool-calling rounds
TABLE_ORDER = ["departments", "students", "faculty", "courses", "enrollments", "fees"]

EXAMPLE_QUESTIONS = [
    "How many students are in CSE?",
    "How many female students are in 3rd year?",
    "Which students have pending fees?",
    "Which faculty teaches the most courses?",
    "How many students are from Hyderabad?",
]


def load_env_file() -> None:
    """Load .env into os.environ without adding a dependency.

    Real environment variables always win, so exporting a key in the shell
    overrides whatever is written in .env.
    """
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        # A broken .env must never stop the app from starting.
        pass


load_env_file()

API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
MODEL_NAME = os.environ.get("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

# --------------------------------------------------------------------------- #
# Database layer
# --------------------------------------------------------------------------- #

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS departments (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT    NOT NULL UNIQUE,
        hod_name TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS students (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        name           TEXT    NOT NULL,
        email          TEXT    NOT NULL UNIQUE,
        gender         TEXT    NOT NULL CHECK (gender IN ('Male', 'Female', 'Other')),
        age            INTEGER NOT NULL CHECK (age BETWEEN 16 AND 70),
        year           INTEGER NOT NULL CHECK (year BETWEEN 1 AND 4),
        department_id  INTEGER NOT NULL REFERENCES departments(id),
        city           TEXT    NOT NULL,
        admission_date TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faculty (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT    NOT NULL,
        email         TEXT    NOT NULL UNIQUE,
        designation   TEXT    NOT NULL,
        department_id INTEGER NOT NULL REFERENCES departments(id),
        joining_date  TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS courses (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        title         TEXT    NOT NULL UNIQUE,
        credits       INTEGER NOT NULL CHECK (credits BETWEEN 1 AND 10),
        department_id INTEGER NOT NULL REFERENCES departments(id),
        faculty_id    INTEGER REFERENCES faculty(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS enrollments (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL REFERENCES students(id),
        course_id  INTEGER NOT NULL REFERENCES courses(id),
        grade      TEXT    CHECK (grade IS NULL OR grade IN ('A', 'B', 'C')),
        UNIQUE (student_id, course_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fees (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id   INTEGER NOT NULL REFERENCES students(id),
        amount_total INTEGER NOT NULL CHECK (amount_total >= 0),
        amount_paid  INTEGER NOT NULL DEFAULT 0 CHECK (amount_paid >= 0 AND amount_paid <= amount_total),
        due_date     TEXT    NOT NULL
    )
    """,
]


@contextmanager
def db_conn():
    """Yield a read/write connection with foreign keys enforced."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def ro_conn():
    """Yield a read-only connection (sqlite URI mode=ro)."""
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create every table if it is missing. Safe to call on every start."""
    with db_conn() as conn:
        for statement in SCHEMA_SQL:
            conn.execute(statement)


def table_count(conn: sqlite3.Connection, table: str) -> int:
    """Row count for one table."""
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def database_is_empty() -> bool:
    """True when no table has any row yet (i.e. seeding is needed)."""
    try:
        with db_conn() as conn:
            return all(table_count(conn, t) == 0 for t in TABLE_ORDER)
    except sqlite3.Error:
        return True


# --------------------------------------------------------------------------- #
# Seed data - fixed, non-random, realistic Indian college data
# --------------------------------------------------------------------------- #

SEED_DEPARTMENTS = [
    (1, "CSE",       "Dr. Rajesh Kumar"),
    (2, "ECE",       "Dr. Anitha Reddy"),
    (3, "Mechanical", "Dr. Suresh Babu"),
    (4, "Civil",     "Dr. Priya Sharma"),
    (5, "MBA",       "Dr. Mohan Rao"),
]

SEED_FACULTY = [
    (1, "Dr. Rajesh Kumar",   "rajesh.kumar@college.edu",   "Professor",            1, "2012-06-04"),
    (2, "Dr. Anitha Reddy",   "anitha.reddy@college.edu",   "Associate Professor",  2, "2014-07-15"),
    (3, "Dr. Suresh Babu",    "suresh.babu@college.edu",    "Professor",            3, "2011-08-01"),
    (4, "Dr. Priya Sharma",   "priya.sharma@college.edu",   "Professor",            4, "2013-06-17"),
    (5, "Dr. Mohan Rao",      "mohan.rao@college.edu",      "Professor",            5, "2015-07-20"),
    (6, "Prof. Kavitha Reddy", "kavitha.reddy@college.edu", "Assistant Professor",  1, "2018-08-06"),
    (7, "Prof. Rahul Verma",  "rahul.verma@college.edu",    "Assistant Professor",  1, "2019-07-15"),
    (8, "Prof. Sneha Iyer",   "sneha.iyer@college.edu",     "Associate Professor",  2, "2017-06-12"),
    (9, "Prof. Arun Kumar",   "arun.kumar@college.edu",     "Assistant Professor",  3, "2020-08-03"),
    (10, "Prof. Divya Sri",  "divya.sri@college.edu",      "Associate Professor",  4, "2016-07-11"),
]

SEED_COURSES = [
    (1,  "Python Programming",        4, 1, 6),
    (2,  "Data Structures",           4, 1, 1),
    (3,  "Database Management Systems", 3, 1, 1),
    (4,  "Compiler Design",           3, 1, 1),
    (5,  "Digital Electronics",       4, 2, 2),
    (6,  "Signal Processing",         3, 2, 8),
    (7,  "VLSI Design",               3, 2, 2),
    (8,  "Thermodynamics",            4, 3, 3),
    (9,  "Fluid Mechanics",           4, 3, 9),
    (10, "Structural Engineering",    4, 4, 4),
    (11, "Business Analytics",        3, 5, 5),
    (12, "Financial Management",      4, 5, 5),
]

# (id, name, email, gender, age, year, department_id, city, admission_date)
SEED_STUDENTS = [
    (1,  "Aarav Sharma",      "aarav.sharma@college.edu",      "Male",   19, 1, 1, "Hyderabad",   "2024-06-15"),
    (2,  "Diya Patel",        "diya.patel@college.edu",        "Female", 20, 2, 1, "Vijayawada",  "2023-07-01"),
    (3,  "Rohan Mehta",       "rohan.mehta@college.edu",       "Male",   21, 3, 1, "Chennai",     "2022-06-20"),
    (4,  "Ananya Iyer",       "ananya.iyer@college.edu",       "Female", 22, 4, 1, "Bengaluru",   "2021-07-05"),
    (5,  "Karthik Reddy",     "karthik.reddy@college.edu",     "Male",   19, 1, 1, "Hyderabad",   "2024-06-18"),
    (6,  "Meera Nair",        "meera.nair@college.edu",        "Female", 20, 2, 1, "Chennai",     "2023-06-25"),
    (7,  "Vikram Singh",      "vikram.singh@college.edu",      "Male",   22, 3, 1, "Pune",        "2022-07-02"),
    (8,  "Sanjana Rao",       "sanjana.rao@college.edu",       "Female", 21, 4, 1, "Vijayawada",  "2021-06-30"),
    (9,  "Rahul Joshi",       "rahul.joshi@college.edu",       "Male",   20, 2, 2, "Hyderabad",   "2023-07-10"),
    (10, "Nandini Reddy",     "nandini.reddy@college.edu",     "Female", 19, 1, 2, "Vijayawada",  "2024-06-20"),
    (11, "Arjun Prasad",      "arjun.prasad@college.edu",      "Male",   21, 3, 2, "Bengaluru",   "2022-06-28"),
    (12, "Lakshmi Prasad",    "lakshmi.prasad@college.edu",    "Female", 22, 4, 2, "Chennai",     "2021-07-12"),
    (13, "Manish Kumar",      "manish.kumar@college.edu",      "Male",   20, 2, 2, "Pune",        "2023-07-05"),
    (14, "Deepa Venkat",      "deepa.venkat@college.edu",      "Female", 19, 1, 2, "Hyderabad",   "2024-06-22"),
    (15, "Suresh Gowd",       "suresh.gowd@college.edu",       "Male",   22, 3, 2, "Vijayawada",  "2022-07-08"),
    (16, "Anjali Deshmukh",   "anjali.deshmukh@college.edu",   "Female", 21, 4, 2, "Bengaluru",   "2021-06-25"),
    (17, "Imran Sheikh",      "imran.sheikh@college.edu",      "Male",   19, 1, 3, "Pune",        "2024-07-01"),
    (18, "Pooja Menon",       "pooja.menon@college.edu",       "Female", 20, 2, 3, "Chennai",     "2023-06-26"),
    (19, "Nikhil Jain",       "nikhil.jain@college.edu",       "Male",   21, 3, 3, "Hyderabad",   "2022-07-04"),
    (20, "Ritika Bansal",     "ritika.bansal@college.edu",     "Female", 22, 4, 3, "Bengaluru",   "2021-07-18"),
    (21, "Aditya Naik",       "aditya.naik@college.edu",       "Male",   20, 2, 3, "Vijayawada",  "2023-07-11"),
    (22, "Swathi Rao",       "swathi.rao@college.edu",        "Female", 19, 1, 3, "Chennai",     "2024-06-19"),
    (23, "Ganesh Pillai",     "ganesh.pillai@college.edu",     "Male",   22, 3, 3, "Pune",        "2022-06-30"),
    (24, "Bhavana Joshi",     "bhavana.joshi@college.edu",     "Female", 21, 4, 3, "Hyderabad",   "2021-07-06"),
    (25, "Harsh Vardhan",     "harsh.vardhan@college.edu",     "Male",   20, 2, 4, "Vijayawada",  "2023-06-24"),
    (26, "Sneha Kulkarni",    "sneha.kulkarni@college.edu",    "Female", 19, 1, 4, "Pune",        "2024-06-21"),
    (27, "Abhishek Dutta",    "abhishek.dutta@college.edu",    "Male",   21, 3, 4, "Chennai",     "2022-07-09"),
    (28, "Nandita Sahu",      "nandita.sahu@college.edu",      "Female", 22, 4, 4, "Hyderabad",   "2021-07-02"),
    (29, "Prashant Rao",      "prashant.rao@college.edu",      "Male",   20, 2, 4, "Bengaluru",   "2023-07-15"),
    (30, "Kirti Jain",        "kirti.jain@college.edu",        "Female", 19, 1, 4, "Vijayawada",  "2024-06-27"),
    (31, "Sameer Khan",       "sameer.khan@college.edu",       "Male",   22, 3, 4, "Chennai",     "2022-06-29"),
    (32, "Aishwarya Pillai",  "aishwarya.pillai@college.edu",  "Female", 21, 4, 4, "Bengaluru",   "2021-07-20"),
    (33, "Varun Trivedi",     "varun.trivedi@college.edu",     "Male",   22, 1, 5, "Bengaluru",   "2024-07-05"),
    (34, "Riya Malhotra",     "riya.malhotra@college.edu",     "Female", 21, 1, 5, "Hyderabad",   "2024-07-08"),
    (35, "Adarsh Chauhan",    "adarsh.chauhan@college.edu",    "Male",   23, 2, 5, "Pune",        "2023-07-20"),
    (36, "Ishita Grover",     "ishita.grover@college.edu",     "Female", 23, 2, 5, "Chennai",     "2023-07-22"),
    (37, "Kunal Saxena",      "kunal.saxena@college.edu",      "Male",   24, 3, 5, "Vijayawada",  "2022-08-01"),
    (38, "Shreya Bose",       "shreya.bose@college.edu",       "Female", 24, 3, 5, "Bengaluru",   "2022-08-05"),
    (39, "Tarun Jain",        "tarun.jain@college.edu",        "Male",   22, 4, 5, "Hyderabad",   "2021-08-10"),
    (40, "Kavya Reddy",       "kavya.reddy@college.edu",       "Female", 22, 4, 5, "Chennai",     "2021-08-12"),
]

SEED_DUE_DATES = ["2025-07-31", "2025-08-31", "2025-09-30", "2025-10-31", "2025-11-30"]
GRADE_CYCLE = ["A", "B", "A", "C", "B", None]


def seed_db() -> None:
    """Insert the fixed demo dataset. Runs only when the tables are empty."""
    # Enrollments: every student takes 2 courses, picked by a fixed rotation, so
    # we always get 80 rows (and never a duplicate course per student).
    enrollments: List[Tuple[int, int, Optional[str]]] = []
    for student_id, _name, _email, _g, _a, _y, _dept, _city, _adm in SEED_STUDENTS:
        course_a = ((student_id - 1) % 12) + 1
        course_b = (student_id % 12) + 1
        if course_a == course_b:
            course_b = (course_b % 12) + 1
        enrollments.append((student_id, course_a, GRADE_CYCLE[(student_id + 0) % len(GRADE_CYCLE)]))
        enrollments.append((student_id, course_b, GRADE_CYCLE[(student_id + 1) % len(GRADE_CYCLE)]))

    # Fees for every student: a third fully paid, a third unpaid, a third half paid.
    fees: List[Tuple[int, int, int, str]] = []
    for student_id, *_rest in SEED_STUDENTS:
        total = 48000 + (student_id % 5) * 6000
        bucket = student_id % 3
        paid = total if bucket == 0 else (0 if bucket == 1 else total // 2)
        fees.append((student_id, total, paid, SEED_DUE_DATES[student_id % 5]))

    with db_conn() as conn:
        conn.executemany(
            "INSERT INTO departments (id, name, hod_name) VALUES (?, ?, ?)", SEED_DEPARTMENTS
        )
        conn.executemany(
            "INSERT INTO faculty (id, name, email, designation, department_id, joining_date)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            SEED_FACULTY,
        )
        conn.executemany(
            "INSERT INTO students (id, name, email, gender, age, year, department_id, city, admission_date)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            SEED_STUDENTS,
        )
        conn.executemany(
            "INSERT INTO courses (id, title, credits, department_id, faculty_id) VALUES (?, ?, ?, ?, ?)",
            SEED_COURSES,
        )
        conn.executemany(
            "INSERT INTO enrollments (student_id, course_id, grade) VALUES (?, ?, ?)", enrollments
        )
        conn.executemany(
            "INSERT INTO fees (student_id, amount_total, amount_paid, due_date) VALUES (?, ?, ?, ?)",
            fees,
        )


def reset_db() -> None:
    """Drop every table (children first) so the demo can start over."""
    with db_conn() as conn:
        for table in ["enrollments", "fees", "courses", "faculty", "students", "departments"]:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db()
    seed_db()


def bootstrap_db() -> None:
    """Called on every app start: create tables, seed when empty."""
    init_db()
    if database_is_empty():
        seed_db()


# --------------------------------------------------------------------------- #
# Agent tools - get_schema() and run_sql()
# --------------------------------------------------------------------------- #

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": (
                "Return the live table and column definitions of the college database "
                "(departments, students, faculty, courses, enrollments, fees) with row counts. "
                "Call this first whenever you are unsure about table or column names."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Run ONE read-only query against the college database and return at most 50 rows. "
                "Only a single SELECT or WITH statement is allowed. "
                "Text comparisons should use LOWER(col) = 'value' or LOWER(col) LIKE '%value%'. "
                "Use COUNT(*) with GROUP BY for 'how many' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A single read-only SELECT/WITH statement.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]

FORBIDDEN_SQL = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH",
    "PRAGMA", "VACUUM", "REPLACE", "TRIGGER", "GRANT", "REVOKE", "BEGIN", "COMMIT",
    "ROLLBACK", "REINDEX", "ANALYZE", "SAVEPOINT", "SQLITE_MASTER", "SQLITE_TEMP",
]


def get_schema() -> str:
    """Build a live schema description for the agent, with row counts."""
    parts: List[str] = []
    try:
        with db_conn() as conn:
            for table in TABLE_ORDER:
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table,)
                ).fetchone()
                if row is None:
                    continue
                create_sql = " ".join(str(row["sql"]).split())
                parts.append(f"TABLE {table} ({table_count(conn, table)} rows)\n{create_sql}")
    except sqlite3.Error as exc:
        return f"ERROR reading schema: {exc}"
    if not parts:
        return "ERROR: the database has no tables yet."
    return "\n\n".join(parts)


def _strip_sql_noise(sql: str) -> str:
    """Remove comments and string literals so keyword checks cannot be bypassed."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:''|[^'])*'", " ", sql)
    sql = re.sub(r'"(?:""|[^"])*"', " ", sql)
    return sql


def check_sql_is_safe(sql: str) -> Optional[str]:
    """Return an error message if the SQL is not a single read-only query."""
    if not isinstance(sql, str) or not sql.strip():
        return "ERROR: empty query. Send one SELECT or WITH statement."

    cleaned = _strip_sql_noise(sql)
    body = cleaned.strip()
    # A single trailing semicolon is fine, anything else means several statements.
    if body.endswith(";"):
        body = body[:-1].strip()
    if ";" in body:
        return "ERROR: only ONE statement is allowed. Remove the extra semicolons."

    upper = body.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return "ERROR: only read-only SELECT or WITH queries are allowed."

    for word in FORBIDDEN_SQL:
        if re.search(rf"\b{word}\b", upper):
            return (
                f"ERROR: the keyword {word} is not allowed. "
                "This database is read-only for you - use a single SELECT or WITH statement."
            )
    return None


def run_safe_query(sql: str) -> Tuple[str, Dict[str, Any]]:
    """Run one read-only SELECT/WITH query and return (text_for_model, meta).

    Problems are returned as text (never raised) so the agent can read the
    error and fix its own SQL on the next step.
    """
    problem = check_sql_is_safe(sql)
    if problem:
        return problem, {"row_count": 0, "truncated": False, "error": True}

    meta: Dict[str, Any] = {"row_count": 0, "truncated": False, "error": False}
    try:
        with ro_conn() as conn:
            cursor = conn.execute(sql)
            rows = cursor.fetchmany(MAX_ROWS + 1)
            columns = [d[0] for d in (cursor.description or [])]
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():  # rare contention, try once more
            time.sleep(0.4)
            try:
                with ro_conn() as conn:
                    cursor = conn.execute(sql)
                    rows = cursor.fetchmany(MAX_ROWS + 1)
                    columns = [d[0] for d in (cursor.description or [])]
            except sqlite3.Error as exc2:
                return (
                    f"ERROR running the query: {exc2}. "
                    "Check the table and column names, then try a corrected query.",
                    {"row_count": 0, "truncated": False, "error": True},
                )
        else:
            return (
                f"ERROR running the query: {exc}. "
                "Check the table and column names, then try a corrected query.",
                {"row_count": 0, "truncated": False, "error": True},
            )
    except sqlite3.Error as exc:
        return (
            f"ERROR running the query: {exc}. Try a corrected query.",
            {"row_count": 0, "truncated": False, "error": True},
        )

    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    meta["row_count"] = len(rows)
    meta["truncated"] = truncated

    if not rows:
        return "0 rows returned. (Columns: " + ", ".join(columns) + ")", meta

    def cell(value: Any) -> str:
        text = "NULL" if value is None else str(value)
        return text if len(text) <= 120 else text[:117] + "..."

    lines = [f"{len(rows)} row(s) returned.", "columns: " + " | ".join(columns)]
    lines.extend(" | ".join(cell(row[idx]) for idx in range(len(columns))) for row in rows)
    if truncated:
        lines.append(f"(showing only the first {MAX_ROWS} rows)")
    return "\n".join(lines), meta


def call_tool(name: str, arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Dispatch a tool call from the model to the right function."""
    if name == "get_schema":
        text = get_schema()
        return text, {"row_count": 0, "truncated": False, "error": False}

    if name == "run_sql":
        sql = arguments.get("query") or ""
        return run_safe_query(sql)

    return (
        f"ERROR: unknown tool '{name}'. Use get_schema or run_sql.",
        {"row_count": 0, "truncated": False, "error": True},
    )


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are the College Assistant for a mid-size Indian engineering college.
You help students and staff by answering questions about this college's own database.

Rules:
- Use the tools to get every fact. Call get_schema first whenever you are unsure
  about a table or column name.
- Answer ONLY from what the tool results tell you. Never invent numbers, names or dates.
- Use run_sql for counting: COUNT(*) with GROUP BY for "how many" questions.
- For text matching use LOWER(col) = 'value' or LOWER(col) LIKE '%value%'.
- Only single read-only SELECT or WITH statements are allowed. If a query fails, read
  the error and send a corrected query.
- If a question is not about this college's data (departments, students, faculty,
  courses, enrollments, fees), politely refuse and offer what you can answer instead.
- If the answer is a long list, say how many rows there are, name only the first three
  or four, then say how many more there are. Copy names exactly as the tool returned them.
- Answer in 1 to 3 friendly sentences. No long reports, no markdown tables, no code blocks."""


@st.cache_resource(show_spinner=False)
def get_groq_client():
    """Create the Groq client once per process (not a database read)."""
    from groq import Groq

    return Groq(api_key=API_KEY)


def _call_chat(client, messages: List[Dict[str, Any]]) -> Tuple[Any, Optional[str]]:
    """Call the chat endpoint, retrying once on rate limit / network errors.

    Returns (response, None) on success or (None, friendly_error_text) on
    failure, so the caller never has to deal with an exception.
    """
    from groq import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0,
                max_tokens=1024,
            )
            return response, None
        except RateLimitError:
            if attempt == 0:
                time.sleep(4)
                continue
            return None, (
                "The AI service is busy right now (rate limit reached). "
                "Please wait a few seconds and ask again."
            )
        except (APITimeoutError, APIConnectionError, InternalServerError):
            if attempt == 0:
                time.sleep(2)
                continue
            return None, "Could not reach the AI service (network problem). Please try again."
        except Exception as exc:  # bad request, auth, unknown - never show a traceback
            name = type(exc).__name__
            if "Authentication" in name or "401" in str(exc):
                return None, "The GROQ_API_KEY looks invalid. Please check the key and restart."
            if "BadRequest" in name or "400" in str(exc):
                return None, "The AI service rejected the request. Please rephrase your question."
            return None, f"Something went wrong while talking to the AI service ({name}). Please try again."
    return None, "The AI service did not respond. Please try again."


def run_agent(question: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
    """Answer one question with a tool-calling loop.

    history: previous turns as {"role": "user"/"assistant", "content": str}.
    Returns {"answer": str, "steps": [...], "failed": bool}.
    """
    client = get_groq_client()

    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-6:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    steps: List[Dict[str, Any]] = []

    for _step in range(MAX_AGENT_STEPS):
        try:
            response, error = _call_chat(client, messages)
        except Exception:
            return {
                "answer": "Could not reach the AI service. Please try again.",
                "steps": steps,
                "failed": True,
            }
        if response is None:
            return {"answer": error, "steps": steps, "failed": True}

        message = response.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])

        if not tool_calls:
            answer = (getattr(message, "content", None) or "").strip()
            if not answer:
                answer = "Sorry, I couldn't answer that. Try rephrasing."
            return {"answer": answer, "steps": steps, "failed": False}

        # Record exactly what the assistant said so the API sees a valid history.
        assistant_turn: Dict[str, Any] = {
            "role": "assistant",
            "content": getattr(message, "content", None) or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments or "{}",
                    },
                }
                for call in tool_calls
            ],
        }
        # Reasoning models (the default gpt-oss model) expect their `reasoning`
        # text echoed back on the next request. Only sent when actually present,
        # so non-reasoning models never receive the field.
        reasoning = getattr(message, "reasoning", None)
        if reasoning:
            assistant_turn["reasoning"] = reasoning
        messages.append(assistant_turn)

        for call in tool_calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    arguments = {}
            except (json.JSONDecodeError, TypeError):
                arguments = {}

            sql = arguments.get("query", "")
            result, meta = call_tool(name, arguments)
            steps.append(
                {
                    "tool": name,
                    "sql": sql,
                    "rows": meta.get("row_count", 0),
                    "truncated": meta.get("truncated", False),
                    "error": meta.get("error", False),
                    "output": result,
                }
            )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return {
        "answer": "Sorry, I couldn't answer that. Try rephrasing.",
        "steps": steps,
        "failed": True,
    }


# --------------------------------------------------------------------------- #
# Generic insert forms (declarative, so all six tables behave the same)
# --------------------------------------------------------------------------- #

FORM_SPECS: Dict[str, Dict[str, Any]] = {
    "students": {
        "columns": ["name", "email", "gender", "age", "year", "department_id", "city", "admission_date"],
        "fields": [
            {"col": "name", "label": "Full name", "type": "text", "required": True},
            {"col": "email", "label": "Email", "type": "text", "required": True, "unique": True},
            {"col": "gender", "label": "Gender", "type": "choice",
             "options": ["Male", "Female", "Other"], "required": True},
            {"col": "age", "label": "Age", "type": "int", "min": 16, "max": 70, "value": 19, "required": True},
            {"col": "year", "label": "Year of study", "type": "choice_int",
             "options": [1, 2, 3, 4], "required": True},
            {"col": "department_id", "label": "Department", "type": "fk",
             "table": "departments", "fk_label": "name", "required": True},
            {"col": "city", "label": "City", "type": "text", "required": True},
            {"col": "admission_date", "label": "Admission date", "type": "date", "required": True},
        ],
    },
    "faculty": {
        "columns": ["name", "email", "designation", "department_id", "joining_date"],
        "fields": [
            {"col": "name", "label": "Full name", "type": "text", "required": True},
            {"col": "email", "label": "Email", "type": "text", "required": True, "unique": True},
            {"col": "designation", "label": "Designation", "type": "text", "required": True},
            {"col": "department_id", "label": "Department", "type": "fk",
             "table": "departments", "fk_label": "name", "required": True},
            {"col": "joining_date", "label": "Joining date", "type": "date", "required": True},
        ],
    },
    "courses": {
        "columns": ["title", "credits", "department_id", "faculty_id"],
        "fields": [
            {"col": "title", "label": "Course title", "type": "text", "required": True, "unique": True},
            {"col": "credits", "label": "Credits", "type": "int", "min": 1, "max": 10, "value": 4, "required": True},
            {"col": "department_id", "label": "Department", "type": "fk",
             "table": "departments", "fk_label": "name", "required": True},
            {"col": "faculty_id", "label": "Faculty", "type": "fk",
             "table": "faculty", "fk_label": "name", "required": False},
        ],
    },
    "enrollments": {
        "columns": ["student_id", "course_id", "grade"],
        "fields": [
            {"col": "student_id", "label": "Student", "type": "fk",
             "table": "students", "fk_label": "name", "required": True},
            {"col": "course_id", "label": "Course", "type": "fk",
             "table": "courses", "fk_label": "title", "required": True},
            {"col": "grade", "label": "Grade", "type": "choice",
             "options": ["(not graded yet)", "A", "B", "C"], "value": "(not graded yet)"},
        ],
    },
    "fees": {
        "columns": ["student_id", "amount_total", "amount_paid", "due_date"],
        "fields": [
            {"col": "student_id", "label": "Student", "type": "fk",
             "table": "students", "fk_label": "name", "required": True},
            {"col": "amount_total", "label": "Total amount (Rs)", "type": "int",
             "min": 0, "max": 10000000, "value": 60000, "required": True},
            {"col": "amount_paid", "label": "Amount paid (Rs)", "type": "int",
             "min": 0, "max": 10000000, "value": 0, "required": True},
            {"col": "due_date", "label": "Due date", "type": "date", "required": True},
        ],
    },
    "departments": {
        "columns": ["name", "hod_name"],
        "fields": [
            {"col": "name", "label": "Department name", "type": "text", "required": True, "unique": True},
            {"col": "hod_name", "label": "Head of department", "type": "text", "required": True},
        ],
    },
}

FK_DISPLAY = {
    "departments": 'name || " (" || hod_name || ")"',
    "students": 'name || " (" || email || ")"',
    "faculty": 'name || " (" || designation || ")"',
    "courses": 'title || " (" || credits || " credits)"',
}

PLACEHOLDER = "-- select --"


def fk_options(table: str) -> List[Tuple[str, int]]:
    """Return [(friendly label, id), ...] for a foreign-key dropdown."""
    if not table or table not in TABLE_ORDER:
        return []
    label_sql = FK_DISPLAY.get(table, "name")
    try:
        with db_conn() as conn:
            rows = conn.execute(
                f"SELECT id, {label_sql} AS label FROM {table} ORDER BY label"
            ).fetchall()
    except sqlite3.Error:
        return []
    return [(str(row["label"]), int(row["id"])) for row in rows]


def render_field(field: Dict[str, Any], prefix: str) -> Any:
    """Render one form widget and return its raw value."""
    key = f"{prefix}_{field['col']}"
    label = field["label"] + (" *" if field.get("required") else "")
    kind = field["type"]

    if kind == "text":
        return st.text_input(label, key=key)
    if kind == "int":
        return st.number_input(
            label, min_value=field["min"], max_value=field["max"],
            value=field.get("value", field["min"]), step=1, key=key,
        )
    if kind == "choice":
        return st.selectbox(label, field["options"], index=0, key=key)
    if kind == "choice_int":
        picked = st.selectbox(label, [str(o) for o in field["options"]], index=0, key=key)
        return int(picked)
    if kind == "date":
        return st.date_input(label, value=date.today(), key=key)
    if kind == "fk":
        options = fk_options(field.get("table", ""))
        labels = [label_text for label_text, _ in options] or ["(table is empty)"]
        picked = st.selectbox(label, [PLACEHOLDER] + labels, index=0, key=key)
        if picked == PLACEHOLDER:
            return None
        for label_text, ident in options:
            if label_text == picked:
                return ident
        return None
    return None


def validate_and_build(
    table: str, values: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Turn raw form values into a row dict, or return a friendly error."""
    spec = FORM_SPECS[table]
    row: Dict[str, Any] = {}

    for field in spec["fields"]:
        col = field["col"]
        value = values.get(col)
        kind = field["type"]

        if kind in ("text", "choice"):
            text = ("" if value is None else str(value)).strip()
            if not text and field.get("required"):
                return None, f"{field['label']} is required."
            if kind == "text":
                text = re.sub(r"\s+", " ", text)
            if field.get("unique") and text:
                try:
                    with db_conn() as conn:
                        hit = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE LOWER({col}) = LOWER(?)", (text,)
                        ).fetchone()[0]
                except sqlite3.Error as exc:
                    return None, f"Could not check the database: {exc}"
                if hit:
                    shown = col.replace("_", " ")
                    return None, f"That {shown} is already in use. Please enter a different one."
                if col == "email":
                    text = text.lower()
            row[col] = text or (None if kind == "choice" else "")
            continue

        if kind == "choice_int":
            try:
                number = int(value)
            except (TypeError, ValueError):
                return None, f"{field['label']} is required."
            if not (field["options"][0] <= number <= field["options"][-1]):
                return None, f"{field['label']} must be between {field['options'][0]} and {field['options'][-1]}."
            row[col] = number
            continue

        if kind == "int":
            if value is None:
                return None, f"{field['label']} is required."
            number = int(value)
            if number < field["min"] or number > field["max"]:
                return None, f"{field['label']} must be between {field['min']} and {field['max']}."
            row[col] = number
            continue

        if kind == "date":
            if not isinstance(value, (date, datetime)):
                return None, f"{field['label']} is required."
            row[col] = value.isoformat()
            continue

        if kind == "fk":
            if value is None:
                if field.get("required"):
                    return None, f"Please choose a {field['label']}."
                row[col] = None
            else:
                row[col] = int(value)
            continue

    # Cross-field rules that a single field cannot express.
    if table == "fees":
        if row["amount_paid"] > row["amount_total"]:
            return None, "Amount paid cannot be more than the total amount."
    if table == "enrollments" and row.get("grade") == "(not graded yet)":
        row["grade"] = None

    return row, None


def insert_row(table: str, row: Dict[str, Any]) -> Tuple[bool, str]:
    """Insert one row and turn SQLite errors into friendly messages."""
    spec = FORM_SPECS[table]
    columns = spec["columns"]
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    try:
        with db_conn() as conn:
            conn.execute(sql, tuple(row[c] for c in columns))
        return True, ""
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "UNIQUE" in message:
            return False, "That record already exists (duplicate value). Please check the fields."
        if "CHECK" in message:
            if table == "fees":
                return False, "Amount paid cannot be more than the total amount."
            return False, "A value is out of the allowed range for this field."
        if "FOREIGN KEY" in message:
            return False, "The selected department, student or course no longer exists. Please try again."
        return False, "That value is not allowed for this field."
    except sqlite3.Error as exc:
        return False, f"Could not save the record: {exc}"


def friendly_delete_error(message: str) -> str:
    """Turn a SQLite constraint message into something a human can read."""
    if "FOREIGN KEY" in message:
        return "Cannot delete this row yet: other records still point to it (enrollments or fees). Delete those first."
    if "NOT NULL" in message:
        return "Cannot delete this row: a required column has no value."
    return "Could not delete this row. Please try again."


# Read-friendly views used by the View Data tab.
VIEW_SQL: Dict[str, str] = {
    "departments": (
        "SELECT d.id, d.name AS department, d.hod_name AS head_of_department "
        "FROM departments d ORDER BY d.id"
    ),
    "students": (
        "SELECT s.id, s.name, s.email, s.gender, s.age, s.year, d.name AS department, "
        "s.city, s.admission_date FROM students s "
        "JOIN departments d ON d.id = s.department_id ORDER BY s.id"
    ),
    "faculty": (
        "SELECT f.id, f.name, f.email, f.designation, d.name AS department, f.joining_date "
        "FROM faculty f JOIN departments d ON d.id = f.department_id ORDER BY f.id"
    ),
    "courses": (
        "SELECT c.id, c.title, c.credits, d.name AS department, f.name AS faculty "
        "FROM courses c JOIN departments d ON d.id = c.department_id "
        "LEFT JOIN faculty f ON f.id = c.faculty_id ORDER BY c.id"
    ),
    "enrollments": (
        "SELECT e.id, s.name AS student, c.title AS course, "
        "COALESCE(e.grade, 'not graded') AS grade FROM enrollments e "
        "JOIN students s ON s.id = e.student_id "
        "JOIN courses c ON c.id = e.course_id ORDER BY e.id"
    ),
    "fees": (
        "SELECT f.id, s.name AS student, f.amount_total, f.amount_paid, "
        "(f.amount_total - f.amount_paid) AS balance, "
        "CASE WHEN f.amount_paid >= f.amount_total THEN 'Paid' ELSE 'Pending' END AS status, "
        "f.due_date FROM fees f JOIN students s ON s.id = f.student_id ORDER BY f.id"
    ),
}


def read_view(table: str) -> pd.DataFrame:
    """Read one table as a friendly dataframe (no caching: always live)."""
    with db_conn() as conn:
        return pd.read_sql_query(VIEW_SQL[table], conn)


# --------------------------------------------------------------------------- #
# UI: tab 1 - Add Data
# --------------------------------------------------------------------------- #

def render_add_tab() -> None:
    """Render the insert form for any of the six tables."""
    help_text = {
        "students": "Fields marked * are required. Emails must be unique.",
        "faculty": "Fields marked * are required. Emails must be unique.",
        "courses": "Fields marked * are required. Credits are 1 to 10.",
        "enrollments": "A student can appear in one course only once.",
        "fees": "Total and paid are in rupees. Paid cannot exceed the total.",
        "departments": "Department names must be unique.",
    }
    table = st.selectbox(
        "Table to add a record to",
        ["students", "faculty", "courses", "enrollments", "fees", "departments"],
        key="add_table",
    )
    st.caption(help_text.get(table, ""))

    spec = FORM_SPECS[table]
    prefix = f"add_{table}"
    values: Dict[str, Any] = {}

    with st.form(f"form_{table}", clear_on_submit=False):
        for field in spec["fields"]:
            values[field["col"]] = render_field(field, prefix)
        st.markdown("")
        submitted = st.form_submit_button("Add record", type="primary", key="add_submit")

    if not submitted:
        return

    row, error = validate_and_build(table, values)
    if error:
        st.error(error)
        return

    ok, message = insert_row(table, row)
    if not ok:
        st.error(message)
        return

    # Clear the widgets and show the confirmation on the next (fresh) run so the
    # dropdowns immediately include the record we just inserted.
    for field in spec["fields"]:
        st.session_state.pop(f"{prefix}_{field['col']}", None)
    label = table[:-1] if table != "students" else "student"
    st.session_state["flash_text"] = f"Saved: a new {label} record was added to {table}."
    st.rerun()


# --------------------------------------------------------------------------- #
# UI: tab 2 - View Data
# --------------------------------------------------------------------------- #

def render_view_tab() -> None:
    """Render a dataframe, a row count and a delete-by-id control."""
    table = st.selectbox("Table to view", ["departments", "students", "faculty", "courses", "enrollments", "fees"], key="view_table")

    try:
        frame = read_view(table)
    except sqlite3.Error as exc:
        st.error(f"Could not read the table: {exc}")
        return

    total = int(len(frame))
    st.markdown(f"**{total}** row{'s' if total != 1 else ''} in `{table}`")
    st.dataframe(frame, width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown("#### Delete a row")
    if total == 0:
        st.info("Nothing to delete yet.")
        return

    valid_ids = [int(v) for v in frame["id"].tolist()]
    chosen = st.selectbox("Row ID", valid_ids, key="view_row_id")
    preview = frame[frame["id"] == chosen]
    st.dataframe(preview, width="stretch", hide_index=True)

    confirm = st.checkbox("Yes, delete this row (this cannot be undone)", key="view_confirm")
    if st.button("Delete row", type="primary", disabled=not confirm, key="view_delete"):
        try:
            with db_conn() as conn:
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (chosen,))
        except sqlite3.IntegrityError as exc:
            st.error(friendly_delete_error(str(exc)))
        except sqlite3.Error as exc:
            st.error(friendly_delete_error(str(exc)))
        else:
            st.session_state["flash_text"] = f"Deleted row {chosen} from {table}."
            st.rerun()


# --------------------------------------------------------------------------- #
# UI: tab 3 - Ask the Database
# --------------------------------------------------------------------------- #

def render_steps(steps: List[Dict[str, Any]]) -> None:
    """Show every tool call the agent made, so students can watch it work."""
    if not steps:
        st.caption("The agent answered directly, without needing a tool.")
        return
    with st.expander(f"Agent steps ({len(steps)} tool call{'s' if len(steps) != 1 else ''})"):
        for number, step in enumerate(steps, start=1):
            if step["tool"] == "get_schema":
                head = f"**Step {number} - tool: `get_schema`** (read the live schema)"
            else:
                head = f"**Step {number} - tool: `run_sql`** - {step['rows']} row(s) returned"
            st.markdown(head)
            if step["sql"]:
                st.code(step["sql"], language="sql")
            if step["error"]:
                st.warning(step["output"])
            else:
                with st.expander("Raw tool result"):
                    st.text(step["output"])


def render_ask_tab() -> None:
    """Render the chat UI plus the agent loop."""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    # Take the queued example question straight away, so it never lingers.
    pending = st.session_state.pop("pending_question", None)

    if not API_KEY:
        st.warning("GROQ_API_KEY is not set, so the AI agent cannot run yet.")
        st.markdown(
            "The **Add Data** and **View Data** tabs work normally without it.\n\n"
            "To enable the agent, set the key and restart the app:\n\n"
            "```\n"
            "# Windows (PowerShell)\n"
            "$env:GROQ_API_KEY = \"gsk_...\"\n"
            "# macOS / Linux\n"
            "export GROQ_API_KEY=\"gsk_...\"\n"
            "```"
        )
        st.code("streamlit run app.py", language="bash")
        st.caption(f"Model that will be used: {MODEL_NAME} (change with GROQ_MODEL)")
        if pending:
            st.info("Set the key and restart the app, then this example question will run.")
        return

    st.caption(
        "Ask anything about departments, students, faculty, courses, enrollments or fees. "
        f"The agent reads this database live using two tools. Model: {MODEL_NAME}."
    )

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                # Replay errors as warnings, exactly as they first appeared.
                if message.get("failed"):
                    st.warning(message["content"])
                else:
                    st.markdown(message["content"])
                if message.get("steps"):
                    render_steps(message["steps"])
            else:
                st.markdown(message["content"])

    typed = st.chat_input("Ask a question about the college data")
    question = (typed or pending or "").strip()

    if question:
        handle_question(question)


def handle_question(question: str) -> None:
    """Run the agent for one question and store the turn in the chat history."""
    # Only successful turns are replayed to the model; error messages are not
    # part of the conversation.
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
        if m["role"] == "user" or (m["role"] == "assistant" and not m.get("failed"))
    ]
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Agent is thinking..."):
            try:
                result = run_agent(question, history)
            except Exception:
                result = {
                    "answer": "Something went wrong while running the agent. Please try again.",
                    "steps": [],
                    "failed": True,
                }
        if result["failed"]:
            st.warning(result["answer"])
        else:
            st.markdown(result["answer"])
        render_steps(result["steps"])

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": result["answer"],
            "steps": result["steps"],
            "failed": result["failed"],
        }
    )


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

def render_sidebar() -> None:
    """Sidebar: title, status, example questions, reset button."""
    with st.sidebar:
        st.title("College Assistant")
        st.caption("Manage the college database and ask an AI agent about it.")

        st.markdown("**Try one of these:**")
        for example in EXAMPLE_QUESTIONS:
            if st.button(example, key=f"ex_{example}", width="stretch"):
                st.session_state["pending_question"] = example
                st.rerun()

        st.markdown("")
        if st.button("Reset demo data", width="stretch"):
            try:
                reset_db()
            except sqlite3.Error as exc:
                st.error(f"Could not reset the database: {exc}")
            else:
                st.session_state["messages"] = []
                st.session_state["flash_text"] = "Demo data has been reset."
                st.rerun()

        st.markdown("---")
        status = "AI agent ready" if API_KEY else "AI agent off (no GROQ_API_KEY)"
        st.caption(f"{status}\n\nModel: {MODEL_NAME}\n\nDatabase: {DB_PATH.name}")


# --------------------------------------------------------------------------- #
# App entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    """Create/seed the database, then draw the app."""
    st.set_page_config(page_title="College Assistant", page_icon="🎓", layout="wide")

    # Never let a database problem stop the UI from drawing.
    db_error: Optional[str] = None
    try:
        bootstrap_db()
    except sqlite3.Error as exc:
        db_error = str(exc)

    render_sidebar()

    if db_error:
        st.error("The college database could not be opened or prepared.")
        st.code(str(db_error), language="text")
        st.info(
            "Quick fix: close the app, delete the file `college.db` in this folder, "
            "and reload the page. The database and the demo data are recreated automatically. "
            "If that does not help, make sure no other program is using college.db."
        )
        return

    st.title("🎓 College Assistant")
    st.markdown(
        "Add records, browse them, and ask the AI agent questions about the same live data."
    )

    # One shared success banner for the whole page, so a save on one tab is
    # visible even after the form resets itself.
    flash = st.session_state.pop("flash_text", None)
    if flash:
        st.success(flash)

    add_tab, view_tab, ask_tab = st.tabs(["Add Data", "View Data", "Ask the Database"])

    with add_tab:
        render_add_tab()
    with view_tab:
        render_view_tab()
    with ask_tab:
        render_ask_tab()


if __name__ == "__main__":
    main()
