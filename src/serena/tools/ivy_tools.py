"""
Ivy-specific tools for formal verification operations.

These tools provide direct access to the Ivy toolchain (ivy_check, ivyc,
ivy_show) through the Serena tool framework, enabling LLM agents to perform
formal verification tasks on Ivy models.
"""

import json
import logging
import os
import os.path
import pathlib
import re
import shlex
import shutil
import time
from typing import TYPE_CHECKING, Any

from serena.tools import Tool, ToolMarkerOptional, ToolMarkerSymbolicRead
from serena.util.shell import execute_shell_command

if TYPE_CHECKING:
    from solidlsp.language_servers.ivy_language_server import IvyLanguageServer

log = logging.getLogger(__name__)


def _get_ivy_language_server(agent: Any) -> "IvyLanguageServer | None":  # noqa: UP007
    """Resolve the IvyLanguageServer instance from the agent, or None."""
    from solidlsp.language_servers.ivy_language_server import IvyLanguageServer

    if not agent.is_using_language_server():
        return None
    try:
        ls_manager = agent.get_language_server_manager_or_raise()
        ls = ls_manager.get_language_server("probe.ivy")
        return ls if isinstance(ls, IvyLanguageServer) else None
    except Exception:
        return None


def _parse_ivy_check_output(output: str) -> list[dict[str, Any]]:
    """Parse ivy_check stderr/stdout into structured diagnostics.

    Looks for lines matching: filename:LINE: error|warning: message
    Returns a list of dicts with file, line, severity, message keys.
    """
    diagnostics: list[dict[str, Any]] = []
    for line in output.splitlines():
        m = re.match(r"(.*?):(\d+):\s*(error|warning):\s*(.*)", line)
        if m:
            diagnostics.append(
                {
                    "file": m.group(1),
                    "line": int(m.group(2)),
                    "severity": m.group(3),
                    "message": m.group(4),
                }
            )
    return diagnostics


def _validate_ivy_path(project_root: str, relative_path: str) -> str:
    """Validate relative_path is an existing .ivy file and return its absolute path."""
    if not relative_path.endswith(".ivy"):
        raise ValueError(f"Expected an .ivy file, got: {relative_path}")
    abs_path = os.path.join(project_root, relative_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Ivy file not found: {relative_path}")
    return abs_path


def _require_ivy_tool(tool_name: str) -> None:
    """Raise FileNotFoundError if the given Ivy CLI tool is not on PATH."""
    if shutil.which(tool_name) is None:
        raise FileNotFoundError(f"'{tool_name}' is not installed or not on PATH.\nInstall the Ivy toolchain to use this tool.")


class IvyCheckTool(Tool, ToolMarkerOptional):
    """
    Runs ivy_check on an Ivy source file to verify its formal properties.
    Returns structured diagnostics with file, line, severity, and message
    for each issue found.

    When the Ivy language server is running, delegates to the ``ivy/verify``
    custom command which provides staging-directory resolution, automatic
    isolate detection, and test-scope redirection.  Falls back to the CLI
    when no LS is available.
    """

    def _apply_via_lsp(
        self,
        relative_path: str,
        isolate: str | None,
        ivy_ls: "IvyLanguageServer",
    ) -> dict[str, Any]:
        """Send ivy/verify via the language server and normalise the response."""
        project_root = self.get_project_root()
        uri = pathlib.Path(os.path.join(project_root, relative_path)).as_uri()
        params: dict[str, Any] = {"textDocument": {"uri": uri}}
        if isolate is not None:
            params["isolate"] = isolate

        resp = ivy_ls.send_custom_request("ivy/verify", params)

        output_lines: list[str] = resp.get("output", [])
        raw_output = "\n".join(output_lines)
        diagnostics = _parse_ivy_check_output(raw_output)

        return {
            "success": resp.get("success", False),
            "diagnostics": diagnostics,
            "diagnostic_count": resp.get("diagnosticCount", len(diagnostics)),
            "error_count": sum(1 for d in diagnostics if d["severity"] == "error"),
            "warning_count": sum(1 for d in diagnostics if d["severity"] == "warning"),
            "raw_output": raw_output.strip(),
            "duration_seconds": round(resp.get("duration", 0.0), 2),
            "isolate": resp.get("isolate"),
            "via": "lsp",
        }

    def _apply_via_cli(
        self,
        relative_path: str,
        isolate: str | None,
    ) -> dict[str, Any]:
        """Fall back to shelling out to ivy_check."""
        project_root = self.get_project_root()
        _require_ivy_tool("ivy_check")

        safe_path = shlex.quote(relative_path)
        if isolate is not None:
            command = f"ivy_check isolate={shlex.quote(isolate)} {safe_path}"
        else:
            command = f"ivy_check {safe_path}"

        start = time.monotonic()
        result = execute_shell_command(command, cwd=project_root, capture_stderr=True)
        duration = time.monotonic() - start

        raw_output = result.stdout + "\n" + (result.stderr or "")
        diagnostics = _parse_ivy_check_output(raw_output)

        payload: dict[str, Any] = {
            "success": result.return_code == 0,
            "diagnostics": diagnostics,
            "diagnostic_count": len(diagnostics),
            "error_count": sum(1 for d in diagnostics if d["severity"] == "error"),
            "warning_count": sum(1 for d in diagnostics if d["severity"] == "warning"),
            "raw_output": raw_output.strip(),
            "return_code": result.return_code,
            "duration_seconds": round(duration, 2),
            "via": "cli",
        }

        if result.return_code != 0 and len(diagnostics) == 0:
            payload["parse_warning"] = (
                "ivy_check exited with non-zero status but no structured diagnostics could be parsed. Check raw_output for details."
            )
        return payload

    def apply(
        self,
        relative_path: str,
        isolate: str | None = None,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Runs ivy_check on the specified Ivy file to perform formal verification.
        This checks isolate assumptions, invariants, and safety properties.

        When the Ivy language server is running, uses the ``ivy/verify`` custom
        command (benefits: staging-dir resolution, auto-isolate detection,
        test-scope redirection).  Falls back to the CLI when no LS is active.

        IMPORTANT: The file must be an .ivy file within the active project.

        :param relative_path: relative path to the .ivy file to check
        :param isolate: optional isolate name to check in isolation
            (e.g. "protocol_model" to check only that isolate)
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object with success, diagnostics array, raw_output,
            and duration_seconds
        """
        project_root = self.get_project_root()
        _validate_ivy_path(project_root, relative_path)

        ivy_ls = _get_ivy_language_server(self.agent)
        if ivy_ls is not None:
            try:
                payload = self._apply_via_lsp(relative_path, isolate, ivy_ls)
                return self._limit_length(json.dumps(payload), max_answer_chars)
            except Exception:
                log.debug("ivy/verify via LSP failed, falling back to CLI", exc_info=True)

        payload = self._apply_via_cli(relative_path, isolate)
        return self._limit_length(json.dumps(payload), max_answer_chars)


class IvyCompileTool(Tool, ToolMarkerOptional):
    """
    Compiles an Ivy source file to a test executable using ivyc.

    When the Ivy language server is running, delegates to ``ivy/compile``
    which handles staging-directory resolution and test-scope redirection.
    Falls back to the CLI when no LS is available.
    """

    def apply(
        self,
        relative_path: str,
        target: str = "test",
        isolate: str | None = None,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Compiles the specified Ivy file using ivyc (the Ivy compiler).
        By default, compiles to a test target that can be executed to run
        the protocol model.

        IMPORTANT: The file must be an .ivy file within the active project.
        Compilation may take significant time for large models.

        :param relative_path: relative path to the .ivy file to compile
        :param target: compilation target, typically "test" for generating
            test executables. Other targets may be available depending on the
            Ivy installation.
        :param isolate: optional isolate name to compile in isolation
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object containing the ivyc output with stdout,
            stderr, and return code
        """
        project_root = self.get_project_root()
        _validate_ivy_path(project_root, relative_path)

        ivy_ls = _get_ivy_language_server(self.agent)
        if ivy_ls is not None:
            try:
                uri = pathlib.Path(os.path.join(project_root, relative_path)).as_uri()
                params: dict[str, Any] = {"textDocument": {"uri": uri}, "target": target}
                resp = ivy_ls.send_custom_request("ivy/compile", params)
                resp["via"] = "lsp"
                return self._limit_length(json.dumps(resp), max_answer_chars)
            except Exception:
                log.debug("ivy/compile via LSP failed, falling back to CLI", exc_info=True)

        _require_ivy_tool("ivyc")
        safe_path = shlex.quote(relative_path)
        if isolate is not None:
            command = f"ivyc target={shlex.quote(target)} isolate={shlex.quote(isolate)} {safe_path}"
        else:
            command = f"ivyc target={shlex.quote(target)} {safe_path}"

        result = execute_shell_command(command, cwd=project_root, capture_stderr=True)
        return self._limit_length(result.model_dump_json(), max_answer_chars)


class IvyModelInfoTool(Tool, ToolMarkerOptional):
    """
    Displays the structure of an Ivy model using ivy_show.

    When the Ivy language server is running, delegates to ``ivy/showModel``
    which provides smart isolate detection (cursor-based and multi-isolate
    disambiguation) and test-scope redirection.
    """

    def apply(
        self,
        relative_path: str,
        isolate: str | None = None,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Runs ivy_show on the specified Ivy file to display its model structure,
        including types, relations, actions, invariants, and isolates.

        IMPORTANT: The file must be an .ivy file within the active project.

        :param relative_path: relative path to the .ivy file to inspect
        :param isolate: optional isolate name to display information about
            a specific isolate only
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object containing the ivy_show output with stdout,
            stderr, and return code
        """
        project_root = self.get_project_root()
        _validate_ivy_path(project_root, relative_path)

        ivy_ls = _get_ivy_language_server(self.agent)
        if ivy_ls is not None:
            try:
                uri = pathlib.Path(os.path.join(project_root, relative_path)).as_uri()
                params: dict[str, Any] = {"textDocument": {"uri": uri}}
                if isolate is not None:
                    params["isolate"] = isolate
                resp = ivy_ls.send_custom_request("ivy/showModel", params)
                resp["via"] = "lsp"
                return self._limit_length(json.dumps(resp), max_answer_chars)
            except Exception:
                log.debug("ivy/showModel via LSP failed, falling back to CLI", exc_info=True)

        _require_ivy_tool("ivy_show")
        safe_path = shlex.quote(relative_path)
        if isolate is not None:
            command = f"ivy_show isolate={shlex.quote(isolate)} {safe_path}"
        else:
            command = f"ivy_show {safe_path}"

        result = execute_shell_command(command, cwd=project_root, capture_stderr=True)
        return self._limit_length(result.model_dump_json(), max_answer_chars)


class IvyDiagnosticsTool(Tool, ToolMarkerOptional):
    """
    Returns cached LSP diagnostics for an Ivy file without running ivy_check.
    Diagnostics are captured from the Ivy language server's publishDiagnostics
    notifications (structural issues, parse errors, requirement analysis).

    Also includes ``featureStatus`` from the server when available, showing
    per-feature availability (code lens, diagnostics, navigation, etc.).
    """

    def apply(
        self,
        relative_path: str | None = None,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Returns cached diagnostics from the Ivy language server for a specific
        file or all files. These include structural checks (missing #lang,
        unmatched braces, unresolved includes) and parse errors, without running
        the expensive ivy_check subprocess.

        :param relative_path: optional relative path to an .ivy file. If omitted,
            returns diagnostics for all files that have been opened.
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object with diagnostics per file and optional featureStatus
        """
        ivy_ls = _get_ivy_language_server(self.agent)
        server_active = ivy_ls is not None

        # Fetch feature status from server when available
        feature_status: dict[str, Any] | None = None
        if ivy_ls is not None:
            try:
                feature_status = ivy_ls.send_custom_request("ivy/featureStatus")
            except Exception:
                log.debug("ivy/featureStatus request failed", exc_info=True)

        if relative_path is not None:
            project_root = self.get_project_root()
            abs_path = os.path.join(project_root, relative_path)
            uri = pathlib.Path(abs_path).as_uri()
            diags = ivy_ls.get_stored_diagnostics(uri) if ivy_ls else []
            payload: dict[str, Any] = {
                "file": relative_path,
                "diagnostics": diags,
                "diagnostic_count": len(diags),
                "server_active": server_active,
            }
            if feature_status is not None:
                payload["featureStatus"] = feature_status
            result = json.dumps(payload)
        else:
            all_diags = ivy_ls.get_all_stored_diagnostics() if ivy_ls else {}
            summary: dict[str, Any] = {}
            for uri, diags in all_diags.items():
                filepath = uri.replace("file://", "")
                summary[filepath] = {
                    "diagnostics": diags,
                    "diagnostic_count": len(diags),
                }
            payload = {
                "files": summary,
                "total_files": len(summary),
                "server_active": server_active,
            }
            if feature_status is not None:
                payload["featureStatus"] = feature_status
            result = json.dumps(payload)

        return self._limit_length(result, max_answer_chars)


def _check_structural_issues(source: str, filepath: str) -> list[dict[str, Any]]:
    """Check for structural problems in Ivy source without full parsing.

    Checks:
    1. Missing #lang ivy1.7 header
    2. Unmatched braces (with depth tracking)
    3. Unresolved includes (basic file existence check)
    """
    diags: list[dict[str, Any]] = []
    lines = source.split("\n")

    # 1. Missing #lang header
    stripped = source.lstrip()
    if not stripped.startswith("#lang"):
        diags.append(
            {
                "line": 1,
                "severity": "warning",
                "message": "Missing '#lang ivy1.7' header",
                "source": "ivy-lint",
            }
        )

    # 2. Unmatched braces
    depth = 0
    for i, line_text in enumerate(lines):
        if line_text.strip().startswith("#lang"):
            code = line_text
        else:
            code = line_text.split("#")[0]
        for ch in code:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth < 0:
                diags.append(
                    {
                        "line": i + 1,
                        "severity": "error",
                        "message": "Unmatched closing brace",
                        "source": "ivy-lint",
                    }
                )
                depth = 0
    if depth > 0:
        diags.append(
            {
                "line": len(lines),
                "severity": "error",
                "message": f"Unmatched opening brace ({depth} unclosed)",
                "source": "ivy-lint",
            }
        )

    # 3. Unresolved includes (check if file exists in same directory)
    parent_dir = os.path.dirname(filepath)
    for match in re.finditer(r"^include\s+(\w+)", source, re.MULTILINE):
        inc_name = match.group(1)
        candidate = os.path.join(parent_dir, inc_name + ".ivy")
        if not os.path.isfile(candidate):
            line_no = source[: match.start()].count("\n") + 1
            diags.append(
                {
                    "line": line_no,
                    "severity": "warning",
                    "message": f"Unresolved include: {inc_name} (not found in same directory)",
                    "source": "ivy-lint",
                }
            )

    return diags


class IvyLintTool(Tool, ToolMarkerOptional):
    """
    Performs fast structural linting on an Ivy file without running ivy_check.
    Checks for missing #lang header, unmatched braces, and unresolved includes.
    Much faster than ivy_check (milliseconds vs seconds/minutes).
    """

    def apply(
        self,
        relative_path: str,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Performs fast structural lint checks on the specified Ivy file.
        This catches common errors (missing #lang header, unmatched braces,
        unresolved includes) in milliseconds, without spawning the expensive
        ivy_check subprocess.

        Use this for quick validation before running full ivy_check verification.

        IMPORTANT: The file must be an .ivy file within the active project.

        :param relative_path: relative path to the .ivy file to lint
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object with diagnostics array (line, severity, message)
        """
        project_root = self.get_project_root()
        abs_path = _validate_ivy_path(project_root, relative_path)

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            source = f.read()

        diagnostics = _check_structural_issues(source, abs_path)

        result = json.dumps(
            {
                "file": relative_path,
                "diagnostics": diagnostics,
                "diagnostic_count": len(diagnostics),
                "error_count": sum(1 for d in diagnostics if d["severity"] == "error"),
                "warning_count": sum(1 for d in diagnostics if d["severity"] == "warning"),
            }
        )
        return self._limit_length(result, max_answer_chars)


class IvyGotoDefinitionTool(Tool, ToolMarkerSymbolicRead, ToolMarkerOptional):
    """
    Goes to the definition of a symbol at a given position in an Ivy file.
    Delegates to the Ivy language server's textDocument/definition request
    to follow symbols across include boundaries.
    """

    def apply(
        self,
        relative_path: str,
        line: int,
        column: int = 0,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Resolves the definition location of a symbol at the given position.
        This follows symbols across include boundaries, enabling navigation
        of Ivy models that span multiple files.

        IMPORTANT: Requires the Ivy language server to be running.

        :param relative_path: relative path to the .ivy file containing the symbol
        :param line: the 0-based line number of the symbol
        :param column: the 0-based column number of the symbol (default: 0)
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object with definition locations (file, line, column)
        """
        if not self.agent.is_using_language_server():
            raise RuntimeError("Language server not available. IvyGotoDefinitionTool requires an active Ivy language server.")

        ls_manager = self.agent.get_language_server_manager_or_raise()
        language_server = ls_manager.get_language_server(relative_path)

        locations = language_server.request_definition(relative_path, line, column)

        definitions = []
        for loc in locations:
            loc_range = loc.get("range", {})
            start = loc_range.get("start", {})
            definition = {
                "file": loc.get("relativePath", loc.get("absolutePath", "")),
                "line": start.get("line", 0),
                "column": start.get("character", 0),
                "uri": loc.get("uri", ""),
            }
            # Include a few lines of context from the target file
            target_path = loc.get("relativePath")
            if target_path:
                try:
                    project_root = self.get_project_root()
                    target_abs = os.path.join(project_root, target_path)
                    if os.path.isfile(target_abs):
                        with open(target_abs, encoding="utf-8", errors="replace") as f:
                            target_lines = f.readlines()
                        target_line = start.get("line", 0)
                        start_ctx = max(0, target_line - 1)
                        end_ctx = min(len(target_lines), target_line + 4)
                        definition["context"] = "".join(target_lines[start_ctx:end_ctx]).rstrip()
                except OSError as e:
                    definition["context_error"] = str(e)
            definitions.append(definition)

        result = json.dumps(
            {
                "source": f"{relative_path}:{line}:{column}",
                "definitions": definitions,
                "definition_count": len(definitions),
            }
        )
        return self._limit_length(result, max_answer_chars)


class IvyIncludeGraphTool(Tool, ToolMarkerOptional):
    """
    Exposes the include dependency graph for Ivy files.

    When the Ivy language server is running, uses ``ivy/includeGraph`` which
    returns accurate indexed data (node URIs with symbol counts and edges).
    Falls back to filesystem scanning when no LS is available.
    """

    def _apply_via_lsp(self, ivy_ls: "IvyLanguageServer") -> dict[str, Any]:
        """Fetch the include graph from the language server and normalise."""
        resp = ivy_ls.send_custom_request("ivy/includeGraph")
        nodes: list[dict[str, Any]] = resp.get("nodes", [])
        edges: list[dict[str, Any]] = resp.get("edges", [])

        # Convert LSP response to the same format as the filesystem walk
        file_summaries: dict[str, Any] = {}
        for node in nodes:
            uri = node.get("uri", "")
            filepath = uri.replace("file://", "")
            includes = [
                e["to"].replace("file://", "")
                for e in edges
                if e.get("from") == uri
            ]
            file_summaries[filepath] = {
                "includes": includes,
                "include_count": len(includes),
                "symbol_count": node.get("symbolCount", 0),
            }

        return {
            "files": file_summaries,
            "total_files": len(file_summaries),
            "total_include_edges": len(edges),
            "via": "lsp",
        }

    def _apply_via_filesystem(self, project_root: str, relative_path: str | None) -> dict[str, Any]:
        """Fall back to scanning .ivy files for include directives."""
        graph: dict[str, list[str]] = {}
        file_by_basename: dict[str, str] = {}

        _SKIP_DIRS = {
            ".git", ".venv", "venv", "node_modules",
            "__pycache__", "build", "dist", "submodules",
        }
        MAX_IVY_FILES = 5000
        skipped_files: list[dict[str, str]] = []

        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                if not fname.endswith(".ivy"):
                    continue
                if len(graph) >= MAX_IVY_FILES:
                    break
                rel_path = os.path.relpath(os.path.join(dirpath, fname), project_root)
                basename = fname[:-4]
                file_by_basename[basename] = rel_path
                abs_file = os.path.join(dirpath, fname)
                try:
                    with open(abs_file, encoding="utf-8", errors="replace") as f:
                        source = f.read()
                except OSError as e:
                    skipped_files.append({"file": rel_path, "error": str(e)})
                    continue
                includes = re.findall(r"^include\s+(\w+)", source, re.MULTILINE)
                graph[rel_path] = includes
            if len(graph) >= MAX_IVY_FILES:
                break

        if relative_path is not None:
            includes = graph.get(relative_path, [])
            resolved_includes = [
                {"module": inc, "resolved_path": file_by_basename.get(inc)}
                for inc in includes
            ]
            target_basename = os.path.basename(relative_path)
            if target_basename.endswith(".ivy"):
                target_basename = target_basename[:-4]
            included_by = [
                fp for fp, fi in graph.items() if target_basename in fi
            ]
            transitive: set[str] = set()
            stack = list(includes)
            while stack:
                mod = stack.pop()
                if mod in transitive:
                    continue
                transitive.add(mod)
                mod_path = file_by_basename.get(mod)
                if mod_path and mod_path in graph:
                    stack.extend(graph[mod_path])
            return {
                "file": relative_path,
                "includes": resolved_includes,
                "included_by": included_by,
                "transitive_includes": sorted(transitive),
                "transitive_include_count": len(transitive),
                "skipped_files": skipped_files,
                "via": "filesystem",
            }
        else:
            file_summaries: dict[str, Any] = {}
            for file_path, includes in graph.items():
                file_summaries[file_path] = {
                    "includes": includes,
                    "include_count": len(includes),
                }
            return {
                "files": file_summaries,
                "total_files": len(file_summaries),
                "total_include_edges": sum(len(inc) for inc in graph.values()),
                "skipped_files": skipped_files,
                "via": "filesystem",
            }

    def apply(
        self,
        relative_path: str | None = None,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Returns the include dependency graph for Ivy files in the project.

        When the Ivy language server is running, uses ``ivy/includeGraph``
        (accurate indexed data).  Falls back to filesystem scanning.

        :param relative_path: optional relative path to a specific .ivy file.
            If provided, returns includes/included_by for that file only.
            If omitted, returns the full project include graph.
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object describing the include dependency graph
        """
        project_root = self.get_project_root()

        # For full graph (no relative_path), try LSP first
        if relative_path is None:
            ivy_ls = _get_ivy_language_server(self.agent)
            if ivy_ls is not None:
                try:
                    payload = self._apply_via_lsp(ivy_ls)
                    return self._limit_length(json.dumps(payload), max_answer_chars)
                except Exception:
                    log.debug("ivy/includeGraph via LSP failed, falling back to filesystem", exc_info=True)

        payload = self._apply_via_filesystem(project_root, relative_path)
        return self._limit_length(json.dumps(payload), max_answer_chars)


class IvyServerStatusTool(Tool, ToolMarkerOptional):
    """
    Returns the current status of the Ivy language server, including mode
    (full/light), version, uptime, tool availability, and indexing state.

    Requires the Ivy language server to be running.
    """

    def apply(
        self,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Queries the Ivy language server for its current operational status.

        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object with mode, version, uptime, tools, indexing state
        """
        ivy_ls = _get_ivy_language_server(self.agent)
        if ivy_ls is None:
            return self._limit_length(
                json.dumps({"server_active": False, "error": "Ivy language server is not running"}),
                max_answer_chars,
            )

        try:
            status = ivy_ls.send_custom_request("ivy/serverStatus")
            status["server_active"] = True
            return self._limit_length(json.dumps(status), max_answer_chars)
        except Exception as e:
            return self._limit_length(
                json.dumps({"server_active": True, "error": f"Failed to query server status: {e}"}),
                max_answer_chars,
            )


class IvyTestScopeTool(Tool, ToolMarkerOptional):
    """
    Lists available test scopes and allows setting the active test scope
    for the Ivy language server.  Test scopes control which test file's
    include closure is used for diagnostics, code lenses, and navigation.

    Requires the Ivy language server to be running.
    """

    def apply(
        self,
        action: str = "list",
        test_file: str | None = None,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Manages Ivy test scopes via the language server.

        :param action: "list" to list all test scopes, "set" to set the active
            test scope. Defaults to "list".
        :param test_file: when action is "set", the test file path to activate.
            Pass None to clear the active test scope.
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object with test scope information
        """
        ivy_ls = _get_ivy_language_server(self.agent)
        if ivy_ls is None:
            return self._limit_length(
                json.dumps({"server_active": False, "error": "Ivy language server is not running"}),
                max_answer_chars,
            )

        try:
            if action == "set":
                params: dict[str, Any] = {}
                if test_file is not None:
                    params["testFile"] = test_file
                resp = ivy_ls.send_custom_request("ivy/setActiveTest", params)
            else:
                resp = ivy_ls.send_custom_request("ivy/listTests")
            resp["server_active"] = True
            return self._limit_length(json.dumps(resp), max_answer_chars)
        except Exception as e:
            return self._limit_length(
                json.dumps({"server_active": True, "error": f"Test scope operation failed: {e}"}),
                max_answer_chars,
            )
