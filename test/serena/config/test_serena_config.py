import json
import logging
import shutil
import tempfile
from pathlib import Path

import pytest

from serena.agent import SerenaAgent
from serena.config.serena_config import LanguageBackend, ProjectConfig, RegisteredProject, SerenaConfig
from serena.constants import PROJECT_TEMPLATE_FILE
from serena.project import Project
from solidlsp.ls_config import Language


class TestProjectConfigAutogenerate:
    """Test class for ProjectConfig autogeneration functionality."""

    def setup_method(self):
        """Set up test environment before each test method."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.project_path = Path(self.test_dir)

    def teardown_method(self):
        """Clean up test environment after each test method."""
        # Remove the temporary directory
        shutil.rmtree(self.test_dir)

    def test_autogenerate_empty_directory(self):
        """Test that autogenerate raises ValueError with helpful message for empty directory."""
        with pytest.raises(ValueError) as exc_info:
            ProjectConfig.autogenerate(self.project_path, save_to_disk=False)

        error_message = str(exc_info.value)
        assert "No source files found" in error_message

    def test_autogenerate_with_python_files(self):
        """Test successful autogeneration with Python source files."""
        # Create a Python file
        python_file = self.project_path / "main.py"
        python_file.write_text("def hello():\n    print('Hello, world!')\n")

        # Run autogenerate
        config = ProjectConfig.autogenerate(self.project_path, save_to_disk=False)

        # Verify the configuration
        assert config.project_name == self.project_path.name
        assert config.languages == [Language.PYTHON]

    def test_autogenerate_with_js_files(self):
        """Test successful autogeneration with JavaScript source files."""
        # Create files for multiple languages
        (self.project_path / "small.js").write_text("console.log('JS');")

        # Run autogenerate - should pick Python as dominant
        config = ProjectConfig.autogenerate(self.project_path, save_to_disk=False)

        assert config.languages == [Language.TYPESCRIPT]

    def test_autogenerate_with_multiple_languages(self):
        """Test autogeneration picks dominant language when multiple are present."""
        # Create files for multiple languages
        (self.project_path / "main.py").write_text("print('Python')")
        (self.project_path / "util.py").write_text("def util(): pass")
        (self.project_path / "small.js").write_text("console.log('JS');")

        # Run autogenerate - should pick Python as dominant
        config = ProjectConfig.autogenerate(self.project_path, save_to_disk=False)

        assert config.languages == [Language.PYTHON]

    def test_autogenerate_saves_to_disk(self):
        """Test that autogenerate can save the configuration to disk."""
        # Create a Go file
        go_file = self.project_path / "main.go"
        go_file.write_text("package main\n\nfunc main() {}\n")

        # Run autogenerate with save_to_disk=True
        config = ProjectConfig.autogenerate(self.project_path, save_to_disk=True)

        # Verify the configuration file was created
        config_path = self.project_path / ".serena" / "project.yml"
        assert config_path.exists()

        # Verify the content
        assert config.languages == [Language.GO]

    def test_autogenerate_nonexistent_path(self):
        """Test that autogenerate raises FileNotFoundError for non-existent path."""
        non_existent = self.project_path / "does_not_exist"

        with pytest.raises(FileNotFoundError) as exc_info:
            ProjectConfig.autogenerate(non_existent, save_to_disk=False)

        assert "Project root not found" in str(exc_info.value)

    def test_autogenerate_with_gitignored_files_only(self):
        """Test autogenerate behavior when only gitignored files exist."""
        # Create a .gitignore that ignores all Python files
        gitignore = self.project_path / ".gitignore"
        gitignore.write_text("*.py\n")

        # Create Python files that will be ignored
        (self.project_path / "ignored.py").write_text("print('ignored')")

        # Should still raise ValueError as no source files are detected
        with pytest.raises(ValueError) as exc_info:
            ProjectConfig.autogenerate(self.project_path, save_to_disk=False)

        assert "No source files found" in str(exc_info.value)

    def test_autogenerate_custom_project_name(self):
        """Test autogenerate with custom project name."""
        # Create a TypeScript file
        ts_file = self.project_path / "index.ts"
        ts_file.write_text("const greeting: string = 'Hello';\n")

        # Run autogenerate with custom name
        custom_name = "my-custom-project"
        config = ProjectConfig.autogenerate(self.project_path, project_name=custom_name, save_to_disk=False)

        assert config.project_name == custom_name
        assert config.languages == [Language.TYPESCRIPT]


class TestProjectConfig:
    def test_template_is_complete(self):
        _, is_complete = ProjectConfig._load_yaml(PROJECT_TEMPLATE_FILE)
        assert is_complete, "Project template YAML is incomplete; all fields must be present (with descriptions)."


class TestProjectConfigLanguageBackend:
    """Tests for the per-project language_backend field."""

    def test_language_backend_defaults_to_none(self):
        config = ProjectConfig(
            project_name="test",
            languages=[Language.PYTHON],
        )
        assert config.language_backend is None

    def test_language_backend_can_be_set(self):
        config = ProjectConfig(
            project_name="test",
            languages=[Language.PYTHON],
            language_backend=LanguageBackend.JETBRAINS,
        )
        assert config.language_backend == LanguageBackend.JETBRAINS

    def test_language_backend_roundtrips_through_yaml(self):
        config = ProjectConfig(
            project_name="test",
            languages=[Language.PYTHON],
            language_backend=LanguageBackend.JETBRAINS,
        )
        d = config._to_yaml_dict()
        assert d["language_backend"] == "JetBrains"

    def test_language_backend_none_roundtrips_through_yaml(self):
        config = ProjectConfig(
            project_name="test",
            languages=[Language.PYTHON],
        )
        d = config._to_yaml_dict()
        assert d["language_backend"] is None

    def test_language_backend_parsed_from_dict(self):
        """Test that _from_dict parses language_backend correctly."""
        template_path = PROJECT_TEMPLATE_FILE
        data, _ = ProjectConfig._load_yaml(template_path)
        data["project_name"] = "test"
        data["languages"] = ["python"]
        data["language_backend"] = "JetBrains"
        config = ProjectConfig._from_dict(data)
        assert config.language_backend == LanguageBackend.JETBRAINS

    def test_language_backend_none_when_missing_from_dict(self):
        """Test that _from_dict handles missing language_backend gracefully."""
        template_path = PROJECT_TEMPLATE_FILE
        data, _ = ProjectConfig._load_yaml(template_path)
        data["project_name"] = "test"
        data["languages"] = ["python"]
        data.pop("language_backend", None)
        config = ProjectConfig._from_dict(data)
        assert config.language_backend is None


def _make_config_with_project(
    project_name: str,
    language_backend: LanguageBackend | None = None,
    global_backend: LanguageBackend = LanguageBackend.LSP,
) -> tuple[SerenaConfig, str]:
    """Create a SerenaConfig with a single registered project and return (config, project_name)."""
    project = Project(
        project_root=str(Path(__file__).parent.parent / "resources" / "repos" / "python" / "test_repo"),
        project_config=ProjectConfig(
            project_name=project_name,
            languages=[Language.PYTHON],
            language_backend=language_backend,
        ),
    )
    config = SerenaConfig(
        gui_log_window=False,
        web_dashboard=False,
        log_level=logging.ERROR,
        language_backend=global_backend,
    )
    config.projects = [RegisteredProject.from_project_instance(project)]
    return config, project_name


class TestEffectiveLanguageBackend:
    """Tests for per-project language_backend override logic in SerenaAgent."""

    def test_default_backend_is_global(self):
        """When no project override, effective backend matches global config."""
        config, name = _make_config_with_project("test_proj", language_backend=None, global_backend=LanguageBackend.LSP)
        agent = SerenaAgent(project=name, serena_config=config)
        try:
            assert agent.get_language_backend() == LanguageBackend.LSP
            assert agent.is_using_language_server() is True
        finally:
            agent.shutdown(timeout=5)

    def test_project_overrides_global_backend(self):
        """When startup project has language_backend set, it overrides the global."""
        config, name = _make_config_with_project(
            "test_jetbrains", language_backend=LanguageBackend.JETBRAINS, global_backend=LanguageBackend.LSP
        )
        agent = SerenaAgent(project=name, serena_config=config)
        try:
            assert agent.get_language_backend() == LanguageBackend.JETBRAINS
            assert agent.is_using_language_server() is False
        finally:
            agent.shutdown(timeout=5)

    def test_no_project_uses_global_backend(self):
        """When no startup project is provided, effective backend is the global one."""
        config = SerenaConfig(
            gui_log_window=False,
            web_dashboard=False,
            log_level=logging.ERROR,
            language_backend=LanguageBackend.LSP,
        )
        agent = SerenaAgent(project=None, serena_config=config)
        try:
            assert agent.get_language_backend() == LanguageBackend.LSP
        finally:
            agent.shutdown(timeout=5)

    def test_activate_project_rejects_backend_mismatch(self):
        """Post-init activation of a project with mismatched backend raises ValueError."""
        # Start with LSP backend
        config, name = _make_config_with_project("lsp_proj", language_backend=None, global_backend=LanguageBackend.LSP)

        # Add a second project that requires JetBrains
        jb_project = Project(
            project_root=str(Path(__file__).parent.parent / "resources" / "repos" / "python" / "test_repo"),
            project_config=ProjectConfig(
                project_name="jb_proj",
                languages=[Language.PYTHON],
                language_backend=LanguageBackend.JETBRAINS,
            ),
        )
        config.projects.append(RegisteredProject.from_project_instance(jb_project))

        agent = SerenaAgent(project=name, serena_config=config)
        try:
            with pytest.raises(ValueError, match="Cannot activate project"):
                agent.activate_project_from_path_or_name("jb_proj")
        finally:
            agent.shutdown(timeout=5)

    def test_activate_project_allows_matching_backend(self):
        """Post-init activation of a project with matching backend succeeds."""
        config, name = _make_config_with_project("lsp_proj", language_backend=None, global_backend=LanguageBackend.LSP)

        # Add a second project that also uses LSP
        lsp_project2 = Project(
            project_root=str(Path(__file__).parent.parent / "resources" / "repos" / "python" / "test_repo"),
            project_config=ProjectConfig(
                project_name="lsp_proj2",
                languages=[Language.PYTHON],
                language_backend=LanguageBackend.LSP,
            ),
        )
        config.projects.append(RegisteredProject.from_project_instance(lsp_project2))

        agent = SerenaAgent(project=name, serena_config=config)
        try:
            # Should not raise
            agent.activate_project_from_path_or_name("lsp_proj2")
        finally:
            agent.shutdown(timeout=5)

    def test_activate_project_allows_none_backend(self):
        """Post-init activation of a project with no backend override succeeds."""
        config, name = _make_config_with_project("lsp_proj", language_backend=None, global_backend=LanguageBackend.LSP)

        # Add a second project with no backend override
        proj2 = Project(
            project_root=str(Path(__file__).parent.parent / "resources" / "repos" / "python" / "test_repo"),
            project_config=ProjectConfig(
                project_name="proj2",
                languages=[Language.PYTHON],
                language_backend=None,
            ),
        )
        config.projects.append(RegisteredProject.from_project_instance(proj2))

        agent = SerenaAgent(project=name, serena_config=config)
        try:
            # Should not raise — None means "inherit session backend"
            agent.activate_project_from_path_or_name("proj2")
        finally:
            agent.shutdown(timeout=5)


class TestGenerateIvyWorkspace:
    """Tests for ProjectConfig._generate_ivy_workspace auto-generation."""

    def test_creates_marker_when_missing(self, tmp_path):
        """Marker file is created with v3 schema when absent."""
        ProjectConfig._generate_ivy_workspace(tmp_path)

        marker = tmp_path / ".ivyworkspace"
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert data["version"] == 3
        assert data["project_type"] == "standalone"
        assert data["workspace_layers"][0]["include_paths"] == ["."]

    def test_does_not_overwrite_existing_marker(self, tmp_path):
        """Existing marker is preserved — no silent overwrite."""
        marker = tmp_path / ".ivyworkspace"
        original_content = '{"version": 2, "custom": true}'
        marker.write_text(original_content)

        ProjectConfig._generate_ivy_workspace(tmp_path)

        assert marker.read_text() == original_content

    def test_custom_include_paths(self, tmp_path):
        """Custom include_paths are written to the workspace_layers."""
        ProjectConfig._generate_ivy_workspace(tmp_path, include_paths=["src", "models"])

        data = json.loads((tmp_path / ".ivyworkspace").read_text())
        assert data["workspace_layers"][0]["include_paths"] == ["src", "models"]

    def test_custom_exclude_paths(self, tmp_path):
        """Custom exclude_paths are written at the top level."""
        ProjectConfig._generate_ivy_workspace(tmp_path, exclude_paths=["vendor", "test"])

        data = json.loads((tmp_path / ".ivyworkspace").read_text())
        assert data["exclude_paths"] == ["vendor", "test"]

    def test_no_exclude_paths_when_empty(self, tmp_path):
        """exclude_paths key is omitted when the list is empty."""
        ProjectConfig._generate_ivy_workspace(tmp_path, exclude_paths=[])

        data = json.loads((tmp_path / ".ivyworkspace").read_text())
        assert "exclude_paths" not in data

    def test_autogenerate_creates_marker_for_ivy_project(self, tmp_path):
        """autogenerate() triggers .ivyworkspace creation when Ivy is in languages."""
        # Create an .ivy file so language detection finds Ivy
        (tmp_path / "model.ivy").write_text("#lang ivy1.8\ntype packet")

        config = ProjectConfig.autogenerate(tmp_path, languages=[Language.IVY], save_to_disk=False)

        marker = tmp_path / ".ivyworkspace"
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert data["version"] == 3

    def test_autogenerate_skips_marker_for_non_ivy_project(self, tmp_path):
        """autogenerate() does NOT create .ivyworkspace for non-Ivy projects."""
        (tmp_path / "main.py").write_text("print('hello')")

        ProjectConfig.autogenerate(tmp_path, save_to_disk=False)

        assert not (tmp_path / ".ivyworkspace").exists()

    def test_load_creates_marker_for_ivy_project(self, tmp_path):
        """load() triggers .ivyworkspace creation when Ivy is configured."""
        # Set up a minimal project.yml with ivy
        serena_dir = tmp_path / ".serena"
        serena_dir.mkdir()
        # First autogenerate a valid project.yml, then modify it
        (tmp_path / "model.ivy").write_text("#lang ivy1.8\ntype packet")
        config = ProjectConfig.autogenerate(tmp_path, languages=[Language.IVY], save_to_disk=True)
        # Remove the auto-generated marker to test load() path
        (tmp_path / ".ivyworkspace").unlink()
        assert not (tmp_path / ".ivyworkspace").exists()

        # Now load — should re-create the marker
        ProjectConfig.load(tmp_path)

        assert (tmp_path / ".ivyworkspace").exists()
