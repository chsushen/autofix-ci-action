"""Unit tests for the AST Mapper module."""

import tempfile
from pathlib import Path
import pytest

from src.parser.ast_mapper import AstMapper


@pytest.fixture
def sample_python_file():
    code = '''"""Module level docstring."""

import os
from sys import version_info

GLOBAL_CONST = 100


def standalone_func(x: int) -> int:
    """Computes square."""
    res = x * x
    return res


class DataProcessor:
    """Processor class docstring."""

    def __init__(self, name: str):
        self.name = name

    def process_item(self, item: dict) -> bool:
        """Process item logic."""
        if not item:
            raise ValueError("empty item")
        return True


async def async_worker(task_id: str):
    """Async background task."""
    return f"done_{task_id}"
'''
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as f:
        f.write(code)
        path = f.name

    yield path

    Path(path).unlink(missing_ok=True)


def test_standalone_function_scope(sample_python_file):
    mapper = AstMapper()
    # Line 11 is "res = x * x" inside standalone_func
    scope = mapper.analyze_file(sample_python_file, target_line=11)

    assert scope.scope_type == "function"
    assert scope.scope_name == "standalone_func"
    assert scope.start_line == 9
    assert scope.docstring == "Computes square."
    assert scope.enclosing_class is None
    assert ">>>   11 |     res = x * x" in scope.code_context
    assert any("import os" in imp for imp in scope.imports)


def test_class_method_scope(sample_python_file):
    mapper = AstMapper()
    # Line 23 is "raise ValueError('empty item')" inside process_item
    scope = mapper.analyze_file(sample_python_file, target_line=23)

    assert scope.scope_type == "function"
    assert scope.scope_name == "process_item"
    assert scope.enclosing_class == "DataProcessor"
    assert scope.docstring == "Process item logic."


def test_async_function_scope(sample_python_file):
    mapper = AstMapper()
    # Line 29 is "return f'done_{task_id}'" inside async_worker
    scope = mapper.analyze_file(sample_python_file, target_line=29)

    assert scope.scope_type == "async_function"
    assert scope.scope_name == "async_worker"
    assert scope.docstring == "Async background task."


def test_module_level_scope(sample_python_file):
    mapper = AstMapper()
    # Line 6 is "GLOBAL_CONST = 100"
    scope = mapper.analyze_file(sample_python_file, target_line=6)

    assert scope.scope_type == "module"
    assert scope.docstring == "Module level docstring."


def test_missing_file_fallback():
    mapper = AstMapper()
    scope = mapper.analyze_file("non_existent_file_999.py", target_line=10)

    assert scope.scope_type == "module"
    assert "not found on disk" in scope.code_context


def test_syntax_error_file_fallback():
    broken_code = """
def broken_syntax(
    # Missing closing paren and body
"""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py") as f:
        f.write(broken_code)
        path = f.name

    try:
        mapper = AstMapper()
        scope = mapper.analyze_file(path, target_line=2)
        assert scope.scope_type == "module"
        assert "broken_syntax" in scope.code_context
    finally:
        Path(path).unlink(missing_ok=True)
