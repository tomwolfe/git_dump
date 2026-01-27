import os
import shutil
import tempfile
import pytest
from src.git_dump.core import RepoProcessor, generate_tree_structure


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


def test_generate_tree_structure():
    # Create a temporary directory structure for testing
    test_dir = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(test_dir, "subdir"))
        with open(os.path.join(test_dir, "file1.txt"), "w") as f:
            f.write("content1")
        with open(os.path.join(test_dir, "subdir", "file2.txt"), "w") as f:
            f.write("content2")
        
        tree_output = generate_tree_structure(test_dir)
        
        assert "--- REPOSITORY STRUCTURE ---" in tree_output
        assert "--- END REPOSITORY STRUCTURE ---" in tree_output
        assert "file1.txt" in tree_output
        assert "subdir/" in tree_output
        assert "file2.txt" in tree_output
    finally:
        shutil.rmtree(test_dir)