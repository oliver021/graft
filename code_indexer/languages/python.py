"""
Python language adapter.

Uses direct CST traversal (node.children, child_by_field_name) instead of
Query.matches(), which is not available in all tree-sitter binding versions.

Node types relied upon (Python grammar):
  module                  — root node
  function_definition     — name, parameters, return_type, body
  class_definition        — name, body
  decorated_definition    — definition (wraps function/class)
  parameters              — children: typed_parameter, default_parameter, etc.
  block                   — body of a function or class
  call                    — function, arguments
  assignment              — left, right
  augmented_assignment    — left, right
  binary_operator         — left, operator, right
  return_statement        — (expression child)
  import_statement        — (dotted_name children)
  import_from_statement   — module_name, name
  expression_statement    — wraps standalone expressions

Node.text returns bytes — decode with .decode("utf-8").
Node.start_point / end_point are (row, col) tuples; row is 0-indexed.
"""

from __future__ import annotations

from typing import Any

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node

from code_indexer.languages.base import (
    LanguageAdapter,
    RawExpression,
    RawExtraction,
    RawReference,
    RawSymbol,
)

PY_LANGUAGE = Language(tspython.language())

FUNCTION_TYPES = frozenset({"function_definition", "async_function_definition"})
CLASS_TYPES = frozenset({"class_definition"})


class PythonAdapter(LanguageAdapter):
    LANGUAGE_NAME = "python"
    FILE_EXTENSIONS = [".py", ".pyw"]

    def __init__(self) -> None:
        self._parser = Parser(PY_LANGUAGE)

    def extract(self, source: str) -> RawExtraction:
        source_bytes = source.encode("utf-8")
        tree = self._parser.parse(source_bytes)
        root = tree.root_node
        extraction = RawExtraction()
        self._collect_symbols(root, extraction, parent_symbol_key=None, class_key=None)
        return extraction

    def _collect_symbols(
        self,
        node: Node,
        extraction: RawExtraction,
        parent_symbol_key: str | None,
        class_key: str | None,
    ) -> None:
        for child in node.children:
            effective = child
            if child.type == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner:
                    effective = inner

            if effective.type in CLASS_TYPES:
                self._handle_class(effective, extraction, parent_symbol_key)
            elif effective.type in FUNCTION_TYPES:
                self._handle_function(effective, extraction, parent_symbol_key, class_key)
            else:
                self._collect_symbols(effective, extraction, parent_symbol_key, class_key)

    def _handle_class(
        self,
        node: Node,
        extraction: RawExtraction,
        parent_symbol_key: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        local_key = self._make_local_key(node)
        extraction.symbols.append(RawSymbol(
            name=self._text(name_node),
            kind="class",
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            local_key=local_key,
            parent_local_key=parent_symbol_key,
        ))
        body = node.child_by_field_name("body")
        if body:
            self._collect_symbols(body, extraction, parent_symbol_key=local_key, class_key=local_key)

    def _handle_function(
        self,
        node: Node,
        extraction: RawExtraction,
        parent_symbol_key: str | None,
        class_key: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        local_key = self._make_local_key(node)
        kind = "method" if class_key is not None else "function"
        params_node = node.child_by_field_name("parameters")
        return_type_node = node.child_by_field_name("return_type")
        extraction.symbols.append(RawSymbol(
            name=self._text(name_node),
            kind=kind,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=self._extract_signature(params_node, return_type_node),
            local_key=local_key,
            parent_local_key=parent_symbol_key,
        ))
        body = node.child_by_field_name("body")
        if body:
            self._walk_body(body, extraction, local_key, None, 0, 8)
            # Collect nested functions/classes inside body
            self._collect_symbols(body, extraction, parent_symbol_key=local_key, class_key=None)

    def _walk_body(
        self,
        node: Node,
        extraction: RawExtraction,
        symbol_local_key: str,
        parent_expr_key: str | None,
        depth: int,
        max_depth: int,
    ) -> None:
        if depth > max_depth:
            return

        t = node.type

        # Unwrap expression_statement wrapper
        if t == "expression_statement" and node.named_children:
            self._walk_body(node.named_children[0], extraction, symbol_local_key, parent_expr_key, depth, max_depth)
            return

        # Skip nested scopes — handled by _collect_symbols
        if t in FUNCTION_TYPES or t in CLASS_TYPES or t == "decorated_definition":
            return

        if t == "call":
            local_key = self._make_local_key(node)
            callee_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            callee_text = self._text(callee_node) if callee_node else ""
            arg_count = len(args_node.named_children) if args_node else 0
            extraction.expressions.append(RawExpression(
                kind="call",
                source_text=self._text(node),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                depth=depth,
                symbol_local_key=symbol_local_key,
                parent_local_key=parent_expr_key,
                local_key=local_key,
                extra={"callee": callee_text, "arg_count": arg_count, "is_method": "." in callee_text},
            ))
            extraction.references.append(RawReference(
                callee_name=callee_text,
                from_symbol_local_key=symbol_local_key,
                expression_local_key=local_key,
            ))
            if args_node:
                for child in args_node.named_children:
                    self._walk_body(child, extraction, symbol_local_key, local_key, depth + 1, max_depth)
            return

        if t in ("assignment", "augmented_assignment"):
            local_key = self._make_local_key(node)
            left_node = node.child_by_field_name("left")
            extraction.expressions.append(RawExpression(
                kind="assignment",
                source_text=self._text(node),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                depth=depth,
                symbol_local_key=symbol_local_key,
                parent_local_key=parent_expr_key,
                local_key=local_key,
                extra={"target": self._text(left_node) if left_node else "", "is_augmented": t == "augmented_assignment"},
            ))
            right_node = node.child_by_field_name("right")
            if right_node:
                self._walk_body(right_node, extraction, symbol_local_key, local_key, depth + 1, max_depth)
            return

        if t == "binary_operator":
            local_key = self._make_local_key(node)
            op = next((self._text(c) for c in node.children if not c.is_named), "")
            extraction.expressions.append(RawExpression(
                kind="binary",
                source_text=self._text(node),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                depth=depth,
                symbol_local_key=symbol_local_key,
                parent_local_key=parent_expr_key,
                local_key=local_key,
                extra={"operator": op},
            ))
            for child in node.named_children:
                self._walk_body(child, extraction, symbol_local_key, local_key, depth + 1, max_depth)
            return

        if t == "return_statement":
            local_key = self._make_local_key(node)
            extraction.expressions.append(RawExpression(
                kind="return",
                source_text=self._text(node),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                depth=depth,
                symbol_local_key=symbol_local_key,
                parent_local_key=parent_expr_key,
                local_key=local_key,
            ))
            for child in node.named_children:
                self._walk_body(child, extraction, symbol_local_key, local_key, depth + 1, max_depth)
            return

        if t == "raise_statement":
            local_key = self._make_local_key(node)
            extraction.expressions.append(RawExpression(
                kind="raise",
                source_text=self._text(node),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                depth=depth,
                symbol_local_key=symbol_local_key,
                parent_local_key=parent_expr_key,
                local_key=local_key,
            ))
            for child in node.named_children:
                self._walk_body(child, extraction, symbol_local_key, local_key, depth + 1, max_depth)
            return

        if t in ("import_statement", "import_from_statement"):
            local_key = self._make_local_key(node)
            module = ""
            if t == "import_from_statement":
                mod_node = node.child_by_field_name("module_name")
                module = self._text(mod_node) if mod_node else ""
            else:
                for child in node.named_children:
                    if child.type == "dotted_name":
                        module = self._text(child)
                        break
                    if child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        module = self._text(name_node) if name_node else ""
                        break
            extraction.expressions.append(RawExpression(
                kind="import",
                source_text=self._text(node),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                depth=depth,
                symbol_local_key=symbol_local_key,
                parent_local_key=parent_expr_key,
                local_key=local_key,
                extra={"module": module},
            ))
            return

        # Default: recurse into named children
        for child in node.named_children:
            self._walk_body(child, extraction, symbol_local_key, parent_expr_key, depth + 1, max_depth)

    def _extract_signature(
        self,
        params_node: Node | None,
        return_type_node: Node | None,
    ) -> dict[str, Any]:
        params: list[dict[str, Any]] = []
        if params_node:
            for child in params_node.named_children:
                t = child.type
                param: dict[str, Any] = {}
                if t == "identifier":
                    param["name"] = self._text(child)
                elif t in ("typed_parameter", "typed_default_parameter"):
                    name_node = next((c for c in child.named_children if c.type == "identifier"), None)
                    type_node = child.child_by_field_name("type")
                    default_node = child.child_by_field_name("default")
                    param["name"] = self._text(name_node) if name_node else ""
                    param["type"] = self._text(type_node) if type_node else None
                    param["default"] = self._text(default_node) if default_node else None
                elif t == "default_parameter":
                    name_node = child.child_by_field_name("name")
                    default_node = child.child_by_field_name("value")
                    param["name"] = self._text(name_node) if name_node else ""
                    param["default"] = self._text(default_node) if default_node else None
                elif t == "list_splat_pattern":
                    inner = child.named_children[0] if child.named_children else None
                    param["name"] = "*" + (self._text(inner) if inner else "")
                elif t == "dictionary_splat_pattern":
                    inner = child.named_children[0] if child.named_children else None
                    param["name"] = "**" + (self._text(inner) if inner else "")
                else:
                    continue
                if param:
                    params.append(param)
        return {"params": params, "return_type": self._text(return_type_node) if return_type_node else None}

    def _text(self, node: Node) -> str:
        if node is None:
            return ""
        raw = node.text
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    def _make_local_key(self, node: Node) -> str:
        return f"{node.start_byte}:{node.end_byte}"
