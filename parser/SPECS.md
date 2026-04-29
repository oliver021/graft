# GQL — Graft Query Language Specification

**Version:** 0.1 (draft)
**Status:** Normative for `graft_parser`. All other modules consume the AST defined here.
**Scope:** This document defines the GQL surface language: lexical rules, grammar, semantic model, type system, and the AST contract that `graft_parser` produces for `graft_engine` to consume.

> This spec is the contract. The parser serves it, the engine serves it. If the two disagree, this document is right until edited.

---

## 1. Design Goals

GQL is a **code-aware query language**. It lets a developer ask structural questions about a codebase — *"what calls `eval`?"*, *"which functions are never called?"* — and receive tabular answers.

GQL is **inspired by CodeQL's `from / where / select` shape** and deliberately adopts a familiar silhouette so CodeQL users feel oriented. It is **not a CodeQL dialect**. GQL is an independent language with its own semantics, its own type system, and its own execution model (relational over SQL, not Datalog over a logic engine). Compatibility is aesthetic and pedagogical — not semantic. See §11 for an enumeration of deliberate divergences.

### Non-goals

- Turing completeness. GQL is declarative and deliberately bounded.
- User-defined predicates, classes, or modules (may appear in a later version).
- Full expression evaluation. GQL conditions are filters, not arithmetic.
- Emulating CodeQL's class hierarchy or Datalog recursion beyond a fixed set of built-in recursive predicates (e.g. `callDepth`).

### Guiding principles

1. **Code vocabulary over SQL vocabulary.** The user writes `function.calls`, not `JOIN expressions ON symbol_id`.
2. **One query, one result table.** Every query produces rows with named columns — nothing else.
3. **Total transparency.** The compiled SQL is always recoverable from the query. No hidden rewrites.
4. **Parser purity.** Parsing is a pure string → AST transform. Zero I/O, zero schema awareness.

---

## 2. Lexical Structure

### 2.1 Source encoding

GQL source is UTF-8. Line endings are `\n`, `\r\n`, or `\r`; all are equivalent.

### 2.2 Whitespace and comments

Whitespace (space, tab, newline) separates tokens but is otherwise insignificant.

Two comment forms:

```
-- line comment to end of line
/* block comment, non-nesting */
```

Comments are stripped before parsing.

### 2.3 Tokens

| Token class     | Pattern                                        | Examples                          |
|-----------------|------------------------------------------------|-----------------------------------|
| `KEYWORD`       | Reserved word (see §2.5)                       | `from`, `where`, `select`, `and`  |
| `IDENT`         | `[a-zA-Z_][a-zA-Z0-9_]*`                       | `function`, `calls`, `myAlias`    |
| `STRING`        | `"..."` or `'...'` with `\` escapes            | `"eval"`, `'open'`                |
| `NUMBER`        | `[0-9]+(\.[0-9]+)?`                            | `42`, `3.14`                      |
| `OPERATOR`      | `=`, `!=`, `>`, `<`, `>=`, `<=`                | —                                 |
| `PUNCT`         | `.`, `,`, `(`, `)`, `+`                        | —                                 |

### 2.4 Identifiers

Identifiers are case-sensitive. By convention:

- **Root entities** are lowercase nouns: `function`, `class`, `file`, `expression`.
- **Traversals / predicates** are `camelCase`: `withoutArgs`, `callDepth`, `getDoesThrow`.
- **User aliases** are any identifier, conventionally short lowercase: `fn`, `c`, `m`.

### 2.5 Reserved words

The following identifiers are reserved and may not be used as aliases:

```
from  where  select  as  and  or  not  like  null  true  false
```

Entity and traversal names (e.g. `function`, `calls`) are **not** reserved lexically — they are contextual keywords resolved by the entity registry at semantic-analysis time. A user may not use them as aliases, but the grammar admits them as `IDENT`.

### 2.6 String escapes

Inside string literals: `\"`, `\'`, `\\`, `\n`, `\r`, `\t`. Unknown escapes are a syntax error.

### 2.7 Numeric literals

Integers and non-negative decimal fractions. No hex, octal, scientific notation, or signed literals — a negative number is a unary expression, which GQL does not support in v0.1. If negativity is needed, the engine compares against a string or re-expresses the predicate.

---

## 3. Grammar

Normative grammar (Lark-compatible EBNF). The parser implementation lives in `graft_parser/grammar.lark` and must match this exactly.

```lark
query        : "from" entity_path "as" ALIAS
               ("where" condition)?
               "select" projection

entity_path  : ROOT_ENTITY ("." traversal)*

ROOT_ENTITY  : "function"
             | "class"
             | "file"
             | "expression"

traversal    : WORD "(" arglist? ")"   -> predicate_traversal
             | WORD                    -> field_traversal

arglist      : arg ("," arg)*
arg          : STRING | NUMBER | "null"

condition    : expr (("and" | "or") expr)*

expr         : field OP value          -> binary_expr
             | field "like" STRING     -> like_expr
             | "not" expr              -> not_expr
             | "(" condition ")"       -> grouped_expr

field        : ALIAS "." WORD ("." WORD)*

OP           : "=" | "!=" | ">" | "<" | ">=" | "<="

value        : STRING | NUMBER | "null" | "true" | "false" | field

projection   : proj_item ("," proj_item)*

proj_item    : concat_item
concat_item  : atom_item ("+" atom_item)*
atom_item    : field       -> field_proj
             | STRING      -> literal_proj

ALIAS        : /[a-z_][a-z0-9_]*/
WORD         : /[a-zA-Z_][a-zA-Z0-9_]*/
STRING       : /"[^"]*"/ | /'[^']*'/
NUMBER       : /[0-9]+(\.[0-9]+)?/

%import common.WS
%ignore WS
```

### 3.1 Operator precedence (semantic)

Boolean operators bind as follows (lowest to highest): `or` < `and` < `not`. Parentheses override. `like` is a binary comparison, same precedence as `=`.

String concatenation (`+`) in projections is left-associative. It is **not** a general arithmetic operator — outside `projection`, `+` is a syntax error.

---

## 4. Semantic Model

### 4.1 The entity-traversal paradigm

Every query starts from a **root entity** — a first-class noun in the code domain (`function`, `class`, `file`, `expression`). Dotted segments after the root are **traversals** that move the current row-set along a relation (`function.calls`) or **predicates** that filter it (`function.withoutArgs()`).

The distinction:

- **Field traversal** — no parentheses. Changes the entity of the row-set. `function.calls` moves from rows-of-functions to rows-of-call-expressions. Field traversals chain: `class.methods.calls`.
- **Predicate traversal** — parentheses, possibly arguments. Does **not** change the entity; filters or augments the current row-set. `function.withoutArgs()` keeps rows-of-functions.

An `entity_path` resolves to a **terminal entity** — the thing the alias ultimately names. The alias binds to this terminal entity, and every field reference in `where` / `select` is resolved against it.

### 4.2 The alias

Exactly one alias is bound per query. Multi-entity joins are not supported in v0.1. If a user needs to correlate two entities, they express it through traversal (e.g. `class.methods` reaches methods-of-classes via the class relation) rather than via a second `from` clause.

### 4.3 WHERE semantics

`where` is a boolean filter applied to the terminal row-set. Fields referenced in `where` must:

1. Be qualified by the query's alias (`c.name`, not bare `name`).
2. Resolve to a field known to the terminal entity (per §6).

NULL semantics follow SQL three-valued logic. `x = null` is **never** true — users must write `x = null` via explicit engine-level handling (the engine emits `IS NULL`). The GQL layer accepts `null` as a value; the engine's compiler is responsible for the SQL translation.

### 4.4 SELECT semantics

`select` is a list of projection items. Each item becomes one output column. A column's name is:

- The last path segment of a `field` projection (`c.filename` → column `filename`).
- The verbatim string for `literal_proj` (rarely useful alone, but valid).
- For `concat_item`, an engine-generated synthetic name (`expr_1`, `expr_2`, ...) unless the engine assigns a better one.

Column naming conflicts (two `fn.name` columns) are allowed; downstream consumers see both with disambiguated names (`name`, `name_1`).

### 4.5 Result model

Every successful query yields a `QueryResult` (see CLAUDE.md "Result Model"). This is the only output shape. GQL has no scalar-returning or boolean-returning form.

---

## 5. Type System

GQL is **weakly, structurally typed**. Types are checked at compile time to the extent that field existence is verified; value comparisons defer to the underlying SQL.

### 5.1 Scalar types

| GQL type  | Source                        | Notes                             |
|-----------|-------------------------------|-----------------------------------|
| `string`  | `STRING` literal, most fields | UTF-8                             |
| `int`     | `NUMBER` without `.`          | 64-bit signed in practice         |
| `float`   | `NUMBER` with `.`             | IEEE-754 double                   |
| `bool`    | `true`, `false`               | Only from fields and predicates   |
| `null`    | `null` literal                | Presence-of-value marker          |

### 5.2 Field types

Field types are declared by the entity registry, not inferred. A field's declared type determines:

- Which operators are valid (`like` only on `string`; `>` on numerics and `string`).
- How the engine quotes or casts in SQL.

Type mismatches are **compile-time errors** raised by the engine (not the parser). The parser accepts any operator on any field.

### 5.3 Coercion

There is no implicit coercion. `c.argCount = "3"` is an error, not a comparison-of-strings.

---

## 6. Root Entities (Normative)

Root entities and their fields / traversals are defined in `graft_engine/entity_registry.py`. This section is the authoritative **surface** — the registry may not expose less, and any addition is a language change requiring a spec revision.

### 6.1 `function`

Rows: functions and methods (any named callable scope).

**Fields** (all string unless noted): `name`, `filename`, `language`, `start` (int), `end` (int), `kind`, `signature` (json), `paramCount` (int).

**Field traversals:** `calls`, `callers`, `callees`.

**Predicate traversals:** `calls(name: string)`, `withoutArgs()`, `signature(...)`, `getDoesThrow()`, `withoutCallers()`, `callDepth()`.

### 6.2 `class`

Rows: class symbols.

**Fields:** `name`, `filename`, `language`, `start`, `end`.

**Field traversals:** `methods`, `methods.calls` (chained).

### 6.3 `file`

Rows: indexed files.

**Fields:** `filename`, `language`, `scannedAt`.

**Field traversals:** `functions`, `classes`, `imports`.

### 6.4 `expression`

Rows: raw expressions table.

**Fields:** `kind`, `source`, `filename`, `start`, `end`, `depth`.

### 6.5 Terminal-entity field sets

The fields available on an alias depend on the **terminal entity** of its path, not the root. After `function.calls`, the alias names call-expression rows and exposes call-expression fields (`c.name`, `c.argCount`, `c.isMethod`, `c.enclosing`, `c.filename`, `c.start`, `c.end`, `c.source`) — not function fields.

See CLAUDE.md "Entity Registry" and "Call Expression Fields" for the full table.

---

## 7. Predicate Catalog (v0.1)

Predicates are closed-set in v0.1. Users cannot define new ones. The registry ships with:

| Predicate                  | Entity    | Arity | Meaning                                                         |
|----------------------------|-----------|-------|-----------------------------------------------------------------|
| `withoutArgs()`            | function  | 0     | Parameter list is empty.                                        |
| `signature(...)`           | function  | n     | Positional param match; `null` = wildcard.                      |
| `getDoesThrow()`           | function  | 0     | Body contains a `raise` / `throw` expression.                   |
| `withoutCallers()`         | function  | 0     | No resolved reference targets this symbol.                      |
| `calls(name)`              | function  | 1     | Body contains a call to `name`.                                 |
| `callDepth()`              | function  | 0     | Computes reachable call-graph depth (adds `depth` field).       |

Predicates that *add fields* (like `callDepth()` adding `depth`) extend the terminal entity's field set **for this query only**. The entity registry declares which fields a predicate injects.

---

## 8. AST Contract

The parser produces the dataclasses defined in CLAUDE.md "AST Node Definitions". This spec elevates them to normative status:

- `QueryAST` is the root. Any successful parse yields exactly one.
- `EntityPath.traversals` preserves source order.
- `Condition.expressions` and `Condition.operators` satisfy `len(operators) == len(expressions) - 1`. Operators are stored lowercase (`"and"`, `"or"`).
- `Field.path` is a non-empty list. The first element is the field name; subsequent elements are nested JSON path segments (e.g. `extra.callee`).
- `PredicateTraversal.args` preserves positional order; `None` represents the `null` literal.

Consumers MUST NOT depend on parser-internal types (Lark `Tree`, `Token`) — only the dataclasses above.

### 8.1 Parser error model

On invalid input, the parser raises `GraftParseError` with:

- `message`: human-readable.
- `line`, `column`: 1-indexed source location.
- `snippet`: the offending line with a caret.

The parser never raises for semantic issues (unknown entity, unknown field) — those are the engine's responsibility.

---

## 9. Examples (Normative Canon)

These examples are part of the spec. If a parser change breaks any of them, the spec is broken. Each is annotated with its expected terminal entity.

```sql
-- Terminal: call-expression
from function.calls as c
where c.name = "eval"
select c.filename, c.start, c.enclosing
```

```sql
-- Terminal: function (filtered)
from function.withoutArgs() as fn
select fn.name, fn.filename
```

```sql
-- Terminal: function (with injected `depth` field)
from function.callDepth() as fn
where fn.depth > 4
select fn.name, fn.depth, fn.filename
```

```sql
-- Terminal: method (via class.methods)
from class.methods as m
where m.className = "Calculator"
select m.name, m.start, m.end
```

```sql
-- Terminal: call-expression, OR-condition
from function.calls as c
where c.name like "open" or c.name like "write" or c.name like "read"
select c.enclosing, c.filename, c.start, c.name
```

```sql
-- Projection with concatenation
from function.calls as c
where c.name = "eval"
select c.filename + ":" + c.start, c.enclosing
```

---

## 10. Execution Contract (Informative)

Though strictly outside the parser's purview, this summary pins the shared mental model:

1. Parser: `string → QueryAST`. Pure.
2. Compiler: `QueryAST → LogicalPlan → SQLAlchemy select()`. Pure, needs the registry.
3. Executor: `select() → QueryResult`. Impure; touches the DB.

The generated SQL is surfaced on `QueryResult.query_sql` — an invariant of the system, not an optional feature.

---

## 11. CodeQL Compatibility — Deliberate Divergences

GQL borrows CodeQL's `from / where / select` syntactic silhouette for familiarity. It is not semantically compatible. The following are intentional, not accidental:

| Aspect                       | CodeQL                                        | GQL                                         |
|------------------------------|-----------------------------------------------|---------------------------------------------|
| Execution model              | Datalog over logic engine                     | Relational over SQL                         |
| Declaration form             | `from <Type> <var>`                           | `from <entity-path> as <alias>`             |
| Type hierarchy               | User-defined classes, extends, char-predicates| Closed set of root entities                 |
| Predicates                   | User-defined, first-class, recursive          | Built-in, closed set, method-call syntax    |
| Multiple variables           | `from A a, B b where ...`                     | Single alias (v0.1); traversal for joins    |
| Aggregation                  | `count`, `strictcount`, `any`, `all`          | Not in v0.1                                 |
| Path queries                 | First-class `path-problem` queries            | Not in v0.1                                 |
| String matching              | `matches`, `regexpMatch`                      | `like` only (SQL-like semantics)            |
| Recursion                    | Transitive closure operator `+`, `*`          | Implicit in specific predicates (`callDepth`)|
| Module system                | `import`                                      | No imports; single-file queries             |
| Result shape                 | Tuple relation                                | Single `QueryResult` table                  |

**Design stance.** GQL is meant to read similar enough that a CodeQL user understands a GQL example at first glance, and different enough that nothing ported from CodeQL will silently misbehave. We refuse to emulate CodeQL's semantics with SQL, because the approximation would leak at every recursive or aggregated edge.

### 11.1 Intentionally shared vocabulary

These tokens carry the same *intent* in both languages and are safe anchors for user intuition: `from`, `where`, `select`, `as`, `and`, `or`, `not`, `like`.

### 11.2 Intentionally different vocabulary

GQL avoids CodeQL keywords whose semantics do not transfer: `exists`, `forall`, `forex`, `instanceof`, `order by`, `asc`, `desc`. If these appear in a future GQL version, they carry GQL semantics — not CodeQL semantics.

---

## 12. Versioning and Stability

- **v0.x:** the grammar and entity registry may change without deprecation cycles. Every change is reflected here first.
- **v1.0:** grammar freeze. Additive registry changes only. Removals go through a two-version deprecation.
- AST dataclasses are versioned with the language. Breaking AST changes are breaking language changes.

---

## 13. Appendix A — Reserved for Future Versions (Non-Normative)

Deliberately absent from v0.1 and tracked for later consideration:

- Aggregations: `count`, `sum`, `min`, `max`, `avg`.
- `order by` and `limit`.
- Second alias in `from` (multi-entity joins).
- User-defined predicates.
- Set operations (`union`, `intersect`).
- Negated traversal (`function.not.calls(...)`).
- Transitive closure operator on traversals (`function.calls+`).

Anything not listed here is out of scope indefinitely.

---

*End of specification.*
