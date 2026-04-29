\# Graft — Master Claude Working Guide



\## Vision



Graft is a \*\*code insight engine\*\*. It answers the question:

\*"What is this codebase actually doing?"\*



It is not a security scanner. It is not a linter. It is not a search engine.

It is a structured, queryable intelligence layer over source code — designed

for developers who need to \*\*understand\*\* unfamiliar, large, or complex codebases.



The user is:

\- A developer dropped into an unfamiliar repo

\- A tech lead who needs to know what their team actually built

\- An architect planning a refactor who needs to know what touches what

\- Someone inheriting legacy code with no documentation



The core loop is:



```

graft index ./src              # parse code → store in DB

graft query insight.gql        # ask a structural question → get an answer

```



Results are structured rows — pipeable to JSON, CSV, a dashboard, a notebook.



\---



\## System Architecture



```

┌─────────────────────────────────────────────────────────┐

│                      graft (monorepo)                    │

├──────────────┬───────────────┬────────────┬─────────────┤

│ graft\_indexer│ graft\_parser  │graft\_engine│ graft\_server│

│              │               │            │             │

│ Tree-sitter  │ Lark grammar  │ SA Core    │ FastAPI     │

│ → SQL INSERT │ → Query AST   │ AST → SQL  │ REST        │

│              │               │ → results  │             │

└──────┬───────┴───────┬───────┴──────┬─────┴──────┬──────┘

&#x20;      │               │              │            │

&#x20;      └───────────────┴───────┬──────┘            │

&#x20;                              │               graft serve

&#x20;                         SQLite/PG/etc        graft index

&#x20;                         (shared DB)          graft query

```



\### Dependency rules — never violate these



```

graft\_indexer  →  graft schema only (writes)

graft\_parser   →  nothing external (pure Python, zero DB)

graft\_engine   →  graft\_parser + graft schema (reads)

graft\_server   →  graft\_engine + graft\_indexer

graft (cli)    →  graft\_server client OR graft\_engine directly

```



\*\*graft\_parser must remain pure.\*\* It has no SQLAlchemy, no Tree-sitter,

no DB connection. Input: string. Output: AST dataclasses. This is non-negotiable

and makes the parser independently testable with zero infrastructure.



\---



\## Database Schema



Defined in `graft\_indexer/schema.py` using SQLAlchemy Core.

All IDs are UUID v5 (deterministic from content). Schema is dialect-agnostic.



\### `files`

| column | type | notes |

|---|---|---|

| id | UUID | PK |

| path | Text | absolute or relative path |

| language | String | "python", "javascript", etc. |

| content\_hash | String | SHA-256, used for incremental re-index |

| scanned\_at | String | ISO-8601 |



\### `symbols`

Functions, methods, classes — any named scope.

| column | type | notes |

|---|---|---|

| id | UUID | PK |

| file\_id | UUID | FK → files |

| name | String | symbol name |

| kind | String | "function" \\| "method" \\| "class" \\| "lambda" |

| start\_byte | Int | |

| end\_byte | Int | |

| start\_line | Int | 1-indexed |

| end\_line | Int | 1-indexed |

| signature | JSON | `{"params": \[...], "return\_type": "str"}` |

| parent\_id | UUID | FK → symbols (for nested/methods) |



\### `expressions`

Every meaningful expression inside a symbol body. Forms a tree via parent\_id.

| column | type | notes |

|---|---|---|

| id | UUID | PK |

| file\_id | UUID | FK → files |

| symbol\_id | UUID | FK → symbols (owning scope) |

| parent\_id | UUID | FK → expressions (tree structure) |

| kind | String | "call" \\| "binary" \\| "assignment" \\| "return" \\| "import" \\| "declaration" |

| source\_text | Text | raw text slice of this node |

| start\_byte | Int | |

| end\_byte | Int | |

| start\_line | Int | |

| end\_line | Int | |

| depth | Int | depth from symbol body root |

| extra | JSON | kind-specific: `{"callee": "eval", "arg\_count": 1, "is\_method": false}` |



\### `references`

Call graph edges. One row per call site.

| column | type | notes |

|---|---|---|

| id | UUID | PK |

| file\_id | UUID | FK → files |

| expression\_id | UUID | FK → expressions (the call site) |

| from\_symbol\_id | UUID | FK → symbols (caller) |

| callee\_name | String | unresolved name as written in source |

| to\_symbol\_id | UUID | FK → symbols, NULL until resolution pass |



\---



\## The Query Language — Graft Query Language (GQL)



\### Philosophy



GQL exposes a \*\*code-aware vocabulary\*\* that hides relational complexity.

The user thinks in code concepts. GQL translates those to SQL.



A query has three clauses:



```

FROM  <entity-path> AS <alias>

WHERE <condition>             ← optional

SELECT <projection>

```



\### Full grammar (Lark syntax, defined in graft\_parser/grammar.lark)



```lark

query        : "from" entity\_path "as" ALIAS

&#x20;              ("where" condition)?

&#x20;              "select" projection



entity\_path  : ROOT\_ENTITY ("." traversal)\*



ROOT\_ENTITY  : "function"

&#x20;            | "class"

&#x20;            | "file"

&#x20;            | "expression"



traversal    : WORD "(" arglist? ")"   -> predicate\_traversal

&#x20;            | WORD                    -> field\_traversal



condition    : expr (("and" | "or") expr)\*



expr         : field OP value

&#x20;            | field "like" STRING

&#x20;            | "not" expr



field        : ALIAS "." WORD ("." WORD)\*



OP           : "=" | "!=" | ">" | "<" | ">=" | "<="



value        : STRING | NUMBER | "null" | field



projection   : proj\_item ("," proj\_item)\*



proj\_item    : field

&#x20;            | STRING

&#x20;            | proj\_item "+" proj\_item    -> concat



ALIAS        : /\[a-z\_]\[a-z0-9\_]\*/

WORD         : /\[a-zA-Z\_]\[a-zA-Z0-9\_]\*/

STRING       : /\\"\[^\\"]\*\\"/ | /\\'\[^\\']\*\\'/

NUMBER       : /\[0-9]+(\\.\[0-9]+)?/

```



\### Query examples



```sql

\-- All calls to a specific function

from function.calls as c

where c.name = "eval"

select c.filename, c.start, c.enclosing



\-- Functions that take no arguments

from function.withoutArgs() as fn

select fn.name, fn.filename



\-- Functions that throw/raise

from function.getDoesThrow() as fn

select fn.name, fn.filename, fn.start



\-- Every function and how many times it's called

from function.callers as fn

select fn.name, fn.callerCount, fn.filename



\-- Dead code: functions never called

from function.withoutCallers() as fn

select fn.name, fn.filename, fn.start



\-- All IO-adjacent calls

from function.calls as c

where c.name like "open" or c.name like "write" or c.name like "read"

select c.enclosing, c.filename, c.start, c.name



\-- All methods on a class

from class.methods as m

where m.className = "Calculator"

select m.name, m.start, m.end



\-- Deep call chains (potential complexity hotspot)

from function.callDepth() as fn

where fn.depth > 4

select fn.name, fn.depth, fn.filename



\-- All imports in the codebase

from file.imports as i

select i.filename, i.module



\-- Functions with many parameters (complexity signal)

from function.signature() as fn

where fn.paramCount > 5

select fn.name, fn.paramCount, fn.filename

```



\---



\## Entity Registry — The Semantic Layer



This is the authoritative mapping of GQL concepts to SQL.

Defined in `graft\_engine/entity\_registry.py`.



Each entity is a Python dataclass describing:

\- What SQL tables it touches

\- What joins it requires

\- What fields it exposes

\- What predicates it accepts



\### Root entities



\#### `function`

Base entity. Maps to `symbols WHERE kind IN ('function', 'method')`.



\*\*Fields:\*\*

| GQL field | SQL source |

|---|---|

| `fn.name` | `symbols.name` |

| `fn.filename` | `files.path` via JOIN on `file\_id` |

| `fn.language` | `files.language` |

| `fn.start` | `symbols.start\_line` |

| `fn.end` | `symbols.end\_line` |

| `fn.kind` | `symbols.kind` |

| `fn.signature` | `symbols.signature` (JSON) |

| `fn.paramCount` | `json\_array\_length(symbols.signature->'params')` |



\*\*Traversals:\*\*



| GQL traversal | SQL translation |

|---|---|

| `function.calls` | `symbols JOIN expressions ON symbol\_id WHERE expressions.kind='call'` |

| `function.calls("name")` | same + `expressions.extra->>'callee' = 'name'` |

| `function.callers` | `symbols JOIN references ON to\_symbol\_id` (resolved refs only) |

| `function.callees` | `symbols JOIN references ON from\_symbol\_id` |

| `function.callDepth()` | recursive CTE walking references graph, counting depth |



\*\*Predicates (filter the root):\*\*



| GQL predicate | SQL translation |

|---|---|

| `function.withoutArgs()` | `json\_array\_length(signature->'params') = 0` |

| `function.signature(x)` | params positional match |

| `function.signature(null)` | alias for `withoutArgs()` |

| `function.getDoesThrow()` | `EXISTS (SELECT 1 FROM expressions WHERE symbol\_id=s.id AND kind='return' AND source\_text LIKE 'raise%')` |

| `function.withoutCallers()` | `NOT EXISTS (SELECT 1 FROM references WHERE to\_symbol\_id=s.id)` |



\#### `class`

Maps to `symbols WHERE kind = 'class'`.



\*\*Fields:\*\* `name`, `filename`, `start`, `end`, `language`



\*\*Traversals:\*\*

| GQL | SQL |

|---|---|

| `class.methods` | `symbols WHERE kind='method' AND parent\_id = class.id` |

| `class.methods.calls` | chain: methods → their call expressions |



\#### `file`

Maps to `files` table directly.



\*\*Fields:\*\* `filename` (path), `language`, `scanned\_at`



\*\*Traversals:\*\*

| GQL | SQL |

|---|---|

| `file.functions` | `symbols WHERE file\_id = file.id AND kind IN ('function','method')` |

| `file.imports` | `expressions WHERE file\_id = file.id AND kind = 'import'` |

| `file.classes` | `symbols WHERE file\_id = file.id AND kind = 'class'` |



\#### `expression`

Maps to `expressions` table directly.



\*\*Fields:\*\* `kind`, `source\_text`, `start`, `end`, `depth`, `filename`



\---



\## Call Expression Fields



When traversal lands on a call (e.g. `function.calls`), these fields are available:



| GQL field | SQL source |

|---|---|

| `c.name` | `expressions.extra->>'callee'` |

| `c.argCount` | `expressions.extra->>'arg\_count'` |

| `c.isMethod` | `expressions.extra->>'is\_method'` |

| `c.enclosing` | `symbols.name` of the owning symbol |

| `c.filename` | `files.path` |

| `c.start` | `expressions.start\_line` |

| `c.end` | `expressions.end\_line` |

| `c.source` | `expressions.source\_text` |



\---



\## AST Node Definitions



These are the exact dataclasses `graft\_parser` produces.

`graft\_engine` must consume exactly these — never couple to parser internals.



```python

@dataclass

class QueryAST:

&#x20;   entity\_path: EntityPath

&#x20;   alias: str

&#x20;   condition: Condition | None

&#x20;   projection: Projection



@dataclass

class EntityPath:

&#x20;   root: str                          # "function" | "class" | "file" | "expression"

&#x20;   traversals: list\[Traversal]



@dataclass

class FieldTraversal:

&#x20;   name: str                          # e.g. "calls", "methods"



@dataclass

class PredicateTraversal:

&#x20;   name: str                          # e.g. "withoutArgs", "getDoesThrow"

&#x20;   args: list\[str | None]             # positional args, None = null



Traversal = FieldTraversal | PredicateTraversal



@dataclass

class Condition:

&#x20;   expressions: list\[ConditionExpr]

&#x20;   operators: list\[str]               # "and" | "or", length = len(expressions) - 1



@dataclass

class ConditionExpr:

&#x20;   field: Field

&#x20;   op: str                            # "=" | "!=" | ">" | "<" | "like" | ...

&#x20;   value: str | int | float | Field | None

&#x20;   negated: bool = False



@dataclass

class Field:

&#x20;   alias: str

&#x20;   path: list\[str]                    # e.g. \["name"], \["extra", "callee"]



@dataclass

class Projection:

&#x20;   items: list\[ProjectionItem]



@dataclass

class FieldProjection:

&#x20;   field: Field



@dataclass

class LiteralProjection:

&#x20;   value: str



@dataclass

class ConcatProjection:

&#x20;   left: ProjectionItem

&#x20;   right: ProjectionItem



ProjectionItem = FieldProjection | LiteralProjection | ConcatProjection

```



\---



\## Result Model



The engine returns a `QueryResult` — never raw DB rows.



```python

@dataclass

class QueryResult:

&#x20;   columns: list\[str]          # column names from SELECT

&#x20;   rows: list\[dict\[str, Any]]  # each row is a dict keyed by column name

&#x20;   query\_sql: str              # the generated SQL (for debugging/transparency)

&#x20;   row\_count: int

&#x20;   elapsed\_ms: float



&#x20;   def to\_json(self) -> str: ...

&#x20;   def to\_csv(self) -> str: ...

&#x20;   def to\_table(self) -> str: ...    # formatted ASCII table for CLI output

```



The `query\_sql` field is important — Graft should always be transparent about

what SQL it ran. This builds trust and lets users debug unexpected results.



\---



\## Compiler Pipeline



```

query string

&#x20;   ↓  graft\_parser.parse()

QueryAST

&#x20;   ↓  graft\_engine.compiler.resolve\_entity()

LogicalPlan (entity + joins + filters + projections)

&#x20;   ↓  graft\_engine.compiler.to\_sqlalchemy()

SQLAlchemy select()

&#x20;   ↓  graft\_engine.executor.run()

QueryResult

```



Each step is a pure function. The compiler never executes SQL.

The executor never builds SQL — it only runs what the compiler produces.



\---



\## HTTP API (graft\_server)



Thin FastAPI wrapper. Two routes only in v1:



```

POST /query

&#x20; body: { "query": "from function.calls as c ...", "format": "json" }

&#x20; returns: QueryResult as JSON



POST /index

&#x20; body: { "path": "./src", "language": "python" }

&#x20; returns: { "files\_indexed": 12, "symbols": 847, "elapsed\_ms": 340 }



GET /status

&#x20; returns: { "db": "sqlite:///index.db", "files": 12, "symbols": 847 }

```



No authentication in v1. This is a local developer tool.



\---



\## CLI Commands



Built with \*\*Typer\*\*.



```bash

\# Index a directory

graft index ./src

graft index ./src --language python

graft index ./src --db postgresql://localhost/myproject

graft index ./src --watch          # future: re-index on file change



\# Query

graft query "from function.calls as c where c.name = 'eval' select c.filename, c.start"

graft query --file audit.gql

graft query --file audit.gql --format json

graft query --file audit.gql --format csv

graft query --file audit.gql --db sqlite:///index.db   # bypass server



\# Server

graft serve

graft serve --port 8080 --db sqlite:///index.db



\# Introspection

graft entities          # list all valid root entities

graft fields function   # list all fields available on 'function'

graft fields function.calls  # fields after traversal

```



\---



\## Build Order for Claude Code Sessions



Each session has one job. Read only the sections relevant to that job.



| Session | Module | Input | Output | Reads from this doc |

|---|---|---|---|---|

| 1 | `graft\_parser` | query string | QueryAST | Grammar, AST Node Definitions |

| 2 | `graft\_engine/entity\_registry` | — | entity map datastructure | Entity Registry, Schema |

| 3 | `graft\_engine/compiler` | QueryAST + registry | SA select() | Compiler Pipeline, AST nodes, Entity Registry |

| 4 | `graft\_engine/executor` | SA select() + engine | QueryResult | Result Model, Schema |

| 5 | `graft\_server` | — | FastAPI app | HTTP API section |

| 6 | `graft` CLI | — | Typer CLI | CLI Commands section |



\*\*Before every session:\*\* read this document top to bottom.

\*\*After every session:\*\* do not modify this document unless the spec genuinely changed.

This document is the contract. Code serves it, not the other way around.



\---



\## What Graft Is Not



\- Not a security scanner — no built-in vulnerability rules

\- Not a type checker — no type inference, best-effort only

\- Not a language server — no real-time, no IDE integration (yet)

\- Not a hosted service — runs locally, your code never leaves your machine

\- Not a replacement for reading code — it's a map, not the territory



\---



\## Naming



The project is named \*\*Graft\*\*.



\- Tree-sitter produces trees. Graft is what you do with trees.

\- You graft a query language onto a code structure.

\- Short, memorable, no existing dev-tools baggage.

\- Domain: `graft.dev` (check availability before publishing)



The query language is \*\*GQL\*\* (Graft Query Language).

Files use the `.gql` extension.



\---



\## Dependencies by Module



```toml

\# graft\_parser

lark >= 1.1



\# graft\_engine

sqlalchemy >= 2.0

graft\_parser  (local)

graft\_indexer (local, schema only)



\# graft\_server

fastapi >= 0.100

uvicorn

graft\_engine  (local)



\# graft (cli)

typer >= 0.9

rich

graft\_engine  (local)

graft\_indexer (local)

graft\_server  (local, optional — for serve command)

```



\---



\## Key Design Principles



1\. \*\*Transparency\*\* — always expose the generated SQL. Users should never feel like magic is hiding something from them.



2\. \*\*Locality\*\* — runs on your machine, against your DB. No cloud, no accounts, no rate limits.



3\. \*\*Composability\*\* — results are rows. Pipe them anywhere. The tool does not own the output.



4\. \*\*Honest scope\*\* — unresolved references are `NULL`, not omitted. The engine tells you what it doesn't know.



5\. \*\*Incrementality\*\* — re-indexing a file that hasn't changed (same SHA-256) is a no-op. Fast enough to run in a pre-commit hook.



6\. \*\*One job per session\*\* — when working with Claude Code, each session touches one module only. Do not let sessions sprawl across module boundaries.

