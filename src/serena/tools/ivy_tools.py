"""
Ivy-specific tools for formal verification operations.

These tools provide direct access to the Ivy toolchain (ivy_check, ivyc,
ivy_show) through the Serena tool framework, enabling LLM agents to perform
formal verification tasks on Ivy models.
"""

import os
import os.path

from serena.tools import Tool, ToolMarkerOptional
from serena.util.shell import execute_shell_command


class IvyCheckTool(Tool, ToolMarkerOptional):
    """
    Runs ivy_check on an Ivy source file to verify its formal properties.
    """

    def apply(
        self,
        relative_path: str,
        isolate: str | None = None,
        max_answer_chars: int = -1,
    ) -> str:
        """
        Runs ivy_check on the specified Ivy file to perform formal verification.
        This checks isolate assumptions, invariants, and safety properties.

        IMPORTANT: The file must be an .ivy file within the active project.

        :param relative_path: relative path to the .ivy file to check
        :param isolate: optional isolate name to check in isolation
            (e.g. "protocol_model" to check only that isolate)
        :param max_answer_chars: if the output is longer than this number of
            characters, no content will be returned. -1 means using the
            default value.
        :return: a JSON object containing the ivy_check output with stdout,
            stderr, and return code
        """
        project_root = self.get_project_root()
        abs_path = os.path.join(project_root, relative_path)

        if not os.path.isfile(abs_path):
            raise FileNotFoundError(
                f"Ivy file not found: {relative_path}"
            )
        if not relative_path.endswith(".ivy"):
            raise ValueError(
                f"Expected an .ivy file, got: {relative_path}"
            )

        command = f"ivy_check {relative_path}"
        if isolate is not None:
            command = f"ivy_check isolate={isolate} {relative_path}"

        result = execute_shell_command(
            command, cwd=project_root, capture_stderr=True
        )
        return self._limit_length(result.json(), max_answer_chars)


class IvyCompileTool(Tool, ToolMarkerOptional):
    """
    Compiles an Ivy source file to a test executable using ivyc.
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
        abs_path = os.path.join(project_root, relative_path)

        if not os.path.isfile(abs_path):
            raise FileNotFoundError(
                f"Ivy file not found: {relative_path}"
            )
        if not relative_path.endswith(".ivy"):
            raise ValueError(
                f"Expected an .ivy file, got: {relative_path}"
            )

        command = f"ivyc target={target} {relative_path}"
        if isolate is not None:
            command = f"ivyc target={target} isolate={isolate} {relative_path}"

        result = execute_shell_command(
            command, cwd=project_root, capture_stderr=True
        )
        return self._limit_length(result.json(), max_answer_chars)


class IvyModelInfoTool(Tool, ToolMarkerOptional):
    """
    Displays the structure of an Ivy model using ivy_show.
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
        This is useful for understanding the high-level architecture of an
        Ivy formal model.

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
        abs_path = os.path.join(project_root, relative_path)

        if not os.path.isfile(abs_path):
            raise FileNotFoundError(
                f"Ivy file not found: {relative_path}"
            )
        if not relative_path.endswith(".ivy"):
            raise ValueError(
                f"Expected an .ivy file, got: {relative_path}"
            )

        command = f"ivy_show {relative_path}"
        if isolate is not None:
            command = f"ivy_show isolate={isolate} {relative_path}"

        result = execute_shell_command(
            command, cwd=project_root, capture_stderr=True
        )
        return self._limit_length(result.json(), max_answer_chars)
