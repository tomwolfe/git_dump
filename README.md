# git-dump

A robust CLI tool to concatenate Git repository contents into a single, well-structured text file for LLM context analysis.

## Features

- **Smart File Filtering**: Respects `.gitignore`, ignores binary files, and skips common junk directories
- **Token Budgeting**: Fits dumps within LLM context limits using intelligent prioritization
- **Clean Mode**: Removes comments and excessive whitespace to reduce token count
- **Aggressive Clean**: Also removes docstrings and collapses empty lines for maximum compression
- **Skeleton Mode**: Extracts only function/class signatures for large files (works without tree-sitter!)
- **Focus Directory**: Pin context on specific directories while skeletonizing others
- **Dependency Resolution**: Auto-include skeletons of imported modules for focused files
- **Diff Mode**: Dump changed files in full, others as skeletons for PR reviews
- **Interactive Mode**: TUI checkbox menu for visual file selection
- **Model-Specific Token Counting**: Accurate counts for GPT-4, GPT-4o, Claude, Llama, etc.
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
- `tree-sitter-*`: Enhanced skeleton mode (regex fallback works without it)
- `questionary`: Interactive TUI mode

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

# Aggressive clean: also remove docstrings (maximum compression)
git-dump . --clean --clean-aggressive -o context.txt

# Focus on specific directory (full content for src/, skeletonize rest)
git-dump . --focus src --skeleton -o context.txt

# Auto-include dependencies of focused files
git-dump . --focus src/auth --deps -o auth_context.txt

# Diff mode: changed files full, others skeletonized
git-dump . --diff main --skeleton -o pr_review.txt

# Diff staged changes only
git-dump . --diff-staged --skeleton -o staged_review.txt

# Limit by token count (smart budgeting)
git-dump . --max-tokens 100000 --clean --skeleton

# Count tokens for specific model (gpt-4, gpt-4o, gpt-3.5-turbo, claude, llama)
git-dump . --count-tokens --model gpt-4o

# Copy to clipboard
git-dump . --clipboard

# Use markdown delimiters instead of XML
git-dump . --no-xml

# Interactive mode: select files visually
git-dump . --interactive

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

### Pattern 3: PR Review (Diff Mode)

```bash
git-dump . --diff main --skeleton --clean -o pr_review.txt
```

Best for: Code reviews - shows changed files in full, rest as skeleton context

### Pattern 4: Fit Within Token Limits

```bash
git-dump . --max-tokens 100000 --clean --skeleton --skeleton-threshold 500
```

Best for: Large codebases that exceed LLM context windows

### Pattern 5: Maximum Compression

```bash
git-dump . --clean --clean-aggressive --skeleton -o compressed.txt
```

Best for: Fitting the maximum amount of code into limited context

### Pattern 6: Architecture Review

```bash
git-dump . --include "*.py" --include "*.ts" --include "types.py" --include "models.py" -o architecture.txt
```

Best for: Understanding type definitions and interfaces

### Pattern 7: Interactive Selection

```bash
git-dump . --interactive --clipboard
```

Best for: When you want to visually pick which files to include

### Pattern 8: Dependency-Aware Focus

```bash
git-dump . --focus src/auth --deps --skeleton -o auth_with_deps.txt
```

Best for: Understanding a module plus its local dependencies

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
3. **Budgeting** (if `--max-tokens` or `--diff`): Multi-pass analysis to fit within limits
4. **Dependency Resolution** (if `--deps`): Analyzes imports and adds skeleton dependencies
5. **Processing**: Applies clean/skeleton modes as configured
   - Enhanced regex skeletonization for Python, TypeScript, Rust, Go, Java, C++
   - No tree-sitter required (works with regex fallback)
6. **Formatting**: Wraps files in XML/Markdown delimiters
7. **Output**: Writes to file and optionally copies to clipboard
8. **Token Counting**: Uses model-specific encodings (GPT-4, GPT-4o, Claude, Llama)

## Best Practices

1. **Use `--clean` for larger context**: Removes ~30-50% of tokens
2. **Use `--clean-aggressive` for maximum compression**: Removes docstrings too (~60-70% reduction)
3. **Use `--focus` for feature work**: Keeps relevant files detailed
4. **Use `--diff` for PR reviews**: Changed files full, context skeletonized
5. **Use `--deps` with focus**: Auto-include imported module skeletons
6. **Use `--skeleton` for large files**: Preserves structure without implementation
7. **Set `max_tokens` slightly below your limit**: Account for your prompt overhead
8. **Specify `--model` for accurate counting**: Match your target LLM
9. **Exclude tests/docs for code tasks**: Use `.gitdumprc.toml` to ignore them
10. **Use `--interactive` for visual selection**: When you need fine-grained control

## Troubleshooting

### "Token limit reached" warning

- Increase `--max-tokens` or use `--clean --clean-aggressive --skeleton`
- Exclude large directories in `.gitdumprc.toml`
- Use `--diff` mode to focus on changed files only

### "Skeleton mode not working"

- Tree-sitter is optional - regex fallback works automatically
- For enhanced skeletons: `pip install tree-sitter tree-sitter-python tree-sitter-javascript`
- Now supports Python, TypeScript, JavaScript, Rust, Go, Java, C++, C# out of the box

### "Interactive mode not working"

- Install questionary: `pip install questionary`
- Or use: `pip install -e ".[all]"` for all dependencies

### "Binary file detected" error

- File has binary content or magic numbers
- Add to `.gitignore` if it should be text

## License

MIT
