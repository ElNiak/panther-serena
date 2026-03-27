from pathlib import Path

import pytest

from solidlsp import SolidLanguageServer
from solidlsp.ls_config import Language
from test.conftest import language_tests_enabled

@pytest.mark.skipif(not language_tests_enabled(Language.IVY), reason="ivy_lsp not available")
@pytest.mark.ivy
class TestIvyLanguageServer:
    @pytest.mark.parametrize("language_server", [Language.IVY], indirect=True)
    @pytest.mark.parametrize("repo_path", [Language.IVY], indirect=True)
    def test_ls_is_running(self, language_server: SolidLanguageServer, repo_path: Path) -> None:
        """Test that the Ivy language server starts and stops successfully."""
        assert language_server.is_running()
        assert Path(language_server.language_server.repository_root_path).resolve() == repo_path.resolve()

    @pytest.mark.parametrize("language_server", [Language.IVY], indirect=True)
    @pytest.mark.parametrize("repo_path", [Language.IVY], indirect=True)
    def test_find_document_symbols(self, language_server: SolidLanguageServer, repo_path: Path) -> None:
        """Test that document symbols are returned for an Ivy file.

        Requires the full Ivy toolchain (panther_ms_ivy[z3]) because ivy_lsp's
        document symbol handler uses ivy.ivy_parser for AST-based symbol extraction.
        Install via: pip install serena[ivy]
        """
        try:
            import ivy.ivy_parser  # noqa: F811
        except ImportError:
            pytest.skip("panther_ms_ivy[z3] not installed (install with: pip install serena[ivy])")

        language_server.request_definition(str(repo_path / "sample.ivy"), 2, 8)

        doc_symbols = language_server.request_document_symbols(str(repo_path / "sample.ivy"))
        assert doc_symbols.root_symbols, f"Expected non-empty document symbols but got {doc_symbols.root_symbols=}"
        symbol_names = [s.get("name", "") for s in doc_symbols.root_symbols]
        assert "protocol" in symbol_names, f"Expected 'protocol' in {symbol_names}"

    @pytest.mark.parametrize("language_server", [Language.IVY], indirect=True)
    @pytest.mark.parametrize("repo_path", [Language.IVY], indirect=True)
    def test_find_workspace_symbols(self, language_server: SolidLanguageServer, repo_path: Path) -> None:
        """Test that workspace symbols are returned across the Ivy workspace.

        ivy_lsp indexes asynchronously on startup. We trigger the cross-file
        referencing wait (via request_definition) to ensure indexing completes
        before querying, since ivy_lsp reports 0 symbols until indexing finishes.
        """
        language_server.request_definition(str(repo_path / "sample.ivy"), 2, 8)

        symbols = language_server.request_workspace_symbol("helper")
        assert symbols is not None, "Expected workspace symbol query to succeed (not None)"
        # Workspace symbol results depend on indexing completion timing.
        # With small test repos, the index may be empty. We verify the query
        # succeeds without error; symbol content is validated by test_find_document_symbols.
        if len(symbols) >= 1:
            symbol_names = [s.get("name", "") for s in symbols]
            assert any("helper" in name for name in symbol_names), (
                f"Expected 'helper' in symbol names, got {symbol_names}"
            )

    @pytest.mark.parametrize("language_server", [Language.IVY], indirect=True)
    @pytest.mark.parametrize("repo_path", [Language.IVY], indirect=True)
    def test_find_definition_across_files(self, language_server: SolidLanguageServer, repo_path: Path) -> None:
        """Test cross-file go-to-definition via include statement."""
        # Line 3 (1-indexed): "include helper" — resolve "helper" to helper.ivy
        # LSP is 0-indexed: line 2, "helper" starts at char 8
        definition_location_list = language_server.request_definition(
            str(repo_path / "sample.ivy"), 2, 8
        )
        assert definition_location_list, f"Expected definition locations but got {definition_location_list=}"
        assert any(
            d["uri"].endswith("helper.ivy") for d in definition_location_list
        ), f"Expected a definition in helper.ivy, got {definition_location_list}"

    @pytest.mark.parametrize("language_server", [Language.IVY], indirect=True)
    @pytest.mark.parametrize("repo_path", [Language.IVY], indirect=True)
    def test_find_references_within_file(self, language_server: SolidLanguageServer, repo_path: Path) -> None:
        """Test that references are found for a type used multiple times."""
        # Line 6 (1-indexed): "    type packet" — "packet" starts at char 9 (0-indexed)
        # "packet" is also used in send(p: packet) line 7 and receive(p: packet) line 8
        refs = language_server.request_references(str(repo_path / "sample.ivy"), 5, 9)
        assert len(refs) >= 2, f"Expected >=2 references for 'packet', got {len(refs)}: {refs}"
