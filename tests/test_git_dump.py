import os
import shutil
import tempfile
import pytest
from src.git_dump.core import RepoProcessor


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
            assert "--- FILE: file1.txt ---" in content
            assert "content1" in content
            assert "--- FILE: dir/file2.txt ---" in content
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
            assert "--- FILE: subdir/secret.txt ---" not in content  # Excluded by nested .gitignore
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
        assert "--- FILE: app.log ---" not in content
        assert "--- FILE: subdir/debug.log ---" not in content

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
        spec1 = processor._load_nested_gitignore(os.path.join(test_dir, "subdir1"))
        assert spec1 is not None
        
        # Second call should return cached version
        spec2 = processor._load_nested_gitignore(os.path.join(test_dir, "subdir1"))
        assert spec2 is spec1  # Same object (cached)
        
        # Non-existent gitignore should cache None
        spec3 = processor._load_nested_gitignore(os.path.join(test_dir, "subdir2"))
        assert spec3 is None
        
        # Should be cached
        assert os.path.join(test_dir, "subdir2") in processor.gitignore_cache
    finally:
        shutil.rmtree(test_dir)
