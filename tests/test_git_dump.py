import os
import shutil
import tempfile
import pytest
from pathlib import Path
from src.git_dump.core import (
    RepoProcessor, get_language_from_path, BINARY_EXTENSIONS,
    DEFAULT_JUNK_DIRS, DEFAULT_IGNORE_PATTERNS, LLM_INSTRUCTIONS
)


class TestRepoProcessor:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "output.txt")

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    def create_file(self, path, content):
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_basic_concatenation(self):
        self.create_file("file1.txt", "content1")
        self.create_file("dir/file2.txt", "content2")

        processor = RepoProcessor(self.test_dir, self.output_file)
        count = processor.process()

        assert count == 2
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "### File: file1.txt" in content
            assert "content1" in content
            assert "### File: dir/file2.txt" in content
            assert "content2" in content

    def test_ignore_git(self):
        self.create_file(".git/config", "git config")
        self.create_file("file1.txt", "content1")

        processor = RepoProcessor(self.test_dir, self.output_file)
        count = processor.process()

        assert count == 1
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert ".git/config" not in content

    def test_gitignore_logic(self):
        self.create_file(".gitignore", "*.log\ntemp/")
        self.create_file("app.log", "log content")
        self.create_file("temp/data.txt", "temp data")
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 2  # main.py and .gitignore
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "main.py" in content
            assert "app.log" not in content
            assert "temp/data.txt" not in content

    def test_nested_gitignore_logic(self):
        # Create a nested directory with its own .gitignore
        self.create_file(".gitignore", "*.log")
        os.makedirs(os.path.join(self.test_dir, "subdir"))
        self.create_file("subdir/.gitignore", "secret.txt")
        self.create_file("app.log", "log content")
        self.create_file("subdir/secret.txt", "secret data")
        self.create_file("subdir/public.txt", "public data")
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        # Should include: .gitignore (root), main.py, subdir/.gitignore, subdir/public.txt
        # app.log and subdir/secret.txt should be excluded due to gitignore rules
        # Total: 4 files
        assert count == 4
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "main.py" in content
            assert "subdir/public.txt" in content
            assert "app.log" not in content  # Excluded by root .gitignore
            # The file 'subdir/secret.txt' should not appear in the output (not the string "secret.txt")
            assert "### File: subdir/secret.txt" not in content  # Excluded by nested .gitignore
            assert ".gitignore" in content  # Root .gitignore file itself is included
            assert "subdir/.gitignore" in content  # Nested .gitignore file itself is included

    def test_include_patterns(self):
        self.create_file("main.py", "print('hello')")
        self.create_file("README.md", "# project")
        self.create_file("data.json", "{}")

        processor = RepoProcessor(self.test_dir, self.output_file, include_patterns=["*.py", "*.md"], include_tree=False)
        count = processor.process()

        assert count == 2
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "main.py" in content
            assert "README.md" in content
            assert "data.json" not in content

    def test_custom_delimiters(self):
        self.create_file("file1.txt", "content1")
        processor = RepoProcessor(
            self.test_dir,
            self.output_file,
            start_delimiter="START {path}",
            end_delimiter="END {path}"
        )
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "START file1.txt" in content
            assert "END file1.txt" in content

    def test_markdown_delimiters_with_language(self):
        """Test that markdown delimiters include language hints."""
        self.create_file("main.py", "print('hello')")
        self.create_file("script.js", "console.log('hi')")
        self.create_file("README.md", "# Project")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "```python" in content
            assert "```javascript" in content
            assert "```markdown" in content

    def test_dry_run(self):
        self.create_file("file1.txt", "content1")
        processor = RepoProcessor(self.test_dir, self.output_file, dry_run=True)
        count = processor.process()

        assert count == 1
        assert not os.path.exists(self.output_file)

    def test_max_size_filtering(self):
        # Create a file larger than the max size limit
        large_content = "a" * 600000  # 600KB, larger than default 500KB
        self.create_file("large_file.txt", large_content)
        self.create_file("small_file.txt", "small content")

        processor = RepoProcessor(self.test_dir, self.output_file, max_file_size=512000, include_tree=False)  # 500KB
        count = processor.process()

        assert count == 1  # Only small file should be processed
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "small_file.txt" in content
            assert "large_file.txt" not in content

    def test_tree_inclusion(self):
        self.create_file("main.py", "print('hello')")
        self.create_file("README.md", "# project")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=True)
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "--- REPOSITORY STRUCTURE ---" in content
            assert "--- END REPOSITORY STRUCTURE ---" in content
            assert "main.py" in content
            assert "README.md" in content

    def test_no_tree_inclusion(self):
        self.create_file("main.py", "print('hello')")
        self.create_file("README.md", "# project")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "--- REPOSITORY STRUCTURE ---" not in content
            assert "--- END REPOSITORY STRUCTURE ---" not in content
            assert "main.py" in content
            assert "README.md" in content

    def test_tree_matches_dump(self):
        """Test that the tree structure matches what's actually dumped."""
        self.create_file(".gitignore", "*.log")
        self.create_file("main.py", "print('hello')")
        self.create_file("app.log", "log content")
        self.create_file("subdir/test.py", "pass")
        self.create_file("subdir/debug.log", "debug")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=True)
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Tree section
        tree_start = content.index("--- REPOSITORY STRUCTURE ---")
        tree_end = content.index("--- END REPOSITORY STRUCTURE ---")
        tree_section = content[tree_start:tree_end]

        # Tree should NOT mention .log files (they're ignored)
        assert "app.log" not in tree_section
        assert "debug.log" not in tree_section

        # Tree SHOULD mention .py files
        assert "main.py" in tree_section
        assert "test.py" in tree_section

        # Dump section should also NOT contain .log file contents
        assert "### File: app.log" not in content
        assert "### File: subdir/debug.log" not in content

    def test_tree_structure_method(self):
        """Test the generate_tree_structure method directly."""
        self.create_file("main.py", "print('hello')")
        self.create_file("README.md", "# project")
        os.makedirs(os.path.join(self.test_dir, "subdir"))
        self.create_file("subdir/test.py", "pass")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        tree = processor.generate_tree_structure()

        assert "--- REPOSITORY STRUCTURE ---" in tree
        assert "--- END REPOSITORY STRUCTURE ---" in tree
        assert "main.py" in tree
        assert "README.md" in tree
        assert "subdir/" in tree
        assert "test.py" in tree

    def test_token_counting_includes_delimiters(self):
        """Test that token counting includes delimiters."""
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(
            self.test_dir, 
            self.output_file, 
            count_tokens=True,
            include_tree=False
        )
        processor.process()

        # Token count should be > 0 and include delimiters
        assert processor.total_tokens > 0
        # The delimiters "### File: main.py" and "```" should be counted

    def test_token_counting_includes_tree(self):
        """Test that token counting includes the tree structure."""
        self.create_file("main.py", "print('hello')")

        processor_with_tree = RepoProcessor(
            self.test_dir, 
            self.output_file, 
            count_tokens=True,
            include_tree=True
        )
        processor_with_tree.process()

        processor_no_tree = RepoProcessor(
            self.test_dir, 
            self.output_file, 
            count_tokens=True,
            include_tree=False
        )
        processor_no_tree.process()

        # Token count with tree should be higher
        assert processor_with_tree.total_tokens > processor_no_tree.total_tokens

    def test_binary_extension_check(self):
        """Test that binary extensions are detected without reading content."""
        processor = RepoProcessor(self.test_dir, self.output_file)
        
        # Test various binary extensions
        for ext in ['.png', '.jpg', '.exe', '.pdf', '.zip']:
            fake_path = Path(f"fake{ext}")
            assert processor._is_binary(fake_path) is True, f"Failed for {ext}"

    def test_get_language_from_path(self):
        """Test language detection from file extension."""
        assert get_language_from_path("test.py") == "python"
        assert get_language_from_path("script.js") == "javascript"
        assert get_language_from_path("app.ts") == "typescript"
        assert get_language_from_path("README.md") == "markdown"
        assert get_language_from_path("unknown.xyz") == ""


def test_gitignore_cache():
    """Test that gitignore specs are cached properly."""
    test_dir = tempfile.mkdtemp()
    try:
        # Create nested .gitignore files
        os.makedirs(os.path.join(test_dir, "subdir1"))
        os.makedirs(os.path.join(test_dir, "subdir2"))

        with open(os.path.join(test_dir, ".gitignore"), "w") as f:
            f.write("*.log\n")
        with open(os.path.join(test_dir, "subdir1", ".gitignore"), "w") as f:
            f.write("*.tmp\n")

        output_file = os.path.join(test_dir, "output.txt")
        processor = RepoProcessor(test_dir, output_file)

        # First call should load and cache
        spec1 = processor._load_nested_gitignore(Path(os.path.join(test_dir, "subdir1")))
        assert spec1 is not None

        # Second call should return cached version
        spec2 = processor._load_nested_gitignore(Path(os.path.join(test_dir, "subdir1")))
        assert spec2 is spec1  # Same object (cached)

        # Non-existent gitignore should cache None
        spec3 = processor._load_nested_gitignore(Path(os.path.join(test_dir, "subdir2")))
        assert spec3 is None

        # Should be cached
        assert os.path.join(test_dir, "subdir2") in processor.gitignore_cache
    finally:
        shutil.rmtree(test_dir)


class TestDefaultJunkDirs:
    """Test fail-safe default ignore patterns."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "output.txt")

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    def create_file(self, path, content):
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_node_modules_ignored_by_default(self):
        """Test that node_modules is ignored even without .gitignore."""
        self.create_file("node_modules/package/index.js", "module.exports = {}")
        self.create_file("src/main.js", "console.log('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1  # Only src/main.js
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "node_modules" not in content
            assert "src/main.js" in content

    def test_venv_ignored_by_default(self):
        """Test that .venv and venv are ignored by default."""
        self.create_file(".venv/lib/python3.9/site-packages/pkg.py", "pass")
        self.create_file("venv/bin/activate", "#!/bin/bash")
        self.create_file("app.py", "print('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1  # Only app.py
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert ".venv" not in content
            assert "venv" not in content
            assert "app.py" in content

    def test_build_artifacts_ignored_by_default(self):
        """Test that build, dist, __pycache__ are ignored by default."""
        self.create_file("build/output.exe", "binary")
        self.create_file("dist/package.whl", "archive")
        self.create_file("__pycache__/module.pyc", "compiled")
        self.create_file("src/module.py", "def func(): pass")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1  # Only src/module.py


class TestDefaultIgnorePatterns:
    """Test fail-safe default ignore patterns."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "output.txt")

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    def create_file(self, path, content):
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_log_files_ignored_by_default(self):
        """Test that .log files are ignored by default."""
        self.create_file("app.log", "log content")
        self.create_file("logs/debug.log", "debug info")
        self.create_file("src/main.py", "print('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1  # Only src/main.py
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert ".log" not in content
            assert "src/main.py" in content

    def test_env_files_ignored_by_default(self):
        """Test that .env files are ignored by default."""
        self.create_file(".env", "SECRET_KEY=abc123")
        self.create_file(".env.local", "DEBUG=true")
        self.create_file("app.py", "print('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1  # Only app.py
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert ".env" not in content
            assert "app.py" in content

    def test_pyc_files_ignored_by_default(self):
        """Test that .pyc files are ignored by default."""
        self.create_file("module.pyc", "compiled")
        self.create_file("module.py", "def func(): pass")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1  # Only module.py
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "module.pyc" not in content
            assert "module.py" in content


class TestLLMInstructions:
    """Test LLM instructions header."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "output.txt")

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    def create_file(self, path, content):
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_llm_instructions_present(self):
        """Test that LLM instructions are at the top of the output."""
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert content.startswith("--- INSTRUCTIONS ---")
            assert "--- END INSTRUCTIONS ---" in content
            assert "LLM" in content

    def test_llm_instructions_counted_in_tokens(self):
        """Test that LLM instructions are included in token count."""
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            count_tokens=True, include_tree=False
        )
        processor.process()

        # Token count should include instructions
        assert processor.total_tokens > 0


class TestRobustFileReading:
    """Test robust file reading with encoding fallback."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "output.txt")

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    def create_file(self, path, content, encoding='utf-8'):
        full_path = os.path.join(self.test_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding=encoding) as f:
            f.write(content)

    def test_utf8_with_bom(self):
        """Test reading UTF-8 files with BOM (common in Windows)."""
        # Create file with UTF-8 BOM
        full_path = os.path.join(self.test_dir, "windows_file.py")
        with open(full_path, "w", encoding="utf-8-sig") as f:
            f.write("# -*- coding: utf-8 -*-\nprint('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "print('hello')" in content

    def test_latin1_fallback(self):
        """Test that latin-1 encoding works as fallback."""
        # Create file with latin-1 encoded content in a subdirectory
        full_path = os.path.join(self.test_dir, "texts", "french.txt")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="latin-1") as f:
            f.write("Café, naïve, résumé")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        count = processor.process()

        assert count == 1
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Content should be readable (may have different byte representation)
            assert "french.txt" in content
            # The accented characters should be present in some form
            assert "Caf" in content


class TestExpandedLanguageMap:
    """Test expanded language detection."""

    def test_more_languages(self):
        """Test detection of additional languages."""
        assert get_language_from_path("script.dart") == "dart"
        assert get_language_from_path("script.groovy") == "groovy"
        assert get_language_from_path("infrastructure.tf") == "terraform"
        assert get_language_from_path("query.graphql") == "graphql"
        assert get_language_from_path("notebook.ipynb") == "jupyter"
        assert get_language_from_path("Dockerfile") == "dockerfile"
        assert get_language_from_path("Makefile") == "makefile"
        assert get_language_from_path("script.ps1") == "powershell"
        assert get_language_from_path("config.toml") == "toml"
        assert get_language_from_path("data.parquet") in ["", "parquet"]
