"""Core functionality for git_dump."""

import os
import sys
import logging
import subprocess
import tempfile
import shutil
import re
from pathlib import Path
from typing import List, Optional, Generator, Tuple, Dict, Set
import fnmatch

try:
    import pathspec
except ImportError:
    pathspec = None

logger = logging.getLogger(__name__)

# Common binary file extensions to skip without reading content
BINARY_EXTENSIONS: Set[str] = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svg',
    '.exe', '.dll', '.so', '.dylib', '.bin', '.dat',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.tar', '.gz', '.rar', '.7z', '.bz2',
    '.mp3', '.mp4', '.avi', '.mov', '.wav', '.flac',
    '.pyc', '.pyo', '.class', '.o', '.a',
    '.db', '.sqlite', '.sqlite3',
    '.woff', '.woff2', '.ttf', '.eot',
    '.parquet', '.pickle', '.pkl', '.npy', '.npz', '.h5', '.hdf5',
    '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v',
    '.psd', '.ai', '.eps', '.indd',
    '.key', '.numbers', '.pages',
}

# Default junk directories to ignore even if no .gitignore exists
DEFAULT_JUNK_DIRS: Set[str] = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build',
    '.tox', '.eggs', '*.egg-info', '.pytest_cache', '.mypy_cache', '.coverage',
    '.husky', '.idea', '.vscode', '.vs', '.eclipse', '.settings',
    'target', 'bin', 'obj', '.gradle', '.mvn',
    '.cache', '.tmp', '.temp', 'tmp', 'temp',
    'coverage', '.nyc_output', '.parcel-cache',
}

# Default ignore patterns for fail-safe filtering
DEFAULT_IGNORE_PATTERNS: List[str] = [
    '*.log', '*.pid', '*.lock', '*.swp', '*.swo', '*~',
    '*.pyc', '*.pyo', '*.pyd',
    '.DS_Store', 'Thumbs.db', 'desktop.ini',
    '*.bak', '*.backup', '*.orig',
    '.env', '.env.local', '.env.*.local',
    '*.min.js', '*.min.css',
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
]

# Priority files to sort first (for better LLM context)
PRIORITY_FILES: List[str] = [
    'README.md',
    'README.rst',
    'README.txt',
    'CHANGELOG.md',
    'LICENSE',
    'LICENSE.md',
    'LICENSE.txt',
    'CONTRIBUTING.md',
    'pyproject.toml',
    'setup.py',
    'setup.cfg',
    'requirements.txt',
    'requirements-dev.txt',
    'package.json',
    'package-lock.json',
    'yarn.lock',
    'tsconfig.json',
    'jsconfig.json',
    'Cargo.toml',
    'go.mod',
    'go.sum',
    'Gemfile',
    'Gemfile.lock',
    'pom.xml',
    'build.gradle',
    'settings.gradle',
    'CMakeLists.txt',
    'Makefile',
    'Dockerfile',
    'docker-compose.yml',
    'docker-compose.yaml',
    '.gitignore',
    '.editorconfig',
    'main.py',
    'app.py',
    'index.py',
    'index.js',
    'index.ts',
    'main.js',
    'main.ts',
    'app.js',
    'app.ts',
    'src/main.py',
    'src/main.js',
    'src/main.ts',
]

# LLM instructions header to prepend to output
LLM_INSTRUCTIONS = """--- INSTRUCTIONS ---
This file contains a complete dump of the repository's source code for LLM analysis.

Format:
- The file starts with a directory tree structure (if enabled)
- Each file is delimited with: ### File: <path>
- Code blocks use markdown syntax with language hints (e.g., ```python)
- Files are processed in alphabetical order within each directory

Usage tips:
- Reference files by their full relative path (e.g., "src/git_dump/core.py")
- The tree structure matches the actual files included in the dump
- Binary files, large files (>500KB), and ignored files are excluded
--- END INSTRUCTIONS ---

"""

# Language mapping for markdown code blocks
LANGUAGE_MAP: Dict[str, str] = {
    '.py': 'python',
    '.pyw': 'python',
    '.pyi': 'python',
    '.js': 'javascript',
    '.mjs': 'javascript',
    '.cjs': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.jsx': 'javascript',
    '.java': 'java',
    '.c': 'c',
    '.cpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.h': 'c',
    '.hpp': 'cpp',
    '.hxx': 'cpp',
    '.cs': 'csharp',
    '.go': 'go',
    '.rs': 'rust',
    '.rb': 'ruby',
    '.erb': 'erb',
    '.php': 'php',
    '.phtml': 'php',
    '.swift': 'swift',
    '.kt': 'kotlin',
    '.kts': 'kotlin',
    '.scala': 'scala',
    '.sc': 'scala',
    '.gradle': 'groovy',
    '.groovy': 'groovy',
    '.gvy': 'groovy',
    '.gy': 'groovy',
    '.gsh': 'groovy',
    '.sh': 'bash',
    '.bash': 'bash',
    '.zsh': 'bash',
    '.fish': 'fish',
    '.ps1': 'powershell',
    '.psm1': 'powershell',
    '.psd1': 'powershell',
    '.bat': 'batch',
    '.cmd': 'batch',
    '.html': 'html',
    '.htm': 'html',
    '.xhtml': 'html',
    '.css': 'css',
    '.scss': 'scss',
    '.sass': 'sass',
    '.less': 'less',
    '.styl': 'stylus',
    '.json': 'json',
    '.json5': 'json',
    '.jsonc': 'json',
    '.xml': 'xml',
    '.xsd': 'xml',
    '.xslt': 'xml',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.toml': 'toml',
    '.ini': 'ini',
    '.cfg': 'ini',
    '.conf': 'ini',
    '.config': 'ini',
    '.sql': 'sql',
    '.plsql': 'sql',
    '.mysql': 'sql',
    '.psql': 'sql',
    '.md': 'markdown',
    '.markdown': 'markdown',
    '.mdx': 'mdx',
    '.rst': 'rst',
    '.tex': 'latex',
    '.latex': 'latex',
    '.r': 'r',
    '.R': 'r',
    '.rmd': 'rmd',
    '.m': 'matlab',
    '.ml': 'ocaml',
    '.mli': 'ocaml',
    '.lua': 'lua',
    '.pl': 'perl',
    '.pm': 'perl',
    '.t': 'perl',
    '.hs': 'haskell',
    '.lhs': 'haskell',
    '.ex': 'elixir',
    '.exs': 'elixir',
    '.eex': 'eex',
    '.leex': 'eex',
    '.erl': 'erlang',
    '.hrl': 'erlang',
    '.clj': 'clojure',
    '.cljs': 'clojure',
    '.cljc': 'clojure',
    '.edn': 'clojure',
    '.vue': 'vue',
    '.svelte': 'svelte',
    '.dart': 'dart',
    '.cmake': 'cmake',
    '.make': 'makefile',
    '.makefile': 'makefile',
    'Makefile': 'makefile',
    '.dockerfile': 'dockerfile',
    'Dockerfile': 'dockerfile',
    '.tf': 'terraform',
    '.tfvars': 'terraform',
    '.graphql': 'graphql',
    '.gql': 'graphql',
    '.proto': 'protobuf',
    '.thrift': 'thrift',
    '.avsc': 'avro',
    '.jl': 'julia',
    '.vim': 'vim',
    '.exrc': 'vim',
    '.tcl': 'tcl',
    '.awk': 'awk',
    '.sed': 'sed',
    '.diff': 'diff',
    '.patch': 'diff',
    '.gitignore': 'gitignore',
    '.gitattributes': 'gitattributes',
    '.gitconfig': 'ini',
    '.editorconfig': 'ini',
    '.eslintrc': 'json',
    '.prettierrc': 'json',
    '.babelrc': 'json',
    '.tsconfig': 'json',
    '.jsconfig': 'json',
    '.ipynb': 'jupyter',
    '.sh-session': 'shell-session',
    '.console': 'shell-session',
}


def estimate_tokens(text: str) -> int:
    """
    Estimate token count using a 1:4 character-to-token ratio.

    Args:
        text: Input text to estimate tokens for

    Returns:
        Estimated number of tokens
    """
    return len(text) // 4


def get_tiktoken_token_count(text: str, encoding_name: str = "cl100k_base") -> int:
    """
    Get exact token count using tiktoken if available.

    Args:
        text: Input text to count tokens for
        encoding_name: Name of the encoding to use

    Returns:
        Exact number of tokens or estimated count if tiktoken unavailable
    """
    try:
        import tiktoken
        encoder = tiktoken.get_encoding(encoding_name)
        return len(encoder.encode(text))
    except ImportError:
        # Fallback to character-based estimation
        return estimate_tokens(text)


def get_language_from_path(path: str) -> str:
    """
    Get the language identifier for a file based on its extension.

    Args:
        path: File path

    Returns:
        Language identifier for markdown code blocks
    """
    path_obj = Path(path)
    name = path_obj.name
    ext = path_obj.suffix.lower()
    
    # Check full filename first for special cases like Dockerfile, Makefile
    if name in LANGUAGE_MAP:
        return LANGUAGE_MAP[name]
    
    # Then check extension
    return LANGUAGE_MAP.get(ext, '')


class RepoProcessor:
    def __init__(
        self,
        repo_path: str,
        output_file: str,
        ignore_patterns: Optional[List[str]] = None,
        include_patterns: Optional[List[str]] = None,
        use_gitignore: bool = True,
        start_delimiter: str = "### File: {path}\n```{lang}",
        end_delimiter: str = "```",
        verbose: bool = True,
        dry_run: bool = False,
        max_file_size: int = 512000,  # 500KB default
        include_tree: bool = True,
        count_tokens: bool = False,
        use_clipboard: bool = False,
        max_tokens: Optional[int] = None,
        clean_mode: bool = False,
        sort_priority: bool = True,
        git_branch: Optional[str] = None,
        git_commit: Optional[str] = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.output_file = Path(output_file).resolve()
        self.ignore_patterns = ignore_patterns or []
        self.include_patterns = include_patterns or []
        self.use_gitignore = use_gitignore
        self.start_delimiter = start_delimiter
        self.end_delimiter = end_delimiter
        self.verbose = verbose
        self.dry_run = dry_run
        self.max_file_size = max_file_size
        self.include_tree = include_tree
        self.count_tokens = count_tokens
        self.use_clipboard = use_clipboard
        self.max_tokens = max_tokens
        self.clean_mode = clean_mode
        self.sort_priority = sort_priority
        self.git_branch = git_branch
        self.git_commit = git_commit
        self.total_tokens = 0

        # Cache for nested .gitignore specs: maps directory path -> PathSpec
        self.gitignore_cache: Dict[str, Optional[pathspec.PathSpec]] = {}

        # Temporary directory for git worktree (if using git branch/commit)
        self._temp_worktree: Optional[Path] = None

        # Load all specs upfront
        self.spec = self._load_spec()

    def _load_spec(self):
        """Load pathspec with support for nested .gitignore files."""
        patterns = []

        # Load root .gitignore if it exists and gitignore is enabled
        if self.use_gitignore:
            gitignore_path = self.repo_path / ".gitignore"
            if gitignore_path.exists():
                try:
                    with open(gitignore_path, "r", encoding="utf-8") as f:
                        patterns.extend(f.readlines())
                except Exception as e:
                    if self.verbose:
                        logger.warning(f"Could not read root .gitignore: {e}")

        # Add user-specified ignore patterns
        patterns.extend(self.ignore_patterns)

        if pathspec and patterns:
            return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        return None

    def _load_nested_gitignore(self, directory: Path) -> Optional[pathspec.PathSpec]:
        """
        Load .gitignore spec for a specific directory (cached).

        Args:
            directory: Absolute path to the directory

        Returns:
            PathSpec for that directory's .gitignore, or None if not found/cached
        """
        if not self.use_gitignore:
            return None

        dir_str = str(directory)
        if dir_str in self.gitignore_cache:
            return self.gitignore_cache[dir_str]

        # Check if this directory has a .gitignore
        gitignore_path = directory / ".gitignore"
        if gitignore_path.exists():
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    patterns = f.readlines()
                if patterns:
                    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
                    self.gitignore_cache[dir_str] = spec
                    return spec
            except Exception as e:
                if self.verbose:
                    logger.warning(f"Could not read .gitignore in {directory}: {e}")

        # Cache None to avoid re-checking
        self.gitignore_cache[dir_str] = None
        return None

    def _matches_include(self, relative_path: str) -> bool:
        if not self.include_patterns:
            return True
        # Normalize path to use forward slashes for pattern matching
        normalized = relative_path.replace('\\', '/')
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(normalized, pattern):
                return True
        return False

    def is_ignored(self, relative_path: str, directory: Optional[Path] = None) -> bool:
        """
        Check if a file/directory should be ignored.

        Implements cumulative gitignore logic: checks all .gitignore files
        from the repo root down to the file's directory.

        Args:
            relative_path: Path relative to repo root (using forward slashes)
            directory: Absolute path to the parent directory (for nested gitignore lookup)

        Returns:
            True if the path should be ignored
        """
        # Normalize path to use forward slashes
        normalized = relative_path.replace('\\', '/')
        parts = normalized.split('/')

        # Check against default junk directories (fail-safe even without .gitignore)
        if any(part in DEFAULT_JUNK_DIRS for part in parts):
            return True

        # Always ignore .git directory
        if '.git' in parts:
            return True

        # Ignore the output file if it's within the repo path
        abs_path = self.repo_path / relative_path
        if abs_path == self.output_file:
            return True

        # Check root-level spec and custom ignore patterns
        if self.spec and self.spec.match_file(normalized):
            return True

        # Check default ignore patterns (fail-safe)
        if self.use_gitignore and pathspec:
            default_spec = pathspec.PathSpec.from_lines("gitwildmatch", DEFAULT_IGNORE_PATTERNS)
            if default_spec.match_file(normalized):
                return True

        # Cumulative nested check: Check every .gitignore from root to the file
        if self.use_gitignore and pathspec:
            # Check each parent directory for a .gitignore
            path_parts = normalized.split('/')
            for i in range(len(path_parts) - 1):  # -1 to exclude the file itself
                # Build the parent directory path
                parent_rel = '/'.join(path_parts[:i+1])
                abs_parent = self.repo_path / parent_rel

                nested_spec = self._load_nested_gitignore(abs_parent)
                if nested_spec:
                    # Match relative to the directory where .gitignore lives
                    rel_to_gitignore = '/'.join(path_parts[i+1:])
                    if nested_spec.match_file(rel_to_gitignore):
                        return True

        return False

    def _is_binary(self, file_path: Path) -> bool:
        """Check if a file is binary by extension first, then by content."""
        # First check extension - fast path for obvious binary files
        if file_path.suffix.lower() in BINARY_EXTENSIONS:
            return True

        try:
            with open(file_path, "rb") as f:
                # Read first 8KB to check for binary content
                chunk = f.read(8192)
                # Check for null bytes (common in binary files)
                if b"\0" in chunk:
                    return True
                # Try to decode as text with multiple encodings
                # If any succeed, it's likely a text file
                for enc in ['utf-8', 'utf-8-sig', 'latin-1']:
                    try:
                        chunk.decode(enc)
                        return False  # Successfully decoded, it's text
                    except UnicodeDecodeError:
                        continue
                # All encodings failed, likely binary
                return True
        except Exception:
            return True

    def _read_file_safely(self, file_path: Path) -> str:
        """
        Read a file with robust encoding fallback.

        Tries multiple encodings in order: utf-8-sig (handles BOM), utf-8, then latin-1.
        Latin-1 never fails as it maps all byte values to characters.

        Args:
            file_path: Path to the file to read

        Returns:
            File content as string, or error message if unreadable
        """
        encodings = ['utf-8-sig', 'utf-8', 'latin-1']

        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
            except (OSError, PermissionError) as e:
                return f"[Error: Could not read file - {e}]"

        return "[Error: Could not decode file with any supported encoding]"

    def _clean_content(self, content: str) -> str:
        """
        Clean content to reduce token count.

        Removes:
        - Excessive blank lines (more than 2 consecutive)
        - Leading/trailing whitespace on each line
        - Single-line comments (for supported languages)
        - Multi-line comments (for supported languages)

        Args:
            content: Original file content

        Returns:
            Cleaned content with reduced token count
        """
        if not self.clean_mode:
            return content

        lines = content.splitlines()
        cleaned_lines = []
        in_multiline_comment = False
        prev_blank_count = 0

        # Detect language from file extension patterns in content
        # This is a simple heuristic - could be improved
        is_python = any(line.strip().startswith('#') for line in lines[:10])
        is_js_ts = any(
            'function' in line or 'const ' in line or 'let ' in line or 'import ' in line
            for line in lines[:10]
        )
        is_c_style = any(
            'void ' in line or 'int ' in line or '#include' in line
            for line in lines[:10]
        )

        for line in lines:
            # Handle multi-line comments
            if in_multiline_comment:
                if '*/' in line:
                    in_multiline_comment = False
                    line = line[line.index('*/') + 2:]
                else:
                    continue

            # Remove single-line comments based on language
            if is_python:
                # Python: remove # comments but keep shebangs and encoding declarations
                if '#' in line and not line.strip().startswith('#!') and not 'coding:' in line:
                    # Only remove if # is not in a string (simple heuristic)
                    if "'" not in line and '"' not in line:
                        line = line.split('#')[0]
                # Python docstrings are harder to detect - skip for now
            elif is_js_ts or is_c_style:
                # Remove // comments
                if '//' in line:
                    # Check it's not in a string (simple heuristic)
                    before_slash = line.split('//')[0]
                    if before_slash.count('"') % 2 == 0 and before_slash.count("'") % 2 == 0:
                        line = before_slash

                # Check for /* start of multi-line comment
                if '/*' in line:
                    if '*/' in line:
                        # Single-line block comment
                        line = re.sub(r'/\*.*?\*/', '', line)
                    else:
                        # Start of multi-line comment
                        line = line[:line.index('/*')]
                        in_multiline_comment = True

            # Strip leading/trailing whitespace
            line = line.rstrip()

            # Track consecutive blank lines
            if not line.strip():
                prev_blank_count += 1
                if prev_blank_count <= 2:  # Allow up to 2 consecutive blank lines
                    cleaned_lines.append(line)
            else:
                prev_blank_count = 0
                cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _setup_git_worktree(self) -> Optional[Path]:
        """
        Set up a git worktree for a specific branch or commit.

        Returns:
            Path to the worktree directory, or None if not using git
        """
        if not self.git_branch and not self.git_commit:
            return None

        # Check if repo_path is a git repository
        git_dir = self.repo_path / '.git'
        if not git_dir.exists():
            logger.warning("Not a git repository. Ignoring --branch/--commit options.")
            return None

        try:
            # Verify git is available
            result = subprocess.run(
                ['git', '--version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                logger.warning("Git not available. Ignoring --branch/--commit options.")
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Git not available. Ignoring --branch/--commit options.")
            return None

        # Create a temporary worktree
        self._temp_worktree = Path(tempfile.mkdtemp(prefix='git_dump_worktree_'))

        try:
            if self.git_branch:
                # Create worktree for branch
                cmd = [
                    'git', '-C', str(self.repo_path),
                    'worktree', 'add', '-f', str(self._temp_worktree),
                    self.git_branch
                ]
            else:
                # Create detached worktree for commit
                cmd = [
                    'git', '-C', str(self.repo_path),
                    'worktree', 'add', '-f', '--detach', str(self._temp_worktree),
                    self.git_commit
                ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                logger.error(f"Failed to create git worktree: {result.stderr}")
                shutil.rmtree(self._temp_worktree, ignore_errors=True)
                self._temp_worktree = None
                return None

            # Update repo_path to point to worktree
            original_repo_path = self.repo_path
            self.repo_path = self._temp_worktree

            if self.verbose:
                ref = self.git_branch or self.git_commit
                logger.info(f"Created git worktree at {self._temp_worktree} for {ref}")

            # Reload specs for the new repo_path
            self.spec = self._load_spec()
            self.gitignore_cache.clear()

            return self._temp_worktree

        except Exception as e:
            logger.error(f"Error setting up git worktree: {e}")
            if self._temp_worktree and self._temp_worktree.exists():
                shutil.rmtree(self._temp_worktree, ignore_errors=True)
            self._temp_worktree = None
            return None

    def _cleanup_git_worktree(self):
        """Clean up the temporary git worktree."""
        if self._temp_worktree and self._temp_worktree.exists():
            try:
                # Remove the worktree
                subprocess.run(
                    ['git', '-C', str(self.repo_path.parent), 'worktree', 'remove',
                     '-f', str(self._temp_worktree)],
                    capture_output=True,
                    timeout=30
                )
            except Exception:
                # Fallback to shutil if git remove fails
                pass

            shutil.rmtree(self._temp_worktree, ignore_errors=True)
            self._temp_worktree = None

            if self.verbose:
                logger.info("Cleaned up git worktree")

    def _sort_files(self, files: List[str], rel_dir: str) -> List[str]:
        """
        Sort files with priority files first.

        Args:
            files: List of filenames in the current directory
            rel_dir: Relative directory path (for constructing full relative paths)

        Returns:
            Sorted list of files
        """
        if not self.sort_priority:
            return sorted(files, key=str.lower)

        def get_priority(file: str) -> Tuple[int, str]:
            # Construct the relative path for priority checking
            rel_path = os.path.join(rel_dir, file) if rel_dir else file
            # Normalize to forward slashes
            rel_path_normalized = rel_path.replace('\\', '/')

            # Check if file or path is in priority list
            if rel_path_normalized in PRIORITY_FILES:
                return (0, PRIORITY_FILES.index(rel_path_normalized))
            if file in PRIORITY_FILES:
                return (0, PRIORITY_FILES.index(file))

            # Non-priority files come after, sorted alphabetically
            return (1, file.lower())

        return sorted(files, key=get_priority)

    def _should_include_in_tree(self, item_path: Path) -> bool:
        """
        Check if a file or directory should appear in the tree.

        For files, also checks binary and size limits to ensure tree parity
        with the actual dump content.

        Args:
            item_path: Absolute path to the item

        Returns:
            True if the item should appear in the tree
        """
        rel_path = item_path.relative_to(self.repo_path).as_posix()

        # First check if ignored
        if self.is_ignored(rel_path, item_path.parent):
            return False

        # For files, also check binary and size for perfect parity with dump
        if item_path.is_file():
            try:
                if item_path.stat().st_size > self.max_file_size:
                    return False
                if self._is_binary(item_path):
                    return False
            except (OSError, PermissionError):
                return False

        return True

    def generate_tree_structure(self) -> str:
        """
        Generate a text-based directory tree structure using the same ignore logic.
        Uses proper tree characters (└── for last item, ├── for others).

        Returns:
            String representation of the directory tree
        """
        tree_lines = ["--- REPOSITORY STRUCTURE ---"]

        def _build_tree(current_path: Path, prefix: str = ""):
            try:
                # Get all entries sorted with dirs last
                entries = sorted(
                    list(current_path.iterdir()),
                    key=lambda x: (not x.is_dir(), x.name.lower())
                )

                # Pre-filter entries to only show what will actually be processed
                valid_entries = []
                for entry in entries:
                    if self._should_include_in_tree(entry):
                        valid_entries.append(entry)

                for i, entry in enumerate(valid_entries):
                    is_last = i == len(valid_entries) - 1
                    char = "└── " if is_last else "├── "

                    if entry.is_dir():
                        tree_lines.append(f"{prefix}{char}{entry.name}/")
                        new_prefix = prefix + ("    " if is_last else "│   ")
                        _build_tree(entry, new_prefix)
                    else:
                        tree_lines.append(f"{prefix}{char}{entry.name}")
            except PermissionError:
                tree_lines.append(f"{prefix}└── [Permission Denied]")

        # Start with the root directory
        root_path = self.repo_path
        tree_lines.append(f"{root_path.name}/")

        _build_tree(root_path)
        tree_lines.append("--- END REPOSITORY STRUCTURE ---\n")
        return "\n".join(tree_lines)

    def process(self) -> int:
        processed_count = 0
        output_content = []

        # Set up git worktree if branch/commit specified
        self._setup_git_worktree()

        if self.dry_run:
            if self.verbose:
                logger.info("Dry run mode: No files will be written.")

        try:
            if self.dry_run:
                outfile = None
            else:
                outfile = open(self.output_file, "w", encoding="utf-8", errors='replace')

            try:
                # Write LLM instructions header first
                if not self.dry_run:
                    outfile.write(LLM_INSTRUCTIONS)
                    if self.count_tokens:
                        self.total_tokens += get_tiktoken_token_count(LLM_INSTRUCTIONS)

                # Write repository structure tree if requested
                if self.include_tree and not self.dry_run:
                    tree_structure = self.generate_tree_structure()
                    outfile.write(tree_structure)
                    if self.count_tokens:
                        self.total_tokens += get_tiktoken_token_count(tree_structure)

                # Walk through the repository
                for root, dirs, files in os.walk(self.repo_path):
                    rel_dir = os.path.relpath(root, self.repo_path)
                    if rel_dir == ".":
                        rel_dir = ""

                    # Filter directories in-place (performance optimization)
                    dirs_to_remove = []
                    for d in dirs:
                        rel_d = os.path.join(rel_dir, d) if rel_dir else d
                        if self.is_ignored(rel_d, Path(root)):
                            dirs_to_remove.append(d)
                    for d in dirs_to_remove:
                        dirs.remove(d)

                    # Sort files with priority files first
                    files = self._sort_files(files, rel_dir)

                    for filename in files:
                        rel_file = os.path.join(rel_dir, filename) if rel_dir else filename

                        if self.is_ignored(rel_file, Path(root)):
                            continue

                        if not self._matches_include(rel_file):
                            continue

                        file_path = Path(root) / filename

                        # Check file size
                        try:
                            file_size = file_path.stat().st_size
                            if file_size > self.max_file_size:
                                if self.verbose:
                                    logger.warning(f"Skipping {rel_file} - exceeds max size ({file_size} > {self.max_file_size})")
                                continue
                        except OSError:
                            if self.verbose:
                                logger.warning(f"Could not get size for {rel_file}, skipping")
                            continue

                        try:
                            if self._is_binary(file_path):
                                continue

                            if self.dry_run:
                                if self.verbose:
                                    logger.info(f"Would process: {rel_file}")
                                processed_count += 1
                                continue

                            # Build delimiter with language hint for markdown
                            lang = get_language_from_path(rel_file)
                            start_header = self.start_delimiter.format(path=rel_file, lang=lang)
                            end_footer = self.end_delimiter.format(path=rel_file, lang=lang)

                            # Write start delimiter and count tokens
                            outfile.write(start_header + "\n")
                            if self.count_tokens:
                                self.total_tokens += get_tiktoken_token_count(start_header + "\n")

                            # Read file content with robust encoding fallback
                            content = self._read_file_safely(file_path)
                            if content.startswith("[Error:"):
                                if self.verbose:
                                    logger.warning(f"Skipping '{rel_file}' - {content}")
                                continue

                            # Clean content if requested
                            if self.clean_mode:
                                content = self._clean_content(content)

                            # Check max_tokens before writing content
                            if self.max_tokens:
                                content_tokens = get_tiktoken_token_count(content)
                                if self.total_tokens + content_tokens > self.max_tokens:
                                    logger.warning(f"Token limit reached. Stopping dump at '{rel_file}'.")
                                    # Write end delimiter for incomplete file
                                    outfile.write("\n")
                                    outfile.write(end_footer + "\n")
                                    if self.count_tokens:
                                        self.total_tokens += get_tiktoken_token_count(end_footer + "\n")
                                    processed_count += 1
                                    break

                            # STREAM file content to output (memory efficient for large files)
                            # Write in chunks to avoid loading entire file into memory for token counting
                            chunk_size = 8192
                            for i in range(0, len(content), chunk_size):
                                chunk = content[i:i + chunk_size]
                                outfile.write(chunk)
                                if self.count_tokens:
                                    self.total_tokens += get_tiktoken_token_count(chunk)

                                # Check token limit during streaming
                                if self.max_tokens and self.total_tokens >= self.max_tokens:
                                    logger.warning(f"Token limit reached mid-file at '{rel_file}'.")
                                    break

                            # Ensure file ends with newline before end delimiter
                            outfile.write("\n")
                            outfile.write(end_footer + "\n")
                            if self.count_tokens:
                                self.total_tokens += get_tiktoken_token_count(end_footer + "\n")

                            processed_count += 1

                            # Final check after file
                            if self.max_tokens and self.total_tokens >= self.max_tokens:
                                break

                        except PermissionError as e:
                            if self.verbose:
                                logger.warning(f"Skipping '{rel_file}' - Permission denied: {e}")
                        except Exception as e:
                            if self.verbose:
                                logger.error(f"Error processing '{rel_file}': {e}")

                    # Check if we hit token limit and need to stop processing directories
                    if self.max_tokens and self.total_tokens >= self.max_tokens:
                        break

            finally:
                if outfile:
                    outfile.close()

                # Copy to clipboard if requested
                if not self.dry_run and self.use_clipboard and os.path.exists(self.output_file):
                    try:
                        import pyperclip
                        with open(self.output_file, "r", encoding="utf-8", errors='replace') as f:
                            content = f.read()
                        pyperclip.copy(content)
                        if self.verbose:
                            logger.info("Output copied to clipboard.")
                    except ImportError:
                        if self.verbose:
                            logger.warning("pyperclip not installed. Install with: pip install pyperclip")
                    except Exception as e:
                        if self.verbose:
                            logger.warning(f"Could not copy to clipboard: {e}")

                # Clean up git worktree if created
                self._cleanup_git_worktree()

        except Exception as e:
            logger.error(f"Fatal error: {e}")
            # Clean up git worktree on fatal error
            self._cleanup_git_worktree()
            sys.exit(1)

        return processed_count
