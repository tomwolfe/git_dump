import os
import shutil
import tempfile
import unittest
from git_dump import RepoProcessor

class TestRepoProcessor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "output.txt")

    def tearDown(self):
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
        
        self.assertEqual(count, 2)
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("--- FILE: file1.txt ---", content)
            self.assertIn("content1", content)
            self.assertIn("--- FILE: dir/file2.txt ---", content)
            self.assertIn("content2", content)

    def test_ignore_git(self):
        self.create_file(".git/config", "git config")
        self.create_file("file1.txt", "content1")
        
        processor = RepoProcessor(self.test_dir, self.output_file)
        count = processor.process()
        
        self.assertEqual(count, 1)
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertNotIn(".git/config", content)

    def test_gitignore_logic(self):
        self.create_file(".gitignore", "*.log\ntemp/")
        self.create_file("app.log", "log content")
        self.create_file("temp/data.txt", "temp data")
        self.create_file("main.py", "print('hello')")
        
        processor = RepoProcessor(self.test_dir, self.output_file)
        count = processor.process()
        
        self.assertEqual(count, 2) # .gitignore and main.py
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("main.py", content)
            self.assertNotIn("app.log", content)
            self.assertNotIn("temp/data.txt", content)

    def test_include_patterns(self):
        self.create_file("main.py", "print('hello')")
        self.create_file("README.md", "# project")
        self.create_file("data.json", "{}")
        
        processor = RepoProcessor(self.test_dir, self.output_file, include_patterns=["*.py", "*.md"])
        count = processor.process()
        
        self.assertEqual(count, 2)
        with open(self.output_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("main.py", content)
            self.assertIn("README.md", content)
            self.assertNotIn("data.json", content)

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
            self.assertIn("START file1.txt", content)
            self.assertIn("END file1.txt", content)

    def test_dry_run(self):
        self.create_file("file1.txt", "content1")
        processor = RepoProcessor(self.test_dir, self.output_file, dry_run=True)
        count = processor.process()
        
        self.assertEqual(count, 1)
        self.assertFalse(os.path.exists(self.output_file))

if __name__ == "__main__":
    unittest.main()