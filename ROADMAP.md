# Graft — Engineering Roadmap

This document captures architectural decisions, technical debt, and the planned
evolution of Graft. It is written for engineers, not for marketing.

Every item here has a *why* alongside the *what*. When a decision was hard,
the reasoning is preserved so the next person doesn't have to re-derive it.

---

## Current State (v0.1 — Preview)

| Module | Status | Tests |
|---|---|---|
| `code_indexer` | Complete — Python only | 73 passing |
| `graft_parser` | Complete | 73 passing |
| `graft_engine/entity_registry` | Complete | 85 passing |
| `graft_engine/compiler` | Complete | 97 passing |
| `graft_engine/executor` | Complete | 53 passing |
| `graft_server` | Minimal (60 lines) | — |
| CLI (`graft`) | Minimal (3 commands) | — |

**235 passing tests. SQLite only. Python only. Local use only.**

---

## Priority Order

| # | Area | Rationale |
|---|---|---|
| 1 | Engine: docs + ORDER BY + LIMIT | Unblocks real queries, makes compiler maintainable |
| 2 | Language: JavaScript adapter | Doubles addressable codebases immediately |
| 3 | Server: logging + timeout + rate limiting | Makes it safe to expose |
| 4 | Testing: real codebase + security audit | Finds bugs that small fixtures cannot |
| 5 | Agent skill + MCP server | The strategic moat. Makes it a platform. |
| 6 | Language: Java adapter | Covers enterprise codebases |
| 7 | Server: auth + metrics | Only after you know who is using it |
| 8 | Language: TypeScript adapter | Incremental after JavaScript |

---

## Track 1 — Multi-Language Support

### Architectural decision: one schema, many languages

**The decision: do NOT create per-language tables.**

The schema (`files`, `symbols`, `expressions`, `references`) is intentionally
language-agnostic. A function is a function. A call is a call. These concepts
exist in every language Graft targets.

The `files.language` column is the only language discriminator the query engine
needs. Every GQL query already has access to `fn.language` for filtering.

Per-language tables would mean:
- Every GQL query needs an explicit language qualifier
- The compiler requires language-aware dispatch
- Joins across languages (e.g. "Python calling a JS function via IPC") become
  impossible to express
- The entity registry doubles or quadruples in size

The correct extension point is `code_indexer/languages/`. Each language adapter
implements one interface: walk a tree-sitter CST and emit `RawExtraction`. The
rest of the pipeline — schema, compiler, executor, server — is identical.

> **Note for future engineers:** if you are tempted to add a `python_symbols`
> table or a `javascript_expressions` table, re-read this section first. The
> answer is almost certainly "no". The schema was designed to be universal.
> Language-specific semantics belong in the extractor, not the schema.

---

### Language adapters — planned order and difficulty

#### JavaScript (Priority: High)
- tree-sitter grammar is mature and well-maintained
- AST structure is similar to Python: functions, classes, calls, imports
- Main complexity: anonymous functions, arrow functions, destructuring
- `kind` values to add: `"arrow_function"`, `"async_function"`
- Estimated effort: 2–3 days

#### Java (Priority: High)
- Very regular AST — class/method hierarchy is explicit and unambiguous
- Verbose but predictable: `MethodDeclaration`, `ClassDeclaration` map directly
- Main complexity: annotations, generics in signatures, nested classes
- `signature` JSON will need `{"params": [...], "return_type": "...", "throws": [...]}`
- Estimated effort: 3–4 days

#### TypeScript (Priority: Medium — after JavaScript)
- JavaScript + type annotations
- tree-sitter-typescript extends tree-sitter-javascript
- Main complexity: type parameters, interface declarations, decorators
- Consider adding `type_annotation` field to `FieldDef` for TS-specific queries
- Estimated effort: 2 days incremental over JS adapter

#### C# / .NET (Priority: Medium)
- Namespaces add a layer of hierarchy the current schema does not model
- Partial classes: one class split across multiple files — `parent_id` alone
  is insufficient to represent this
- Generics: `List<T>` in signatures needs careful serialisation
- Consider: add `namespace` column to `symbols` (nullable, text) without
  breaking existing queries
- Estimated effort: 4–5 days

#### Go (Priority: Later)
- Simple, regular AST — explicit function signatures, no inheritance
- Interfaces are structural (not nominal) — affects how callers are resolved
- Goroutines/channels are not representable in current expression kinds
- Estimated effort: 3 days

#### Rust (Priority: Later)
- Ownership and lifetime semantics have no equivalent in the current schema
- Traits are not classes; impls are not inheritance
- Macros expand at compile time — tree-sitter sees pre-expansion source only
- The `references` table assumes call-by-name resolution; Rust's trait dispatch
  requires type information Graft does not currently have
- Estimated effort: 1 week+, requires schema extensions

#### C / C++ (Priority: Later)
- Preprocessor directives mean the AST tree-sitter sees is not the compiled AST
- Header files: symbols defined in `.h`, implemented in `.c` — two-file symbols
- C++ templates: similar problem to Rust generics
- `#include` resolution requires knowledge of include paths at index time
- Estimated effort: complex. Scope separately.

---

### What to add to the schema for language support

No new tables. Possible nullable column additions (all backward-compatible):

```sql
-- On symbols: namespace for Java/C#
ALTER TABLE symbols ADD COLUMN namespace TEXT;

-- On symbols: visibility for Java/C#/TypeScript
ALTER TABLE symbols ADD COLUMN visibility TEXT;  -- public, private, protected

-- On expressions: resolved type for TypeScript
ALTER TABLE expressions ADD COLUMN resolved_type TEXT;
```

These are additive. Existing Python queries are unaffected.

---

## Track 2 — Engine Strength

### What the compiler currently handles (v0.1)

- All 4 root entities and 7 virtual entities
- Field traversals, predicate traversals
- JSON field extraction (`json_extract`)
- Computed fields (`json_array_length` for paramCount)
- Correlated subqueries (EXISTS, NOT EXISTS)
- Recursive CTEs (callDepth)
- Auto GROUP BY for aggregate fields (callerCount)
- Full WHERE clause: =, !=, >, <, >=, <=, LIKE, IS NULL, NOT, AND, OR

### What is missing (prioritised)

#### ORDER BY (High priority)
```sql
-- Cannot currently do this:
from function as fn select fn.name, fn.paramCount order by fn.paramCount desc
```
Required for any meaningful ranking query. One additional clause in the AST,
one additional compiler pass. Grammar change required in `graft_parser`.

#### LIMIT (High priority)
```sql
from function.calls as c select c.name limit 10
```
Essential for large codebases. Prevents runaway queries. Pairs with ORDER BY.

#### DISTINCT (Medium priority)
```sql
-- function.callers currently returns duplicate rows when a function
-- is called from multiple sites in the same caller
from function.callers as fn select distinct fn.name
```

#### Field aliasing in SELECT (Medium priority)
```sql
select fn.name as functionName, fn.filename as file
```
Without this, column names in results are always the GQL field name. Fine for
small queries, limiting for programmatic use.

#### COUNT and aggregates in GQL (Medium priority)
Currently COUNT is only auto-injected for `callerCount`. GQL should support:
```sql
from class.methods as m select m.className, count(m.name) as methodCount
```
Requires GROUP BY to become explicit in GQL syntax, not just implicit.

#### Multi-hop traversals (Lower priority — v2)
```sql
-- Two levels of callers
from function.callers.callers as fn select fn.name
```
The entity registry supports chained traversals but the compiler does not
generate the correct nested JOIN for self-referential hops. Requires
rethinking how join aliases are assigned when the same entity appears twice.

#### Cross-entity field access (Lower priority — v2)
```sql
-- Filter on a field that belongs to a parent entity
from function.calls as c where c.file.language = "python" select c.name
```

---

### Internal documentation debt

The compiler (`graft_engine/compiler.py`) has three non-obvious decisions that
are currently undocumented in the code:

1. **Join deduplication** — when a traversal's `extra_joins` reference a table
   already in the FROM clause (e.g. `file.functions` traversal re-adds
   `symbols` which is already the terminal entity's base table), the compiler
   silently skips both the join and its `extra_where`. This prevents
   contradictory WHERE conditions. It is correct but invisible.

2. **CTE placeholder column** — the `call_depth` field resolves to
   `sa.column("call_depth")` (a bare column reference) during field resolution,
   and is replaced with the actual CTE column in `_assemble_with_cte()`. Any
   engineer reading `_field_to_col()` will find the `raise CompileError()`
   for `call_depth` confusing without knowing the replacement happens later.

3. **GROUP BY auto-injection** — `_needs_group_by()` scans the projection for
   `computed="caller_count"` fields and silently adds GROUP BY. This only works
   correctly if the caller_count field is always the only aggregate in the
   projection. A second aggregate would require a more sophisticated approach.

These must be documented inline before the compiler grows further.

---

## Track 3 — Server

### Current state

The server is 60 lines. It has one endpoint, no middleware, no logging, no
error boundaries beyond exception handlers. It is correct for local use.

### Required before any networked deployment

#### Structured logging
Every request should emit a structured log line:
```json
{"request_id": "uuid", "query": "from function...", "elapsed_ms": 3.2, "row_count": 47, "status": 200}
```
Use `structlog` or `python-json-logger`. Attach `request_id` to error logs so
a failed query can be traced. This is non-negotiable for any shared deployment.

#### Query timeout
Recursive CTEs (callDepth) can loop on pathological graphs. The server must
enforce a hard timeout (default: 5s) on every query execution. SQLAlchemy
does not provide this natively for SQLite — use `threading.Timer` or
`asyncio.wait_for` depending on the execution model.

#### Rate limiting
One expensive query should not degrade all other users. Use `slowapi` (wraps
`limits` for FastAPI). Default: 60 queries/minute per IP for local, lower for
hosted.

#### Error normalisation
The server must never return a Python stack trace to a client. All exceptions
should be caught at the boundary and returned as:
```json
{"error": "compile_error", "message": "Field 'bogus' not found on entity 'function'", "query_sql": null}
```

### Deferred until use case is clear

#### Authentication
The original design spec says: *"No authentication in v1. This is a local
developer tool."* This is still correct.

Auth should only be added when Graft is deployed as a shared service (team
server, hosted SaaS). The right answer depends on the deployment model:
- Local tool: no auth, bind to 127.0.0.1 only
- Team server: API key in header, keys stored in DB
- Hosted SaaS: OAuth2 / JWT

Do not add auth complexity until the deployment model is decided.

#### Metrics (Prometheus / OpenTelemetry)
Instrument query latency, error rate, and row count per query type. FastAPI
has first-class Prometheus middleware. Worth adding early once the server
handles real traffic — you want this data before you optimise.

---

## Track 4 — Testing

### Current test coverage gaps

The executor tests use a seeded in-memory DB with 8 symbols and 7 expressions.
This is enough to prove the pipeline is wired correctly. It is not enough to
prove reliability on real codebases.

#### Real codebase integration tests (High priority)

Index a known open-source Python project (CPython stdlib, Flask, Django core)
and run a fixed set of queries. Assert:
- No crashes or exceptions
- Row counts are within expected ranges
- Known functions appear in results

These are regression tests. Once golden, a failing test means something broke.

#### SQL injection audit (High priority)

GQL accepts user-supplied strings (field values, callee names). The compiler
uses SQLAlchemy bind parameters for all user values in the executed statement.
The `query_sql` string uses `literal_binds=True` for display only — it is never
executed.

This needs an explicit test:
```python
# Verify no user string is ever interpolated into raw SQL
query = 'from function.calls as c where c.name = "eval\'; DROP TABLE symbols; --" select c.name'
result = run(compile(parse(query)), engine)
# Must return 0 rows, must not drop the table
assert symbols_count_unchanged()
```

#### Adversarial input tests (Medium priority)

- File with 10,000 functions (performance)
- Deeply nested calls (stack depth in tree-sitter walker)
- Unicode identifiers (`def naïve():`, `class データ:`)
- Empty files
- Files with only comments
- Circular imports (two files importing each other)

#### Query fuzzing (Medium priority)

Generate random syntactically valid GQL queries and assert:
- They either compile and execute cleanly
- Or raise a typed `CompileError` / `GraftParseError`
- They never produce an unhandled exception or a Python traceback

---

## Track 5 — Docs and Agent Integration

### The agent skill

This is the highest-leverage documentation item. A well-defined agent skill
tells an LLM *exactly* when and how to use Graft — what questions it can
answer, what it cannot, how to interpret results.

The skill answers:
- When should an agent call `graft query` vs reading a file directly?
- What does a result of 0 rows mean?
- How should an agent phrase a question as a GQL query?
- What are the limits of what GQL can express?

Without this skill, an agent defaults to reading files. With it, an agent can
understand a 50,000-line codebase by running 10 queries.

### MCP server (Model Context Protocol)

An MCP wrapper exposes Graft as a native tool to Claude, Cursor, Copilot, and
any MCP-compatible agent. The tool surface is minimal:

```
graft_query(query: str) -> QueryResult
graft_index(path: str) -> IndexResult
graft_entities() -> list[str]
graft_fields(entity: str) -> list[str]
```

This closes the loop on the original problem: an agent writes code, then
immediately queries that code to understand what it built — without reading
files, without consuming source in the context window.

### Query cookbook

30+ real GQL queries with descriptions, grouped by use case:
- Onboarding (what is this codebase?)
- Security audit (dangerous calls, external I/O)
- Refactor planning (dead code, fat classes, deep call chains)
- Understanding AI-generated code (what did the agent build?)
- Dependency analysis (what calls what?)

---

## Principles (do not override these)

These were set at project start and every decision above respects them.

**Transparency** — always expose the generated SQL. Users must never feel
that magic is hiding something from them.

**Locality** — runs on your machine. No cloud, no accounts, no rate limits.
Code never leaves the developer's environment.

**Composability** — results are rows. Pipe them anywhere. Graft does not own
the output format.

**Honest scope** — unresolved references are `NULL`, not omitted. Graft tells
you what it does not know.

**Incrementality** — re-indexing an unchanged file (same SHA-256) is a no-op.

**One schema** — language adapters produce identical schema rows. The query
engine is language-agnostic. This is a hard constraint, not a preference.
