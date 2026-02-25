"""
Provides Ivy specific instantiation of the LanguageServer class using ivy_lsp.
Contains various configurations and settings specific to the Ivy formal verification language.
"""

import logging
import os
import pathlib
import shutil

from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import LanguageServerConfig
from solidlsp.lsp_protocol_handler.lsp_types import InitializeParams
from solidlsp.lsp_protocol_handler.server import ProcessLaunchInfo
from solidlsp.settings import SolidLSPSettings

log = logging.getLogger(__name__)


class IvyLanguageServer(SolidLanguageServer):
    """
    Provides Ivy specific instantiation of the LanguageServer class using ivy_lsp.
    Ivy is a formal verification language used for protocol modeling and verification.
    """

    _diagnostics_store: dict[str, list[dict[str, object]]] = {}
    """Stores the latest publishDiagnostics notifications keyed by file URI."""

    @classmethod
    def get_stored_diagnostics(cls, uri: str) -> list[dict[str, object]]:
        """Return stored diagnostics for the given URI, or empty list."""
        return cls._diagnostics_store.get(uri, [])

    @classmethod
    def get_all_stored_diagnostics(cls) -> dict[str, list[dict[str, object]]]:
        """Return all stored diagnostics keyed by URI."""
        return dict(cls._diagnostics_store)

    def __init__(
        self,
        config: LanguageServerConfig,
        repository_root_path: str,
        solidlsp_settings: SolidLSPSettings,
    ):
        """
        Creates an IvyLanguageServer instance. This class is not meant to be
        instantiated directly. Use LanguageServer.create() instead.
        """
        ivy_lsp_cmd = self._find_ivy_lsp()
        include_paths = os.environ.get("IVY_LSP_INCLUDE_PATHS", "")
        exclude_paths = os.environ.get("IVY_LSP_EXCLUDE_PATHS", "submodules,test")
        super().__init__(
            config,
            repository_root_path,
            ProcessLaunchInfo(
                cmd=ivy_lsp_cmd,
                cwd=repository_root_path,
                env={
                    "IVY_LSP_INCLUDE_PATHS": include_paths,
                    "IVY_LSP_EXCLUDE_PATHS": exclude_paths,
                },
            ),
            "ivy",
            solidlsp_settings,
        )

    @staticmethod
    def _find_ivy_lsp() -> str:
        """
        Locate the ivy_lsp executable on the system PATH.

        Unlike most other language servers in Serena, ivy_lsp is not
        auto-downloaded. It must be installed separately (typically via
        pip install from the ivy-lsp package).

        :return: path to the ivy_lsp executable
        :raises FileNotFoundError: if ivy_lsp is not found on PATH
        """
        ivy_lsp_path = shutil.which("ivy_lsp")
        if ivy_lsp_path is None:
            raise FileNotFoundError(
                "ivy_lsp is not installed or is not in PATH.\n"
                "Install it via: pip install ivy-lsp\n"
                "Or from the panther_ivy package: pip install -e '.[lsp]'\n"
                "After installation, make sure 'ivy_lsp' is available on your PATH."
            )
        log.info(f"Found ivy_lsp at: {ivy_lsp_path}")
        return ivy_lsp_path

    @staticmethod
    def _get_initialize_params(repository_absolute_path: str) -> InitializeParams:
        """
        Returns the initialize params for the Ivy Language Server.
        """
        root_uri = pathlib.Path(repository_absolute_path).as_uri()
        initialize_params = {
            "locale": "en",
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "didSave": True,
                        "dynamicRegistration": True,
                    },
                    "completion": {
                        "dynamicRegistration": True,
                        "completionItem": {"snippetSupport": True},
                    },
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "hierarchicalDocumentSymbolSupport": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                    },
                    "hover": {
                        "dynamicRegistration": True,
                        "contentFormat": ["markdown", "plaintext"],
                    },
                },
                "workspace": {
                    "workspaceFolders": True,
                    "didChangeConfiguration": {"dynamicRegistration": True},
                    "symbol": {"dynamicRegistration": True},
                },
            },
            "processId": os.getpid(),
            "rootPath": repository_absolute_path,
            "rootUri": root_uri,
            "workspaceFolders": [
                {
                    "uri": root_uri,
                    "name": os.path.basename(repository_absolute_path),
                }
            ],
        }
        return initialize_params

    def _start_server(self) -> None:
        """
        Starts the Ivy Language Server and waits for it to be ready.
        """

        def register_capability_handler(params):
            return

        def window_log_message(msg):
            log.info(f"LSP: window/logMessage: {msg}")

        def do_nothing(params):
            return

        def store_diagnostics(params):
            """Capture publishDiagnostics notifications for later querying."""
            if isinstance(params, dict):
                uri = params.get("uri", "")
                diags = params.get("diagnostics", [])
                self.__class__._diagnostics_store[uri] = diags
                log.debug(f"Stored {len(diags)} diagnostics for {uri}")

        self.server.on_request("client/registerCapability", register_capability_handler)
        self.server.on_notification("window/logMessage", window_log_message)
        self.server.on_notification("$/progress", do_nothing)
        self.server.on_notification("textDocument/publishDiagnostics", store_diagnostics)

        log.info("Starting ivy_lsp server process")
        self.server.start()
        initialize_params = self._get_initialize_params(self.repository_root_path)

        log.info("Sending initialize request from LSP client to ivy_lsp server and awaiting response")
        init_response = self.server.send.initialize(initialize_params)
        log.debug(f"Received initialize response from ivy_lsp server: {init_response}")

        capabilities = init_response.get("capabilities", {})
        assert "textDocumentSync" in capabilities, "ivy_lsp did not report textDocumentSync capability"

        for cap_name in [
            "completionProvider",
            "definitionProvider",
            "referencesProvider",
            "documentSymbolProvider",
            "workspaceSymbolProvider",
            "hoverProvider",
        ]:
            if cap_name in capabilities:
                log.info(f"ivy_lsp supports {cap_name}")
            else:
                log.warning(f"ivy_lsp does not report {cap_name}")

        self.server.notify.initialized({})
        log.info("Ivy language server initialization complete")
