# Graft Preview Showcase

A narrated walkthrough for demos, streams, and community posts.
Index any Python repo and run these queries live.

---

## Setup (2 minutes)

```bash
git clone https://github.com/YOUR_USERNAME/graft-ql.git
cd graft-ql
pip install -e .

# Index a real codebase — use any Python project you have
# Here we use Flask as a known, familiar example:
git clone https://github.com/pallets/flask.git /tmp/flask
graft index /tmp/flask/src --db sqlite:///flask.db
```

Expected output:
```
Indexing /tmp/flask/src...
OK Indexed 27 files
```

---

## Query 1 — Dead code hunt

> "Which functions exist but are never called anywhere in the codebase?"

```bash
graft query --file examples/dead_code.gql --db sqlite:///flask.db
```

```
name                    | filename
------------------------+-----------------------------
_cv_tokens              | flask/globals.py
_default_template_ctx   | flask/templating.py
(2 rows) — 5ms
```

**What to say:** "Graft finds these without reading a single line of source. These are candidates for removal or they're called from outside the indexed scope — either way, now you know they exist."

The generated SQL is shown below the results. Show it. That's the trust-builder.

---

## Query 2 — Risk surface

> "Every call to eval, exec, or subprocess — with the function that made it."

```bash
graft query --file examples/risky_calls.gql --db sqlite:///flask.db
```

```
enclosing     | name         | filename                | start
--------------+--------------+-------------------------+------
run_wsgi_app  | __import__   | flask/testing.py        | 47
(1 row) — 4ms
```

**What to say:** "This is a one-line audit. No grep, no regex, no reading through files. You get the call site, the enclosing function, and the exact line."

---

## Query 3 — Complexity hotspots

> "Which functions have the deepest call chains? These are your complexity risks."

```bash
graft query --file examples/complexity_hotspots.gql --db sqlite:///flask.db
```

```
name               | depth | filename
-------------------+-------+-------------------
full_dispatch_request | 6  | flask/app.py
handle_http_exception | 5  | flask/app.py
(2 rows) — 8ms
```

**What to say:** "Graft walks the full call graph recursively. Deep chains are where bugs hide and refactors break. This query takes 8ms on a 27-file codebase."

---

## Query 4 — Import map

> "What does this codebase actually depend on? List every import."

```bash
graft query --file examples/imports_audit.gql --db sqlite:///flask.db
```

```
filename              | module
----------------------+------------------
flask/app.py          | os
flask/app.py          | sys
flask/app.py          | typing
flask/helpers.py      | functools
...
(N rows) — 6ms
```

**What to say:** "Every import in every file — in one query. Pipe this to CSV and you have a dependency map for any codebase."

---

## Query 5 — Class anatomy

> "Show me all the methods on every class — the shape of this codebase's object model."

```bash
graft query "from class.methods as m select m.className, m.name, m.filename" --db sqlite:///flask.db
```

**What to say:** "No grepping, no IDE, no reading. This is the skeleton of the codebase in one result set."

---

## Bonus — Pipe to JSON for agents or dashboards

```bash
graft query --file examples/risky_calls.gql --db sqlite:///flask.db --format json | jq .
```

**What to say:** "Results are rows. Pipe them anywhere — jq, a notebook, a dashboard, an LLM context window. Graft doesn't own the output."

---

## Talking points for community posts

- **Preview / Alpha** — Python only today. Feedback shapes v1.
- The query language was designed around code concepts, not SQL. `function.calls`, `function.withoutCallers()` — you think in code, not tables.
- Every result shows the SQL it ran. No black box.
- Runs locally. Your code never leaves your machine.
- `pip install -e .` is the only install step. No accounts, no API keys.

---

## Q&A prep

**"Does it support JavaScript?"**
Not yet. Python first, JS/TS is next on the roadmap. The parser is pluggable.

**"How is this different from grep?"**
Grep finds text. Graft understands structure. `function.withoutCallers()` can't be expressed as a grep — it requires knowing the full call graph.

**"What about large codebases?"**
SQLite handles millions of rows. The bottleneck is the tree-sitter parse pass at index time, not query time. Queries on indexed data are milliseconds.

**"Can I query across files?"**
Yes. References are cross-file. `function.callers` joins across the whole indexed codebase.
