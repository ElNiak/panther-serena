"""Unit tests for Ivy tool helper functions (no Ivy toolchain required)."""

import os

import pytest

from serena.tools.ivy_tools import (
    _check_structural_issues,
    _parse_ivy_check_output,
    _require_ivy_tool,
    _validate_ivy_path,
)

REPO_DIR = os.path.join(os.path.dirname(__file__), "../../resources/repos/ivy")


@pytest.mark.ivy
class TestParseIvyCheckOutput:
    def test_parses_error_line(self) -> None:
        output = "model.ivy:10: error: type mismatch\n"
        result = _parse_ivy_check_output(output)
        assert len(result) == 1
        assert result[0]["file"] == "model.ivy"
        assert result[0]["line"] == 10
        assert result[0]["severity"] == "error"
        assert result[0]["message"] == "type mismatch"

    def test_parses_warning_line(self) -> None:
        output = "model.ivy:5: warning: unused variable\n"
        result = _parse_ivy_check_output(output)
        assert len(result) == 1
        assert result[0]["severity"] == "warning"

    def test_parses_multiple_diagnostics(self) -> None:
        output = (
            "a.ivy:1: error: missing type\n"
            "b.ivy:2: warning: shadowed name\n"
            "some other output line\n"
            "a.ivy:10: error: undeclared\n"
        )
        result = _parse_ivy_check_output(output)
        assert len(result) == 3

    def test_ignores_non_matching_lines(self) -> None:
        output = "Checking model...\nDone.\n"
        result = _parse_ivy_check_output(output)
        assert result == []

    def test_empty_input(self) -> None:
        assert _parse_ivy_check_output("") == []


@pytest.mark.ivy
class TestCheckStructuralIssues:
    def test_missing_lang_header(self) -> None:
        source = "object foo = { }"
        diags = _check_structural_issues(source, "/tmp/test.ivy")
        warnings = [d for d in diags if d["message"].startswith("Missing")]
        assert len(warnings) == 1

    def test_valid_lang_header(self) -> None:
        source = "#lang ivy1.7\nobject foo = { }"
        diags = _check_structural_issues(source, "/tmp/test.ivy")
        warnings = [d for d in diags if d["message"].startswith("Missing")]
        assert len(warnings) == 0

    def test_unmatched_opening_brace(self) -> None:
        source = "#lang ivy1.7\nobject foo = {\n  type t\n"
        diags = _check_structural_issues(source, "/tmp/test.ivy")
        errors = [d for d in diags if d["severity"] == "error"]
        assert any("unclosed" in d["message"].lower() for d in errors)

    def test_unmatched_closing_brace(self) -> None:
        source = "#lang ivy1.7\n}\n"
        diags = _check_structural_issues(source, "/tmp/test.ivy")
        errors = [d for d in diags if d["severity"] == "error"]
        assert any("closing brace" in d["message"].lower() for d in errors)

    def test_balanced_braces(self) -> None:
        source = "#lang ivy1.7\nobject foo = {\n  type t\n}\n"
        diags = _check_structural_issues(source, "/tmp/test.ivy")
        brace_errors = [d for d in diags if "brace" in d["message"].lower()]
        assert brace_errors == []

    def test_unresolved_include(self) -> None:
        source = "#lang ivy1.7\ninclude nonexistent\n"
        diags = _check_structural_issues(source, "/tmp/test.ivy")
        inc_warns = [d for d in diags if "unresolved" in d["message"].lower()]
        assert len(inc_warns) == 1

    def test_resolved_include(self) -> None:
        """Uses the test resource repo where helper.ivy exists."""
        sample_path = os.path.join(REPO_DIR, "sample.ivy")
        source = "#lang ivy1.7\ninclude helper\n"
        diags = _check_structural_issues(source, sample_path)
        inc_warns = [d for d in diags if "unresolved" in d["message"].lower()]
        assert inc_warns == []


@pytest.mark.ivy
class TestValidateIvyPath:
    def test_valid_path(self) -> None:
        abs_path = _validate_ivy_path(REPO_DIR, "sample.ivy")
        assert abs_path.endswith("sample.ivy")
        assert os.path.isabs(abs_path)

    def test_rejects_non_ivy_extension(self) -> None:
        with pytest.raises(ValueError, match="Expected an .ivy file"):
            _validate_ivy_path(REPO_DIR, "sample.py")

    def test_rejects_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError, match="Ivy file not found"):
            _validate_ivy_path(REPO_DIR, "does_not_exist.ivy")


@pytest.mark.ivy
class TestRequireIvyTool:
    def test_rejects_missing_tool(self) -> None:
        with pytest.raises(FileNotFoundError, match="not installed"):
            _require_ivy_tool("ivy_nonexistent_tool_abc123")
