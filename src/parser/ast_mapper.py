"""AST Static Analysis & Scope Mapper for mapping failure tracebacks to syntax scopes."""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class AstScope:
    file_path: str
    target_line: int
    scope_type: str  # "function", "async_function", "class", or "module"
    scope_name: str
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    enclosing_class: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    code_context: str = ""
    full_source: str = ""


class _ScopeVisitor(ast.NodeVisitor):
    """Traverses AST to identify the tightest enclosing class/function node for a target line."""

    def __init__(self, target_line: int):
        self.target_line = target_line
        self.scope_stack: List[ast.AST] = []
        self.enclosing_node: Optional[ast.AST] = None
        self.enclosing_class_name: Optional[str] = None
        self.imports: List[str] = []

    def visit_Import(self, node: ast.Import):
        names = ", ".join(alias.name for alias in node.names)
        self.imports.append(f"import {names}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        names = ", ".join(alias.name for alias in node.names)
        self.imports.append(f"from {mod} import {names}")
        self.generic_visit(node)

    def _check_node_bounds(self, node: ast.AST) -> bool:
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is not None and end is not None:
            return start <= self.target_line <= end
        return False

    def visit_ClassDef(self, node: ast.ClassDef):
        if self._check_node_bounds(node):
            self.scope_stack.append(node)
            self.generic_visit(node)
            if not self.enclosing_node:
                self.enclosing_node = node
            self.scope_stack.pop()
        else:
            self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if self._check_node_bounds(node):
            self.scope_stack.append(node)
            self.generic_visit(node)
            if not self.enclosing_node:
                self.enclosing_node = node
                # Find if an enclosing class exists in scope_stack
                for parent in self.scope_stack[:-1]:
                    if isinstance(parent, ast.ClassDef):
                        self.enclosing_class_name = parent.name
            self.scope_stack.pop()
        else:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        if self._check_node_bounds(node):
            self.scope_stack.append(node)
            self.generic_visit(node)
            if not self.enclosing_node:
                self.enclosing_node = node
                for parent in self.scope_stack[:-1]:
                    if isinstance(parent, ast.ClassDef):
                        self.enclosing_class_name = parent.name
            self.scope_stack.pop()
        else:
            self.generic_visit(node)


class AstMapper:
    """Maps Python runtime stack trace coordinates to exact Abstract Syntax Tree scopes."""

    def __init__(self, repo_dir: Optional[str] = None):
        self.repo_dir = Path(repo_dir).resolve() if repo_dir else Path.cwd().resolve()

    def analyze_file(
        self,
        file_path: str,
        target_line: int,
        context_window: int = 30,
    ) -> AstScope:
        """Parses a target source file and extracts syntax context around target_line."""
        resolved_path = self._resolve_file_path(file_path)

        if not resolved_path.exists() or not resolved_path.is_file():
            # Return placeholder scope if file is unreadable or external
            return AstScope(
                file_path=file_path,
                target_line=target_line,
                scope_type="module",
                scope_name=Path(file_path).name,
                start_line=max(1, target_line - 15),
                end_line=target_line + 15,
                docstring=None,
                enclosing_class=None,
                imports=[],
                code_context=f"// File {file_path} not found on disk at {resolved_path}",
                full_source="",
            )

        source_code = resolved_path.read_text(encoding="utf-8", errors="replace")
        source_lines = source_code.splitlines()
        total_lines = len(source_lines)

        try:
            tree = ast.parse(source_code, filename=str(resolved_path))
        except SyntaxError:
            # Fallback to simple window extraction if file has a syntax error
            return self._build_fallback_scope(
                file_path=file_path,
                source_lines=source_lines,
                target_line=target_line,
                context_window=context_window,
            )

        visitor = _ScopeVisitor(target_line)
        visitor.visit(tree)

        node = visitor.enclosing_node
        if isinstance(node, ast.AsyncFunctionDef):
            scope_type = "async_function"
            scope_name = node.name
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno)
            docstring = ast.get_docstring(node)
        elif isinstance(node, ast.FunctionDef):
            scope_type = "function"
            scope_name = node.name
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno)
            docstring = ast.get_docstring(node)
        elif isinstance(node, ast.ClassDef):
            scope_type = "class"
            scope_name = node.name
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno)
            docstring = ast.get_docstring(node)
        else:
            scope_type = "module"
            scope_name = resolved_path.stem
            start_line = max(1, target_line - (context_window // 2))
            end_line = min(total_lines, target_line + (context_window // 2))
            docstring = ast.get_docstring(tree)

        # Extract contextual code window
        context_start = max(1, min(start_line, target_line - (context_window // 2)))
        context_end = min(total_lines, max(end_line, target_line + (context_window // 2)))

        context_code = self._format_code_window(source_lines, context_start, context_end, target_line)

        return AstScope(
            file_path=file_path,
            target_line=target_line,
            scope_type=scope_type,
            scope_name=scope_name,
            start_line=start_line,
            end_line=end_line,
            docstring=docstring,
            enclosing_class=visitor.enclosing_class_name,
            imports=visitor.imports,
            code_context=context_code,
            full_source=source_code,
        )

    def _resolve_file_path(self, file_path: str) -> Path:
        """Resolves path against repo_dir or returns absolute."""
        p = Path(file_path)
        if p.is_absolute():
            return p
        return (self.repo_dir / p).resolve()

    def _format_code_window(
        self,
        source_lines: List[str],
        start_line: int,
        end_line: int,
        target_line: int,
    ) -> str:
        """Formats code snippet with 1-based line numbers and pointer markers."""
        formatted = []
        for idx in range(start_line - 1, end_line):
            curr_lineno = idx + 1
            line_str = source_lines[idx] if idx < len(source_lines) else ""
            marker = ">>>" if curr_lineno == target_line else "   "
            formatted.append(f"{marker} {curr_lineno:4d} | {line_str}")
        return "\n".join(formatted)

    def _build_fallback_scope(
        self,
        file_path: str,
        source_lines: List[str],
        target_line: int,
        context_window: int,
    ) -> AstScope:
        """Fallback slice generator when AST parsing fails due to invalid syntax."""
        total_lines = len(source_lines)
        start = max(1, target_line - (context_window // 2))
        end = min(total_lines, target_line + (context_window // 2))
        context_code = self._format_code_window(source_lines, start, end, target_line)

        return AstScope(
            file_path=file_path,
            target_line=target_line,
            scope_type="module",
            scope_name=Path(file_path).stem,
            start_line=start,
            end_line=end,
            docstring=None,
            enclosing_class=None,
            imports=[],
            code_context=context_code,
            full_source="\n".join(source_lines),
        )
