"""
Parser tests — all §9 canonical examples are normative:
any change that breaks them breaks the spec.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../parser"))

import pytest
from graft_parser import (
    parse,
    GraftParseError,
    QueryAST,
    EntityPath,
    FieldTraversal,
    PredicateTraversal,
    Field,
    Condition,
    ConditionExpr,
    Projection,
    FieldProjection,
    LiteralProjection,
    ConcatProjection,
)


# ── §9 Canonical examples ─────────────────────────────────────────────────
# These are normative. If they break, the spec is broken.

class TestCanonicalExamples:
    """SPECS.md §9 — every example must parse without error."""

    def test_canon_1_function_calls_filter_on_name(self):
        q = '''
        from function.calls as c
        where c.name = "eval"
        select c.filename, c.start, c.enclosing
        '''
        ast = parse(q)
        assert isinstance(ast, QueryAST)
        assert ast.alias == "c"
        # entity path
        assert ast.entity_path.root == "function"
        assert len(ast.entity_path.traversals) == 1
        assert isinstance(ast.entity_path.traversals[0], FieldTraversal)
        assert ast.entity_path.traversals[0].name == "calls"
        # condition
        assert ast.condition is not None
        assert len(ast.condition.expressions) == 1
        expr = ast.condition.expressions[0]
        assert expr.field == Field(alias="c", path=["name"])
        assert expr.op == "="
        assert expr.value == "eval"
        assert expr.negated is False
        # projection
        assert len(ast.projection.items) == 3
        cols = [p.field.path[-1] for p in ast.projection.items]
        assert cols == ["filename", "start", "enclosing"]

    def test_canon_2_without_args_predicate(self):
        q = '''
        from function.withoutArgs() as fn
        select fn.name, fn.filename
        '''
        ast = parse(q)
        assert ast.alias == "fn"
        assert ast.entity_path.root == "function"
        assert len(ast.entity_path.traversals) == 1
        trav = ast.entity_path.traversals[0]
        assert isinstance(trav, PredicateTraversal)
        assert trav.name == "withoutArgs"
        assert trav.args == []
        assert ast.condition is None
        assert len(ast.projection.items) == 2

    def test_canon_3_call_depth_predicate(self):
        q = '''
        from function.callDepth() as fn
        where fn.depth > 4
        select fn.name, fn.depth, fn.filename
        '''
        ast = parse(q)
        assert ast.alias == "fn"
        trav = ast.entity_path.traversals[0]
        assert isinstance(trav, PredicateTraversal)
        assert trav.name == "callDepth"
        # condition
        expr = ast.condition.expressions[0]
        assert expr.op == ">"
        assert expr.value == 4
        assert expr.field == Field(alias="fn", path=["depth"])
        # projection: name, depth, filename
        assert len(ast.projection.items) == 3

    def test_canon_4_class_methods_traversal(self):
        q = '''
        from class.methods as m
        where m.className = "Calculator"
        select m.name, m.start, m.end
        '''
        ast = parse(q)
        assert ast.entity_path.root == "class"
        assert ast.entity_path.traversals[0].name == "methods"
        assert ast.alias == "m"
        expr = ast.condition.expressions[0]
        assert expr.value == "Calculator"
        assert len(ast.projection.items) == 3

    def test_canon_5_or_condition(self):
        q = '''
        from function.calls as c
        where c.name like "open" or c.name like "write" or c.name like "read"
        select c.enclosing, c.filename, c.start, c.name
        '''
        ast = parse(q)
        cond = ast.condition
        assert len(cond.expressions) == 3
        assert cond.operators == ["or", "or"]
        for expr in cond.expressions:
            assert expr.op == "like"
        assert len(ast.projection.items) == 4

    def test_canon_6_concat_projection(self):
        q = '''
        from function.calls as c
        where c.name = "eval"
        select c.filename + ":" + c.start, c.enclosing
        '''
        ast = parse(q)
        items = ast.projection.items
        assert len(items) == 2
        # First item is a ConcatProjection tree
        assert isinstance(items[0], ConcatProjection)
        assert isinstance(items[1], FieldProjection)
        assert items[1].field.path == ["enclosing"]


# ── Entity path ────────────────────────────────────────────────────────────

class TestEntityPath:

    def test_root_entities(self):
        for root in ("function", "class", "file", "expression"):
            ast = parse(f"from {root} as x select x.name")
            assert ast.entity_path.root == root

    def test_no_traversals(self):
        ast = parse("from function as fn select fn.name")
        assert ast.entity_path.traversals == []

    def test_single_field_traversal(self):
        ast = parse("from function.calls as c select c.name")
        assert len(ast.entity_path.traversals) == 1
        assert isinstance(ast.entity_path.traversals[0], FieldTraversal)
        assert ast.entity_path.traversals[0].name == "calls"

    def test_chained_field_traversals(self):
        ast = parse("from class.methods.calls as c select c.name")
        travs = ast.entity_path.traversals
        assert len(travs) == 2
        assert travs[0].name == "methods"
        assert travs[1].name == "calls"

    def test_predicate_no_args(self):
        ast = parse("from function.withoutCallers() as fn select fn.name")
        trav = ast.entity_path.traversals[0]
        assert isinstance(trav, PredicateTraversal)
        assert trav.name == "withoutCallers"
        assert trav.args == []

    def test_predicate_with_string_arg(self):
        ast = parse('from function.calls("eval") as c select c.name')
        trav = ast.entity_path.traversals[0]
        assert isinstance(trav, PredicateTraversal)
        assert trav.args == ["eval"]

    def test_predicate_with_null_arg(self):
        ast = parse("from function.signature(null) as fn select fn.name")
        trav = ast.entity_path.traversals[0]
        assert trav.args == [None]

    def test_predicate_with_number_arg(self):
        ast = parse("from function.signature(3) as fn select fn.name")
        trav = ast.entity_path.traversals[0]
        assert trav.args == [3]

    def test_predicate_with_multiple_args(self):
        ast = parse('from function.signature("x", null, "str") as fn select fn.name')
        trav = ast.entity_path.traversals[0]
        assert trav.args == ["x", None, "str"]

    def test_traversal_order_preserved(self):
        ast = parse("from file.functions as fn select fn.name")
        assert ast.entity_path.traversals[0].name == "functions"


# ── WHERE condition ─────────────────────────────────────────────────────────

class TestCondition:

    def test_no_where_clause(self):
        ast = parse("from function as fn select fn.name")
        assert ast.condition is None

    def test_equality(self):
        ast = parse('from function as fn where fn.name = "foo" select fn.name')
        expr = ast.condition.expressions[0]
        assert expr.op == "="
        assert expr.value == "foo"

    def test_not_equal(self):
        ast = parse('from function as fn where fn.kind != "lambda" select fn.name')
        assert ast.condition.expressions[0].op == "!="

    def test_greater_than(self):
        ast = parse("from function as fn where fn.start > 10 select fn.name")
        expr = ast.condition.expressions[0]
        assert expr.op == ">"
        assert expr.value == 10

    def test_greater_equal(self):
        ast = parse("from function as fn where fn.start >= 10 select fn.name")
        assert ast.condition.expressions[0].op == ">="

    def test_less_than(self):
        ast = parse("from function as fn where fn.end < 100 select fn.name")
        assert ast.condition.expressions[0].op == "<"

    def test_less_equal(self):
        ast = parse("from function as fn where fn.end <= 100 select fn.name")
        assert ast.condition.expressions[0].op == "<="

    def test_like(self):
        ast = parse('from function as fn where fn.name like "get%" select fn.name')
        expr = ast.condition.expressions[0]
        assert expr.op == "like"
        assert expr.value == "get%"

    def test_null_value(self):
        ast = parse("from function as fn where fn.signature = null select fn.name")
        assert ast.condition.expressions[0].value is None

    def test_true_value(self):
        ast = parse("from function as fn where fn.isPublic = true select fn.name")
        assert ast.condition.expressions[0].value is True

    def test_false_value(self):
        ast = parse("from function as fn where fn.isPublic = false select fn.name")
        assert ast.condition.expressions[0].value is False

    def test_field_to_field_comparison(self):
        ast = parse("from function as fn where fn.start = fn.end select fn.name")
        expr = ast.condition.expressions[0]
        assert isinstance(expr.value, Field)
        assert expr.value.path == ["end"]

    def test_and_condition(self):
        ast = parse(
            'from function as fn where fn.name = "foo" and fn.start > 5 select fn.name'
        )
        cond = ast.condition
        assert len(cond.expressions) == 2
        assert cond.operators == ["and"]

    def test_or_condition(self):
        ast = parse(
            'from function as fn where fn.name = "a" or fn.name = "b" select fn.name'
        )
        assert ast.condition.operators == ["or"]

    def test_three_terms_mixed(self):
        ast = parse(
            'from function as fn '
            'where fn.a = "x" and fn.b = "y" or fn.c = "z" '
            'select fn.name'
        )
        cond = ast.condition
        assert len(cond.expressions) == 3
        assert cond.operators == ["and", "or"]

    def test_not_negates_expression(self):
        ast = parse(
            'from function as fn where not fn.name = "foo" select fn.name'
        )
        expr = ast.condition.expressions[0]
        assert expr.negated is True
        assert expr.op == "="

    def test_not_not_double_negation(self):
        ast = parse(
            'from function as fn where not not fn.name = "foo" select fn.name'
        )
        expr = ast.condition.expressions[0]
        assert expr.negated is False

    def test_number_integer(self):
        ast = parse("from function as fn where fn.paramCount > 5 select fn.name")
        assert ast.condition.expressions[0].value == 5
        assert isinstance(ast.condition.expressions[0].value, int)

    def test_number_float(self):
        ast = parse("from function as fn where fn.score > 3.14 select fn.name")
        assert abs(ast.condition.expressions[0].value - 3.14) < 1e-9

    def test_single_quoted_string(self):
        ast = parse("from function as fn where fn.name = 'hello' select fn.name")
        assert ast.condition.expressions[0].value == "hello"

    def test_nested_field_path(self):
        ast = parse('from function as fn where fn.extra.callee = "open" select fn.name')
        expr = ast.condition.expressions[0]
        assert expr.field.path == ["extra", "callee"]

    def test_grouped_single_expr_allowed(self):
        ast = parse(
            'from function as fn where (fn.name = "foo") select fn.name'
        )
        assert ast.condition.expressions[0].value == "foo"


# ── SELECT projection ────────────────────────────────────────────────────────

class TestProjection:

    def test_single_field(self):
        ast = parse("from function as fn select fn.name")
        items = ast.projection.items
        assert len(items) == 1
        assert isinstance(items[0], FieldProjection)
        assert items[0].field == Field(alias="fn", path=["name"])

    def test_multiple_fields(self):
        ast = parse("from function as fn select fn.name, fn.filename, fn.start")
        assert len(ast.projection.items) == 3

    def test_literal_projection(self):
        ast = parse('from function as fn select "hello", fn.name')
        assert isinstance(ast.projection.items[0], LiteralProjection)
        assert ast.projection.items[0].value == "hello"

    def test_simple_concat(self):
        ast = parse('from function as fn select fn.filename + ":" + fn.start')
        item = ast.projection.items[0]
        assert isinstance(item, ConcatProjection)
        # left side is itself a ConcatProjection (left-associative)
        assert isinstance(item.left, ConcatProjection)
        assert isinstance(item.left.left, FieldProjection)
        assert isinstance(item.left.right, LiteralProjection)
        assert item.left.right.value == ":"
        assert isinstance(item.right, FieldProjection)

    def test_concat_is_left_associative(self):
        ast = parse('from function as fn select "a" + "b" + "c"')
        item = ast.projection.items[0]
        # (("a" + "b") + "c")
        assert isinstance(item, ConcatProjection)
        assert isinstance(item.left, ConcatProjection)
        assert isinstance(item.left.left, LiteralProjection)
        assert item.left.left.value == "a"

    def test_nested_field_path_in_projection(self):
        ast = parse("from function as fn select fn.extra.callee")
        field = ast.projection.items[0].field
        assert field.path == ["extra", "callee"]


# ── Comments ────────────────────────────────────────────────────────────────

class TestComments:

    def test_line_comment_ignored(self):
        q = '''
        -- find all eval calls
        from function.calls as c
        where c.name = "eval"  -- the dangerous one
        select c.filename, c.start
        '''
        ast = parse(q)
        assert ast.condition.expressions[0].value == "eval"

    def test_block_comment_ignored(self):
        q = '''
        /* audit query */
        from function.calls as c
        where c.name = "eval"
        select /* just these two */ c.filename, c.start
        '''
        ast = parse(q)
        assert len(ast.projection.items) == 2

    def test_block_comment_inline(self):
        ast = parse('from function as fn /* root */ select fn.name')
        assert ast.entity_path.root == "function"


# ── String escapes ────────────────────────────────────────────────────────

class TestStringEscapes:

    def test_escaped_double_quote(self):
        ast = parse('from function as fn where fn.name = "say \\"hi\\"" select fn.name')
        assert ast.condition.expressions[0].value == 'say "hi"'

    def test_escaped_newline(self):
        ast = parse('from function as fn where fn.name = "a\\nb" select fn.name')
        assert ast.condition.expressions[0].value == "a\nb"

    def test_escaped_tab(self):
        ast = parse('from function as fn where fn.name = "a\\tb" select fn.name')
        assert ast.condition.expressions[0].value == "a\tb"

    def test_single_quote_string_escape(self):
        ast = parse("from function as fn where fn.name = 'it\\'s' select fn.name")
        assert ast.condition.expressions[0].value == "it's"


# ── Alias binding ─────────────────────────────────────────────────────────

class TestAlias:

    def test_alias_used_in_condition_field(self):
        ast = parse('from function as fn where fn.name = "x" select fn.name')
        assert ast.condition.expressions[0].field.alias == "fn"

    def test_alias_used_in_projection(self):
        ast = parse('from function as fn select fn.name, fn.start')
        for item in ast.projection.items:
            assert item.field.alias == "fn"

    def test_alias_single_letter(self):
        ast = parse("from class.methods as m select m.name")
        assert ast.alias == "m"


# ── Whitespace tolerance ───────────────────────────────────────────────────

class TestWhitespace:

    def test_single_line(self):
        ast = parse('from function as fn where fn.name = "x" select fn.name')
        assert ast.alias == "fn"

    def test_extra_newlines(self):
        ast = parse('\n\nfrom function as fn\n\nselect fn.name\n\n')
        assert ast.alias == "fn"

    def test_tabs_and_spaces(self):
        ast = parse('\tfrom\tfunction\tas\tfn\tselect\tfn.name')
        assert ast.alias == "fn"


# ── AST contract invariants (SPECS.md §8) ────────────────────────────────

class TestASTContract:

    def test_condition_operator_length_invariant(self):
        ast = parse(
            'from function as fn '
            'where fn.a = "x" and fn.b = "y" and fn.c = "z" '
            'select fn.name'
        )
        cond = ast.condition
        assert len(cond.operators) == len(cond.expressions) - 1

    def test_operators_are_lowercase(self):
        # Even if user writes AND/OR (grammar is case-sensitive, but verify no upper leaks)
        ast = parse('from function as fn where fn.a = "x" and fn.b = "y" select fn.name')
        assert all(op in ("and", "or") for op in ast.condition.operators)

    def test_field_path_is_nonempty(self):
        ast = parse('from function as fn where fn.name = "x" select fn.name')
        assert len(ast.condition.expressions[0].field.path) >= 1
        assert len(ast.projection.items[0].field.path) >= 1

    def test_predicate_args_preserves_order(self):
        ast = parse('from function.signature("str", null, "int") as fn select fn.name')
        args = ast.entity_path.traversals[0].args
        assert args == ["str", None, "int"]

    def test_traversal_order_is_preserved(self):
        ast = parse("from class.methods.calls as c select c.name")
        names = [t.name for t in ast.entity_path.traversals]
        assert names == ["methods", "calls"]

    def test_null_in_args_is_none(self):
        ast = parse("from function.signature(null) as fn select fn.name")
        assert ast.entity_path.traversals[0].args[0] is None

    def test_parse_returns_query_ast(self):
        ast = parse("from function as fn select fn.name")
        assert isinstance(ast, QueryAST)


# ── Error handling (SPECS.md §8.1) ───────────────────────────────────────

class TestErrors:

    def test_missing_from_raises(self):
        with pytest.raises(GraftParseError):
            parse("function as fn select fn.name")

    def test_missing_select_raises(self):
        with pytest.raises(GraftParseError):
            parse("from function as fn")

    def test_missing_as_raises(self):
        with pytest.raises(GraftParseError):
            parse("from function fn select fn.name")

    def test_invalid_root_entity_raises(self):
        with pytest.raises(GraftParseError):
            parse("from module as m select m.name")

    def test_empty_string_raises(self):
        with pytest.raises(GraftParseError):
            parse("")

    def test_incomplete_condition_raises(self):
        with pytest.raises(GraftParseError):
            parse('from function as fn where fn.name select fn.name')

    def test_error_has_line_and_column(self):
        try:
            parse("from function fn select fn.name")
        except GraftParseError as e:
            assert e.line >= 1
            assert e.column >= 1
        else:
            pytest.fail("GraftParseError not raised")

    def test_parser_never_raises_plain_exception(self):
        # Any error from parse() must be GraftParseError, never raw Lark/Exception
        bad_inputs = [
            "not a query",
            "from",
            "select fn.name",
            "from function as fn where",
            "from function as fn select",
        ]
        for src in bad_inputs:
            with pytest.raises(GraftParseError):
                parse(src)

    def test_nested_grouped_condition_raises(self):
        with pytest.raises(GraftParseError):
            parse(
                'from function as fn '
                'where (fn.a = "x" and fn.b = "y") '
                'select fn.name'
            )
