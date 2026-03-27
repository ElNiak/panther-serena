"""Unit tests for Ivy tool helper functions (no Ivy toolchain required)."""

import os
from unittest.mock import MagicMock

import pytest

from serena.tools.ivy_tools import _get_ivy_language_server

REPO_DIR = os.path.join(os.path.dirname(__file__), "../../resources/repos/ivy")


@pytest.mark.ivy
class TestGetIvyLanguageServer:
    """Tests for the _get_ivy_language_server helper."""

    def test_returns_none_when_ls_not_active(self) -> None:
        agent = MagicMock()
        agent.is_using_language_server.return_value = False
        assert _get_ivy_language_server(agent) is None

    def test_returns_none_when_ls_manager_raises(self) -> None:
        agent = MagicMock()
        agent.is_using_language_server.return_value = True
        agent.get_language_server_manager_or_raise.side_effect = RuntimeError("no manager")
        assert _get_ivy_language_server(agent) is None

    def test_returns_none_when_ls_is_wrong_type(self) -> None:
        agent = MagicMock()
        agent.is_using_language_server.return_value = True
        ls_manager = MagicMock()
        ls_manager.get_language_server.return_value = MagicMock()  # not IvyLanguageServer
        agent.get_language_server_manager_or_raise.return_value = ls_manager
        assert _get_ivy_language_server(agent) is None

    def test_returns_ivy_ls_when_available(self) -> None:
        from solidlsp.language_servers.ivy_language_server import IvyLanguageServer

        agent = MagicMock()
        agent.is_using_language_server.return_value = True
        mock_ls = MagicMock(spec=IvyLanguageServer)
        ls_manager = MagicMock()
        ls_manager.get_language_server.return_value = mock_ls
        agent.get_language_server_manager_or_raise.return_value = ls_manager
        assert _get_ivy_language_server(agent) is mock_ls
