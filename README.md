# Git Dump

A robust CLI tool to concatenate the contents of a Git repository into a single text file. Useful for feeding codebase context into LLMs.

## Features

- **Respects .gitignore**: Automatically skips files ignored by Git (requires `pathspec`).
- **Binary File Detection**: Automatically skips binary files.
- **Custom Patterns**: Include or exclude files using glob patterns.
- **Flexible Output**: Specify custom output filenames and delimiters.
- **Dry Run Mode**: See what would be processed without writing any files.
- **Quiet Mode**: Reduce output for scripts and automation.

## Installation

Ensure you have Python 3.6+ installed. It is recommended to install `pathspec` for better `.gitignore` support:

```bash
pip install pathspec
```

## Usage

Basic usage:
```bash
python git_dump.py /path/to/repo
```

Advanced usage:
```bash
python git_dump.py /path/to/repo -o codebase.txt --include "*.py" --include "*.md" --ignore "temp/*"
```

### Options

- `repo_path`: Path to the root of the Git repository.
- `-o`, `--output`: Output filename (default: `repository_contents.txt`).
- `--no-gitignore`: Do not respect `.gitignore` files.
- `-i`, `--ignore`: Additional glob patterns to ignore.
- `--include`: Patterns to include (if specified, only matching files are included).
- `--start-delimiter`: Custom start delimiter (default: `--- FILE: {path} ---`).
- `--end-delimiter`: Custom end delimiter (default: `--- END FILE ---`).
- `-q`, `--quiet`: Quiet mode (minimal output).
- `--dry-run`: Show what would be processed without writing to disk.

## Running Tests

```bash
PYTHONPATH=. pytest tests/test_git_dump.py
```
