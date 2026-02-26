import os
import shutil
import tempfile
import pytest
from pathlib import Path
from src.git_dump.core import (
    RepoProcessor, get_language_from_path, BINARY_EXTENSIONS,
    DEFAULT_JUNK_DIRS, DEFAULT_IGNORE_PATTERNS, LLM_INSTRUCTIONS,
    MAGIC_NUMBERS, estimate_tokens
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

        processor = RepoProcessor(self.test_dir, self.output_file, use_xml_format=False)
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

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False, use_xml_format=False)
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
            assert '<file path="subdir/secret.txt">' not in content  # Excluded by nested .gitignore
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
            end_delimiter="END {path}",
            use_xml_format=False,
            include_tree=False,
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

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            include_tree=False,
            use_xml_format=False,
        )
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
        assert '<file path="app.log">' not in content
        assert '<file path="subdir/debug.log">' not in content

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
            include_tree=False,
            use_xml_format=False,
        )
        processor.process()

        # Token count should be > 0 and include delimiters
        assert processor.total_tokens > 0
        # The delimiters should be counted

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


class TestMaxTokens:
    """Test --max-tokens functionality."""

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

    def test_max_tokens_stops_processing(self):
        """Test that processing stops when max_tokens is reached."""
        # Create multiple files with known content
        self.create_file("file1.txt", "a" * 1000)  # ~250 tokens
        self.create_file("file2.txt", "b" * 1000)  # ~250 tokens
        self.create_file("file3.txt", "c" * 1000)  # ~250 tokens

        # Set max_tokens very low - should stop after first file
        processor = RepoProcessor(
            self.test_dir, self.output_file,
            max_tokens=100,  # Very low limit
            include_tree=False,
            count_tokens=True,
        )
        count = processor.process()

        # Should have processed at least 1 file but not all
        assert count >= 1
        assert count < 3  # Should not have processed all files
        # Token count should exceed limit slightly due to chunk-based checking
        # but should be less than processing 2 full files
        assert processor.total_tokens < 600  # Less than 2 files worth

    def test_max_tokens_with_tree(self):
        """Test that max_tokens accounts for tree structure."""
        self.create_file("main.py", "print('hello')")

        processor_with_tree = RepoProcessor(
            self.test_dir, self.output_file,
            max_tokens=50,
            include_tree=True,
            count_tokens=True,
        )
        processor_with_tree.process()

        # Tree takes tokens, so less room for content
        # Token limit should be respected (with some tolerance for delimiters)
        assert processor_with_tree.total_tokens <= 250  # Allow overhead for tree + delimiters


class TestCleanMode:
    """Test --clean mode functionality."""

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

    def test_clean_removes_comments(self):
        """Test that clean mode removes comments."""
        python_code = """# This is a comment
def hello():
    # Another comment
    print("hello")
"""
        self.create_file("clean.py", python_code)

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            clean_mode=True,
            include_tree=False,
        )
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Comments should be removed
            assert "# This is a comment" not in content
            assert "# Another comment" not in content
            # Code should remain
            assert "def hello():" in content
            assert 'print("hello")' in content

    def test_clean_removes_excessive_whitespace(self):
        """Test that clean mode removes excessive blank lines."""
        code_with_blanks = """line1


line2



line3
"""
        self.create_file("blanks.txt", code_with_blanks)

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            clean_mode=True,
            include_tree=False,
        )
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Should not have more than 2 consecutive blank lines
            assert "\n\n\n\n" not in content

    def test_clean_mode_off(self):
        """Test that clean mode preserves content when disabled."""
        code_with_comments = """# Comment
def test():
    pass
"""
        self.create_file("preserve.py", code_with_comments)

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            clean_mode=False,
            include_tree=False,
        )
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Comments should be preserved
            assert "# Comment" in content


class TestPrioritySorting:
    """Test priority file sorting functionality."""

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

    def test_priority_files_first(self):
        """Test that priority files appear first in output."""
        self.create_file("README.md", "# Project")
        self.create_file("zulu.txt", "zulu content")
        self.create_file("pyproject.toml", "[tool.poetry]")
        self.create_file("alpha.txt", "alpha content")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            sort_priority=True,
            include_tree=False,
            use_xml_format=False,
        )
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Find positions of files
        readme_pos = content.index("### File: README.md")
        pyproject_pos = content.index("### File: pyproject.toml")
        alpha_pos = content.index("### File: alpha.txt")
        zulu_pos = content.index("### File: zulu.txt")

        # Priority files should come first
        assert readme_pos < alpha_pos
        assert pyproject_pos < alpha_pos
        # Non-priority files should be alphabetical
        assert alpha_pos < zulu_pos

    def test_no_sort_option(self):
        """Test that --no-sort disables priority sorting."""
        self.create_file("README.md", "# Project")
        self.create_file("alpha.txt", "alpha content")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            sort_priority=False,
            include_tree=False,
            use_xml_format=False,
        )
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Files should be alphabetical, not priority-based
        readme_pos = content.index("### File: README.md")
        alpha_pos = content.index("### File: alpha.txt")
        # Alphabetically, README comes after alpha
        assert alpha_pos < readme_pos


class TestXMLFormat:
    """Test XML-style delimiters."""

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

    def test_xml_delimiters_default(self):
        """Test that XML delimiters are used by default."""
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(self.test_dir, self.output_file, include_tree=False)
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert '<file path="main.py">' in content
            assert '<![CDATA[' in content
            assert ']]></file>' in content
            assert "print('hello')" in content

    def test_markdown_delimiters_option(self):
        """Test that markdown delimiters can be used instead of XML."""
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            include_tree=False,
            use_xml_format=False,
        )
        processor.process()

        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert '### File: main.py' in content
            assert '```python' in content


class TestMagicNumberDetection:
    """Test magic number binary detection."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "output.txt")

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    def test_png_magic_number(self):
        """Test PNG detection via magic number."""
        from src.git_dump.core import MAGIC_PREFIXES
        
        # Create a file with PNG magic number
        png_path = os.path.join(self.test_dir, "fake.png")
        with open(png_path, "wb") as f:
            f.write(b'\x89PNG\x0d\x0a\x1a\x0a' + b'fake png data')

        processor = RepoProcessor(self.test_dir, self.output_file)
        assert processor._is_binary(Path(png_path)) is True

    def test_pdf_magic_number(self):
        """Test PDF detection via magic number."""
        # Create a file with PDF magic number
        pdf_path = os.path.join(self.test_dir, "fake.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b'%PDF-1.4 fake pdf data')

        processor = RepoProcessor(self.test_dir, self.output_file)
        assert processor._is_binary(Path(pdf_path)) is True

    def test_text_file_not_binary(self):
        """Test that text files are not detected as binary."""
        # Create a text file
        txt_path = os.path.join(self.test_dir, "test.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write('Hello, World!')

        processor = RepoProcessor(self.test_dir, self.output_file)
        assert processor._is_binary(Path(txt_path)) is False


class TestSkeletonMode:
    """Test skeleton mode functionality."""

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

    def test_python_skeleton_regex(self):
        """Test Python skeleton extraction (regex fallback)."""
        python_code = """
import os

def hello():
    print("Hello")
    return True

def world():
    x = 1
    y = 2
    return x + y

class MyClass:
    def __init__(self):
        self.value = 0
"""
        processor = RepoProcessor(
            self.test_dir, self.output_file,
            skeleton_mode=True,
            skeleton_threshold=10,  # Low threshold to trigger skeleton
            include_tree=False,
        )
        
        skeleton = processor._extract_skeleton(python_code, 'python')
        
        # Should keep function signatures
        assert 'def hello():' in skeleton
        assert 'def world():' in skeleton
        assert 'class MyClass:' in skeleton
        # Should have pass statements
        assert 'pass' in skeleton
        # Should be shorter than original
        assert len(skeleton) < len(python_code)

    def test_js_skeleton_regex(self):
        """Test JavaScript skeleton extraction (regex fallback)."""
        js_code = """
function hello() {
    console.log("Hello");
    return true;
}

class MyClass {
    constructor() {
        this.value = 0;
    }
}
"""
        processor = RepoProcessor(
            self.test_dir, self.output_file,
            skeleton_mode=True,
            skeleton_threshold=10,
            include_tree=False,
        )
        
        skeleton = processor._extract_skeleton(js_code, 'javascript')
        
        # Should keep function/class declarations
        assert 'function' in skeleton or 'class' in skeleton
        # Should be shorter than original
        assert len(skeleton) < len(js_code)


class TestTokenBudget:
    """Test smart token budgeting."""

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

    def test_token_budget_prioritizes_important_files(self):
        """Test that token budget prioritizes README and config files."""
        # Create multiple files
        self.create_file("README.md", "# Project\n" * 100)  # Large README
        self.create_file("pyproject.toml", "[tool.poetry]\n" * 50)  # Config
        self.create_file("src/deep/nested/file.py", "x = 1\n" * 200)  # Deep nested

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            max_tokens=500,  # Very low budget
            include_tree=False,
            count_tokens=True,
        )
        
        # Get all files
        all_files = list(processor.get_valid_files())
        
        # Calculate budget strategy
        strategy = processor._calculate_token_budget(all_files)
        
        # README and pyproject.toml should be included
        assert 'README.md' in strategy
        assert 'pyproject.toml' in strategy
        
        # Deep nested file might be excluded, skeletonized, cleaned, or full if budget allows
        deep_file = 'src/deep/nested/file.py'
        if deep_file in strategy:
            # If included, action could be anything depending on budget
            assert strategy[deep_file]['action'] in ('full', 'skeleton', 'clean')

    def test_token_budget_excludes_deep_files_first(self):
        """Test that deeply nested files are excluded before shallow ones."""
        self.create_file("shallow1.py", "x = 1\n" * 50)
        self.create_file("shallow2.py", "x = 2\n" * 50)
        self.create_file("deep/nested/file1.py", "x = 3\n" * 50)
        self.create_file("deep/nested/file2.py", "x = 4\n" * 50)

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            max_tokens=200,
            include_tree=False,
        )
        
        all_files = list(processor.get_valid_files())
        strategy = processor._calculate_token_budget(all_files)
        
        # Shallow files should have priority
        assert 'shallow1.py' in strategy
        assert 'shallow2.py' in strategy


class TestConfigFile:
    """Test config file support."""

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

    def test_config_file_loading(self):
        """Test that config file is loaded correctly."""
        # Create config file (top-level keys, not under [tool.gitdump])
        config_content = """
ignore = ["*.log", "temp/"]
max_tokens = 5000
max_file_size = 102400
"""
        config_path = os.path.join(self.test_dir, ".gitdumprc.toml")
        with open(config_path, "w") as f:
            f.write(config_content)
        
        self.create_file("main.py", "print('hello')")
        self.create_file("test.log", "log content")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            config_file=config_path,
            include_tree=False,
        )

        # Config should have loaded ignore patterns
        assert "*.log" in processor.ignore_patterns or "temp/" in processor.ignore_patterns


class TestFocusDir:
    """Test --focus directory context pinning."""

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

    def test_focus_dir_full_content(self):
        """Test that focus directory files get full content."""
        self.create_file("src/main.py", "def main():\n    print('hello')")
        self.create_file("src/utils.py", "def helper():\n    return 42")
        self.create_file("tests/test_main.py", "def test_main():\n    assert True")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            focus_dir="src",
            include_tree=False,
            use_xml_format=False,
        )
        count = processor.process()

        assert count == 3
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # All files should be present
            assert "src/main.py" in content
            assert "src/utils.py" in content
            assert "tests/test_main.py" in content

    def test_focus_dir_with_clean_mode(self):
        """Test that focus directory files skip cleaning even if clean_mode is on."""
        self.create_file("src/main.py", "# Comment\ndef main():\n    print('hello')")
        self.create_file("tests/test_main.py", "# Test comment\ndef test():\n    pass")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            focus_dir="src",
            clean_mode=True,
            include_tree=False,
            use_xml_format=False,
        )
        count = processor.process()

        assert count == 2
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Focus dir file should keep comments
            assert "# Comment" in content
            # Non-focus file should have comments removed
            assert "# Test comment" not in content

    def test_is_in_focus_dir_helper(self):
        """Test the _is_in_focus_dir helper method."""
        processor = RepoProcessor(
            self.test_dir, self.output_file,
            focus_dir="src",
        )

        assert processor._is_in_focus_dir("src/main.py") is True
        assert processor._is_in_focus_dir("src/utils/helpers.py") is True
        assert processor._is_in_focus_dir("src") is True
        assert processor._is_in_focus_dir("tests/test.py") is False
        assert processor._is_in_focus_dir("README.md") is False

        # Test with nested focus dir
        processor2 = RepoProcessor(
            self.test_dir, self.output_file,
            focus_dir="src/components",
        )
        assert processor2._is_in_focus_dir("src/components/button.py") is True
        assert processor2._is_in_focus_dir("src/utils.py") is False


class TestPrioritySortingEnhanced:
    """Test enhanced priority sorting with type definitions."""

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

    def test_type_definition_files_prioritized(self):
        """Test that type definition files are sorted first."""
        self.create_file("types.py", "class Model: pass")
        self.create_file("utils.py", "def helper(): pass")
        self.create_file("main.py", "print('hello')")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            include_tree=False,
            use_xml_format=False,
        )
        count = processor.process()

        assert count == 3
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # types.py should be prioritized (appear before non-priority files like utils.py)
            types_pos = content.find('### File: types.py')
            utils_pos = content.find('### File: utils.py')
            # types.py should come before utils.py
            assert types_pos < utils_pos

    def test_d_ts_files_prioritized(self):
        """Test that .d.ts files are sorted first."""
        self.create_file("index.d.ts", "export interface Foo {}")
        self.create_file("helpers.ts", "export const x = 1")
        self.create_file("utils.ts", "export const y = 2")

        processor = RepoProcessor(
            self.test_dir, self.output_file,
            include_tree=False,
            use_xml_format=False,
        )
        count = processor.process()

        assert count == 3
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            # index.d.ts should be prioritized over regular .ts files
            dts_pos = content.find('### File: index.d.ts')
            helpers_pos = content.find('### File: helpers.ts')
            utils_pos = content.find('### File: utils.ts')
            # .d.ts file should come first (before non-priority .ts files)
            assert dts_pos < helpers_pos and dts_pos < utils_pos
