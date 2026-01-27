# Git Dump

A robust CLI tool to concatenate the contents of a Git repository into a single text file. Useful for feeding codebase context into LLMs.

## Features

- **Respects .gitignore**: Automatically skips files ignored by Git (requires `pathspec`).
- **Nested .gitignore Support**: Respects `.gitignore` files in subdirectories, not just the root.
- **Binary File Detection**: Automatically skips binary files.
- **Directory Tree Structure**: Generates a visual directory tree at the top of the output for better LLM context.
- **Custom Patterns**: Include or exclude files using glob patterns.
- **Flexible Output**: Specify custom output filenames and delimiters.
- **Token Estimation**: Estimate token count for LLM context window management.
- **Max File Size Filter**: Skip files larger than a specified size to avoid overwhelming LLM context windows.
- **Stream Processing**: Memory-safe processing of large files using generators.
- **Dry Run Mode**: See what would be processed without writing any files.
- **Quiet Mode**: Reduce output for scripts and automation.

## Installation

Install from PyPI:

```bash
pip install git-dump
# Or with tiktoken for exact token counting
pip install git-dump[tiktoken]
```

Or install from source:

```bash
pip install -e .
# Or with tiktoken for exact token counting
pip install -e .[tiktoken]
```

## Usage

Basic usage:
```bash
git-dump /path/to/repo
```

Advanced usage:
```bash
git-dump /path/to/repo -o codebase.txt --include "*.py" --include "*.md" --ignore "temp/*" --max-size 1000000
```

With token counting:
```bash
git-dump /path/to/repo --count-tokens
```

Without directory tree:
```bash
git-dump /path/to/repo --no-tree
```

### Options

- `repo_path`: Path to the root of the Git repository.
- `-o`, `--output`: Output filename (default: `repository_contents.txt`).
- `--no-gitignore`: Do not respect `.gitignore` files.
- `-i`, `--ignore`: Additional glob patterns to ignore.
- `--include`: Patterns to include (if specified, only matching files are included).
- `--start-delimiter`: Custom start delimiter (default: `--- FILE: {path} ---`).
- `--end-delimiter`: Custom end delimiter (default: `--- END FILE ---`).
- `--max-size`: Maximum file size to include in bytes (default: 512000 = 500KB).
- `--no-tree`: Do not include directory tree structure.
- `--count-tokens`: Count total tokens in output (requires tiktoken if available).
- `-q`, `--quiet`: Quiet mode (minimal output).
- `--dry-run`: Show what would be processed without writing to disk.

## Best Practices for LLMs

When using this tool for LLM context:

1. **Use the Directory Tree**: The directory structure at the beginning of the output helps LLMs understand the project organization.

2. **Monitor Token Count**: Use the `--count-tokens` flag to estimate how much context you're providing. Most LLMs have context window limits (e.g., 4K, 8K, 32K tokens).

3. **Filter Large Files**: Use `--max-size` to exclude large files like logs, data dumps, or minified JavaScript that don't contribute meaningful context.

4. **Include Relevant Extensions**: Use `--include` to focus on specific file types (e.g., `--include "*.py"` for Python projects).

5. **Respect Git Ignores**: The tool automatically respects `.gitignore` files, which typically exclude irrelevant files like build artifacts and dependencies.

## Development

To set up for development:

```bash
pip install -e .[tiktoken]
pip install pytest
```

Run tests:
```bash
pytest tests/
```

## License

MIT