# git-dump

A robust CLI tool to concatenate Git repository contents into a single, well-structured text file for LLM context analysis.

## Features

- **Smart File Filtering**: Respects `.gitignore`, ignores binary files, and skips common junk directories
- **Token Budgeting**: Fits dumps within LLM context limits using intelligent prioritization
- **Clean Mode**: Removes comments and excessive whitespace to reduce token count
- **Skeleton Mode**: Extracts only function/class signatures for large files (requires tree-sitter)
- **Focus Directory**: Pin context on specific directories while skeletonizing others
- **Tree Structure**: Includes a directory tree that exactly matches included files
- **Multiple Encodings**: Robust file reading with UTF-8, UTF-8-BOM, and Latin-1 fallback
- **Git Branch/Commit**: Dump specific branches or commits using temporary worktrees

## Installation

```bash
# Basic installation
pip install -e .

# With all optional dependencies for maximum functionality
pip install -e ".[all]"
```

### Optional Dependencies

- `tiktoken`: Accurate token counting for OpenAI models
- `pyperclip`: Copy output to clipboard
- `tree-sitter-*`: Skeleton mode for code abstraction

## Usage

### Basic Usage

```bash
Basic usage:

source venv/bin/activate
git-dump /path/to/your/repo

# Dump to specific output file
git-dump . -o context.txt

# Dump a specific repository
git-dump /path/to/repo -o output.txt
```

### Advanced Options

```bash
# Clean mode: remove comments and whitespace
git-dump . --clean -o context.txt

# Focus on specific directory (full content for src/, skeletonize rest)
git-dump . --focus src --skeleton -o context.txt

# Limit by token count (smart budgeting)
git-dump . --max-tokens 100000 --clean --skeleton

# Count tokens in output
git-dump . --count-tokens

# Copy to clipboard
git-dump . --clipboard

# Use markdown delimiters instead of XML
git-dump . --no-xml

# Dump a specific branch
git-dump . --branch feature-xyz

# Dump a specific commit
git-dump . --commit abc123
```

### Configuration File

Create a `.gitdumprc.toml` in your repository root:

```toml
# Patterns to ignore (in addition to .gitignore)
ignore = [
    "*.log",
    "docs/**",
    "tests/**",
]

# Maximum token count (for smart budgeting)
max_tokens = 100000

# Maximum file size in bytes (default: 512000 = 500KB)
max_file_size = 512000
```

## Usage Patterns for LLMs

### Pattern 1: Full Context for Debugging

```bash
git-dump . --clean --count-tokens -o debugging_context.txt
```

Best for: General debugging, understanding codebase structure

### Pattern 2: Focus on Active Development

```bash
git-dump . --focus src/features/new-feature --skeleton --clean -o feature_context.txt
```

Best for: Working on a specific feature while maintaining awareness of the rest

### Pattern 3: Fit Within Token Limits

```bash
git-dump . --max-tokens 100000 --clean --skeleton --skeleton-threshold 500
```

Best for: Large codebases that exceed LLM context windows

### Pattern 4: Architecture Review

```bash
git-dump . --include "*.py" --include "*.ts" --include "types.py" --include "models.py" -o architecture.txt
```

Best for: Understanding type definitions and interfaces

### Pattern 5: Code Review

```bash
git-dump . --branch feature-branch --clean -o review_context.txt
```

Best for: Reviewing changes in isolation

## Output Format

The default output uses XML-style delimiters with CDATA sections:

```xml
--- INSTRUCTIONS ---
This file contains a complete dump of the repository's source code for LLM analysis.
...

--- REPOSITORY STRUCTURE ---
repo_name/
├── src/
│   ├── main.py
│   └── utils.py
└── README.md
--- END REPOSITORY STRUCTURE ---

<file path="README.md"><![CDATA[
# Project Name
...
]]></file>

<file path="src/main.py"><![CDATA[
def main():
    print("Hello, World!")
]]></file>
```

## File Prioritization

Files are automatically sorted by importance:

1. **Documentation**: README, CHANGELOG, LICENSE
2. **Config Files**: pyproject.toml, package.json, tsconfig.json
3. **Entry Points**: main.py, app.py, index.ts
4. **Type Definitions**: types.py, models.py, *.d.ts
5. **Other Files**: Alphabetically sorted

## How It Works

1. **Discovery**: Uses `git ls-files` for speed, falls back to `os.walk`
2. **Filtering**: Applies `.gitignore`, binary detection, size limits
3. **Budgeting** (if `--max-tokens`): Multi-pass analysis to fit within limits
4. **Processing**: Applies clean/skeleton modes as configured
5. **Formatting**: Wraps files in XML/Markdown delimiters
6. **Output**: Writes to file and optionally copies to clipboard

## Best Practices

1. **Use `--clean` for larger context**: Removes ~30-50% of tokens
2. **Use `--focus` for feature work**: Keeps relevant files detailed
3. **Use `--skeleton` for large files**: Preserves structure without implementation
4. **Set `max_tokens` slightly below your limit**: Account for your prompt overhead
5. **Exclude tests/docs for code tasks**: Use `.gitdumprc.toml` to ignore them

## Troubleshooting

### "Token limit reached" warning

- Increase `--max-tokens` or use `--clean --skeleton`
- Exclude large directories in `.gitdumprc.toml`

### "Skeleton mode not working"

- Tree-sitter requires compiled language grammars
- Install: `pip install tree-sitter tree-sitter-python tree-sitter-javascript`
- Fallback regex mode works automatically if tree-sitter unavailable

### "Binary file detected" error

- File has binary content or magic numbers
- Add to `.gitignore` if it should be text

## License

MIT
