"""
Ivy language server integration tools for panther-serena.

These tools expose LSP-unique capabilities (cached diagnostics, go-to-definition,
server status, test scope management) that require an active Ivy language server.
For verification, compilation, linting, and analysis, use the ivy-tools MCP server.
"""

import json
import logging
import os
import os.path
import pathlib
from typing import TYPE_CHECKING, Any

from serena.tools import Tool, ToolMarkerOptional, ToolMarkerSymbolicRead

if TYPE_CHECKING:
    from solidlsp.language_servers.ivy_language_server import IvyLanguageServer

log = logging.getLogger(__name__)


def _get_ivy_language_server(agent: Any) -> "IvyLanguageServer | None":
    """Resolve the IvyLanguageServer instance from the agent, or None."""
    from solidlsp.language_servers.ivy_language_server import IvyLanguageServer

    if not agent.is_using_language_server():
        return None
    try:
        ls_manager = agent.get_language_server_manager_or_raise()
        ls = ls_manager.get_language_server("probe.ivy")
        return ls if isinstance(ls, IvyLanguageServer) else None
    except Exception:
        log.debug("Language server lookup failed", exc_info=True)
        return None


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
                log.warning("ivy/featureStatus request failed, falling back to CLI", exc_info=True)

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
