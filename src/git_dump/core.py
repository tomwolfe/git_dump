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

# Magic number signatures for common binary formats
# These are the first few bytes of files that indicate binary content
MAGIC_NUMBERS: Dict[bytes, str] = {
    b'\x89PNG': 'PNG image',
    b'\xff\xd8\xff': 'JPEG image',
    b'GIF87a': 'GIF image',
    b'GIF89a': 'GIF image',
    b'\x89HDF': 'HDF5 file',
    b'\x80HDF': 'HDF5 file (big endian)',
    b'PK\x03\x04': 'ZIP archive',
    b'PK\x05\x06': 'ZIP archive (empty)',
    b'PK\x07\x08': 'ZIP archive (spanned)',
    b'\x1f\x8b': 'GZIP compressed',
    b'BZh': 'BZIP2 compressed',
    b'\xfd7zXZ\x00': 'XZ compressed',
    b'\x04\x22\x4d\x18': 'LZ4 compressed',
    b'\x28\xb5\x2f\xfd': 'ZSTD compressed',
    b'%PDF': 'PDF document',
    b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1': 'Microsoft Office (OLE)',
    b'\x50\x4b\x03\x04': 'Office Open XML (docx, xlsx, pptx)',
    b'\x00\x00\x00': 'Binary data (null bytes)',
    b'\x7fELF': 'ELF executable',
    b'MZ': 'DOS/Windows executable',
    b'\xca\xfe\xba\xbe': 'Java class / Mach-O fat binary',
    b'\xcf\xfa\xed\xfe': 'Mach-O binary (little endian)',
    b'\xfe\xed\xfa\xcf': 'Mach-O binary (big endian)',
    b'\x52\x49\x46\x46': 'RIFF format (WAV, AVI, WEBP)',
    b'OggS': 'OGG Vorbis',
    b'fLaC': 'FLAC audio',
    b'\x1a\x45\xdf\xa3': 'Matroska (MKV)',
    b'SQLite format 3\x00': 'SQLite database',
    b'\x00\x00\x00\x01': 'Apple resource fork',
}

# Magic number prefixes to check (sorted by length for efficient checking)
MAGIC_PREFIXES = sorted(MAGIC_NUMBERS.keys(), key=len, reverse=True)

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
- Each file is delimited with XML tags: <file path="..."><![CDATA[...code...]]></file>
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
        start_delimiter: Optional[str] = None,  # None = use XML format by default
        end_delimiter: Optional[str] = None,
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
        use_xml_format: bool = True,  # New: Use XML-style delimiters
        use_git_ls_files: bool = False,  # New: Use git ls-files for faster traversal
        skeleton_mode: bool = False,  # New: Use tree-sitter for skeleton extraction
        skeleton_threshold: int = 1000,  # Token threshold for skeleton mode
        config_file: Optional[str] = None,  # New: Path to config file
    ):
        self.repo_path = Path(repo_path).resolve()
        self.output_file = Path(output_file).resolve()
        self.ignore_patterns = ignore_patterns or []
        self.include_patterns = include_patterns or []
        self.use_gitignore = use_gitignore
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
        self.use_xml_format = use_xml_format
        self.use_git_ls_files = use_git_ls_files
        self.skeleton_mode = skeleton_mode
        self.skeleton_threshold = skeleton_threshold
        self.config_file = config_file
        self.total_tokens = 0

        # Set delimiters based on format choice
        if use_xml_format:
            self.start_delimiter = "<file path=\"{path}\"><![CDATA["
            self.end_delimiter = "]]></file>"
        else:
            self.start_delimiter = start_delimiter or "### File: {path}\n```{lang}"
            self.end_delimiter = end_delimiter or "```"

        # Cache for nested .gitignore specs: maps directory path -> PathSpec
        self.gitignore_cache: Dict[str, Optional[pathspec.PathSpec]] = {}

        # Temporary directory for git worktree (if using git branch/commit)
        self._temp_worktree: Optional[Path] = None

        # Cache for git ls-files results
        self._git_files_cache: Optional[List[str]] = None

        # Load all specs upfront
        self.spec = self._load_spec()

    def _load_spec(self):
        """Load pathspec with support for nested .gitignore files."""
        patterns = []

        # Load config file if specified
        if self.config_file:
            config_path = Path(self.config_file)
            if config_path.exists():
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib

                with open(config_path, "rb") as f:
                    config = tomllib.load(f)

                # Load ignore patterns from config
                if 'ignore' in config:
                    patterns.extend(config['ignore'])
                    # Also add to self.ignore_patterns for visibility
                    self.ignore_patterns.extend(config['ignore'])

                # Load other config options if not already set
                if self.include_patterns is None and 'include' in config:
                    self.include_patterns = config['include']

                if self.max_tokens is None and 'max_tokens' in config:
                    self.max_tokens = config['max_tokens']

                if self.max_file_size == 512000 and 'max_file_size' in config:
                    self.max_file_size = config['max_file_size']

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

    def _get_git_files(self) -> List[str]:
        """
        Get list of tracked files using git ls-files (faster than os.walk).
        
        Returns:
            List of relative file paths tracked by git
        """
        if self._git_files_cache is not None:
            return self._git_files_cache
        
        # Check if this is a git repository
        git_dir = self.repo_path / '.git'
        if not git_dir.exists():
            return []
        
        try:
            # Verify git is available
            result = subprocess.run(
                ['git', '--version'],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.repo_path)
            )
            if result.returncode != 0:
                return []
            
            # Get all tracked files
            result = subprocess.run(
                ['git', 'ls-files'],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self.repo_path)
            )
            
            if result.returncode != 0:
                return []
            
            # Cache and return
            self._git_files_cache = result.stdout.strip().split('\n') if result.stdout.strip() else []
            return self._git_files_cache
            
        except (subprocess.TimeoutExpired, Exception) as e:
            if self.verbose:
                logger.warning(f"git ls-files failed: {e}, falling back to os.walk")
            return []

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
        """Check if a file is binary by extension first, then by magic numbers, then by content."""
        # First check extension - fast path for obvious binary files
        if file_path.suffix.lower() in BINARY_EXTENSIONS:
            return True

        try:
            with open(file_path, "rb") as f:
                # Read first 16 bytes for magic number check
                header = f.read(16)
                
                if not header:  # Empty file
                    return False
                
                # Check magic numbers
                for magic in MAGIC_PREFIXES:
                    if header.startswith(magic):
                        if self.verbose:
                            logger.debug(f"Binary detected by magic number: {MAGIC_NUMBERS[magic]}")
                        return True
                
                # Check for null bytes in header
                if b"\0" in header:
                    return True
                
                # Read more content for encoding check
                f.seek(0)
                chunk = f.read(8192)
                
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

    def _clean_content(self, content: str, lang: str = '') -> str:
        """
        Clean content to reduce token count using language-aware rules.

        Removes:
        - Excessive blank lines (more than 2 consecutive)
        - Leading/trailing whitespace on each line
        - Single-line comments (for supported languages)
        - Multi-line comments (for supported languages)

        Args:
            content: Original file content
            lang: Language identifier (e.g., 'python', 'javascript')

        Returns:
            Cleaned content with reduced token count
        """
        if not self.clean_mode:
            # But still apply skeleton mode if enabled and content is large
            if self.skeleton_mode:
                content_tokens = estimate_tokens(content)
                if content_tokens > self.skeleton_threshold:
                    return self._extract_skeleton(content, lang)
            return content

        lines = content.splitlines()
        cleaned_lines = []
        in_multiline_comment = False
        prev_blank_count = 0

        # Detect language from extension or content heuristics
        if not lang:
            is_python = any(line.strip().startswith('#') for line in lines[:10])
            is_js_ts = any(
                'function' in line or 'const ' in line or 'let ' in line or 'import ' in line
                for line in lines[:10]
            )
            is_c_style = any(
                'void ' in line or 'int ' in line or '#include' in line
                for line in lines[:10]
            )
        else:
            is_python = lang == 'python'
            is_js_ts = lang in ('javascript', 'typescript')
            is_c_style = lang in ('c', 'cpp', 'java', 'go', 'rust')

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
                if '#' in line and not line.strip().startswith('#!') and 'coding:' not in line:
                    # Use state machine to check if # is in a string
                    line = self._remove_python_comment(line)
            elif is_js_ts or is_c_style:
                # Remove // comments with string-aware logic
                if '//' in line:
                    line = self._remove_cpp_style_comment(line)

                # Check for /* start of multi-line comment
                if '/*' in line:
                    if '*/' in line:
                        # Single-line block comment - use regex to remove
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

        result = '\n'.join(cleaned_lines)
        
        # Apply skeleton mode if enabled and content is still large
        if self.skeleton_mode:
            result_tokens = estimate_tokens(result)
            if result_tokens > self.skeleton_threshold:
                result = self._extract_skeleton(result, lang)
        
        return result

    def _remove_python_comment(self, line: str) -> str:
        """
        Remove Python # comment safely, respecting strings.

        Uses a simple state machine to track whether we're inside a string.
        """
        in_single = False
        in_double = False
        in_triple_single = False
        in_triple_double = False
        i = 0

        while i < len(line):
            # Check for triple quotes first
            if i <= len(line) - 3:
                triple_single = line[i:i+3]
                triple_double = line[i:i+3]
                if triple_single == "'''" and not in_double and not in_triple_double:
                    in_triple_single = not in_triple_single
                    i += 3
                    continue
                if triple_double == '"""' and not in_single and not in_triple_single:
                    in_triple_double = not in_triple_double
                    i += 3
                    continue

            # Check for escape sequences
            if i < len(line) - 1 and line[i] == '\\':
                i += 2  # Skip escaped character
                continue

            # Track string delimiters (only if not in triple-quoted string)
            if not in_triple_single and not in_triple_double:
                if line[i] == "'" and not in_double:
                    in_single = not in_single
                elif line[i] == '"' and not in_single:
                    in_double = not in_double

            # Check for comment (only if not in any string)
            if line[i] == '#' and not in_single and not in_double and not in_triple_single and not in_triple_double:
                return line[:i]

            i += 1

        return line

    def _remove_cpp_style_comment(self, line: str) -> str:
        """
        Remove C++/JS // comment safely, respecting strings.

        Uses a simple state machine to track whether we're inside a string.
        """
        in_single = False
        in_double = False
        in_template = False
        i = 0

        while i < len(line):
            # Check for escape sequences
            if i < len(line) - 1 and line[i] == '\\':
                i += 2  # Skip escaped character
                continue

            # Track string delimiters
            if line[i] == "'" and not in_double and not in_template:
                in_single = not in_single
            elif line[i] == '"' and not in_single and not in_template:
                in_double = not in_double
            elif line[i] == '`' and not in_single and not in_double:
                in_template = not in_template  # Template literals in JS

            # Check for // comment (only if not in any string)
            if (i < len(line) - 1 and line[i:i+2] == '//' and
                not in_single and not in_double and not in_template):
                return line[:i]

            i += 1

        return line

    def _extract_skeleton(self, content: str, lang: str) -> str:
        """
        Extract skeleton (function/class signatures) from code using tree-sitter.
        
        Falls back to regex-based extraction if tree-sitter is not available.
        
        Args:
            content: Original source code
            lang: Language identifier
            
        Returns:
            Skeletonized version with function bodies removed
        """
        if not self.skeleton_mode:
            return content
        
        # Try tree-sitter first
        try:
            import tree_sitter
            from tree_sitter import Language
            
            # Map language to tree-sitter grammar
            lang_map = {
                'python': ('tree-sitter-python', 'python.so'),
                'javascript': ('tree-sitter-javascript', 'javascript.so'),
                'typescript': ('tree-sitter-typescript', 'typescript.so'),
            }
            
            if lang not in lang_map:
                return self._extract_skeleton_regex(content, lang)
            
            # Try to load the language grammar
            try:
                ts_lang = Language(lang_map[lang][1], lang_map[lang][0].replace('tree-sitter-', ''))
                parser = tree_sitter.Parser()
                parser.set_language(ts_lang)
                
                # Parse the code
                tree = parser.parse(content.encode())
                
                # Extract skeleton based on language
                if lang == 'python':
                    return self._extract_python_skeleton(tree, content)
                elif lang in ('javascript', 'typescript'):
                    return self._extract_js_skeleton(tree, content)
                    
            except Exception:
                # Fall back to regex if tree-sitter fails
                pass
                
        except ImportError:
            pass
        
        # Fallback to regex-based extraction
        return self._extract_skeleton_regex(content, lang)

    def _extract_python_skeleton(self, tree, content: str) -> str:
        """Extract Python skeleton using tree-sitter AST."""
        lines = content.splitlines(keepends=True)
        result_lines = []
        
        def walk(node):
            # Keep class and function definitions, but strip bodies
            if node.type in ('function_definition', 'class_definition'):
                # Get the header line (def/class line)
                start_point = node.start_point
                end_point = node.end_point
                
                # Find the colon and keep just the signature
                for line_num in range(start_point[0], min(end_point[0] + 1, len(lines))):
                    line = lines[line_num]
                    result_lines.append(line.rstrip())
                    
                    # If this is the first line and contains the signature, add pass statement
                    if line_num == start_point[0]:
                        if ':' in line:
                            result_lines.append('    pass\n')
                        return
                    
            elif node.type == 'module':
                # Process children
                for child in node.children:
                    walk(child)
            else:
                # Keep other top-level statements (imports, constants, etc.)
                if node.start_point[0] == node.end_point[0]:  # Single line
                    line = lines[node.start_point[0]]
                    if line.strip() and not line.strip().startswith('#'):
                        result_lines.append(line)
        
        walk(tree.root_node)
        return ''.join(result_lines) if result_lines else content

    def _extract_js_skeleton(self, tree, content: str) -> str:
        """Extract JavaScript/TypeScript skeleton using tree-sitter AST."""
        lines = content.splitlines(keepends=True)
        result_lines = []
        
        def walk(node):
            # Keep class and function declarations
            if node.type in ('function_declaration', 'class_declaration', 
                            'method_definition', 'arrow_function'):
                # Get just the signature
                start_point = node.start_point
                line = lines[start_point[0]]
                result_lines.append(line.rstrip())
                
                # Add stub body
                if '{' in line:
                    result_lines.append('    {}\n')
                return
                    
            elif node.type in ('program', 'lexical_declaration', 'variable_declaration'):
                # Process children or keep declarations
                has_children = False
                for child in node.children:
                    if child.type not in (';', '{', '}', '='):
                        walk(child)
                        has_children = True
                
                if not has_children and node.start_point[0] == node.end_point[0]:
                    line = lines[node.start_point[0]]
                    if line.strip() and not line.strip().startswith('//'):
                        result_lines.append(line)
            else:
                # Keep other top-level statements
                if node.start_point[0] == node.end_point[0]:
                    line = lines[node.start_point[0]]
                    if line.strip() and not line.strip().startswith('//'):
                        result_lines.append(line)
        
        walk(tree.root_node)
        return ''.join(result_lines) if result_lines else content

    def _extract_skeleton_regex(self, content: str, lang: str) -> str:
        """
        Extract skeleton using regex patterns (fallback when tree-sitter unavailable).
        
        Args:
            content: Original source code
            lang: Language identifier
            
        Returns:
            Skeletonized version with function bodies replaced by pass/{}
        """
        lines = content.splitlines()
        result_lines = []
        skip_until_dedent = False
        brace_depth = 0
        
        if lang == 'python':
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                
                # Keep function/class definitions
                if stripped.startswith(('def ', 'class ', 'async def ')):
                    result_lines.append(line.rstrip())
                    result_lines.append('    pass')
                    skip_until_dedent = True
                    continue
                
                # Keep imports and module-level statements
                if stripped.startswith(('import ', 'from ')) or (not stripped and result_lines):
                    result_lines.append(line.rstrip())
                    continue
                
                # Skip indented lines (function bodies)
                if skip_until_dedent:
                    if stripped and not line[0].isspace():
                        skip_until_dedent = False
                        result_lines.append(line.rstrip())
                elif not line[0].isspace() if line else True:
                    result_lines.append(line.rstrip())
                    
        elif lang in ('javascript', 'typescript', 'java', 'cpp', 'c', 'go', 'rust'):
            for line in lines:
                stripped = line.strip()
                
                # Keep function/class declarations
                if any(kw in stripped for kw in ['function ', 'class ', 'interface ', 'struct ', 'impl ']):
                    result_lines.append(line.rstrip())
                    if '{' not in line:
                        result_lines.append('{}')
                    continue
                
                # Track brace depth for C-style languages
                brace_depth += line.count('{') - line.count('}')
                
                # Keep lines at top level (brace_depth == 0) or declarations
                if brace_depth <= 1:
                    result_lines.append(line.rstrip())
        else:
            # Unknown language - return original
            return content
        
        return '\n'.join(result_lines)

    def _clean_content_chunk(self, chunk: str) -> str:
        """
        Clean a chunk of content (for streaming mode).

        This is a simplified version of _clean_content that works on chunks.
        Note: Multi-line comments may not be perfectly handled across chunk boundaries.

        Args:
            chunk: A chunk of file content

        Returns:
            Cleaned chunk
        """
        if not self.clean_mode:
            return chunk

        # For chunked cleaning, we use line-by-line processing
        # Multi-line comments spanning chunks will be partially cleaned
        lines = chunk.splitlines(keepends=True)
        cleaned_lines = []
        prev_blank_count = 0

        for line in lines:
            # Strip trailing whitespace
            line = line.rstrip()

            # Track consecutive blank lines
            if not line.strip():
                prev_blank_count += 1
                if prev_blank_count <= 2:
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

    def _is_valid_file(self, file_path: Path) -> bool:
        """
        Check if a file should be included in the dump.

        Checks:
        - Not ignored by gitignore/rules
        - Not binary
        - Within size limit
        - Matches include patterns

        Args:
            file_path: Absolute path to the file

        Returns:
            True if the file should be included
        """
        rel_path = file_path.relative_to(self.repo_path).as_posix()

        # Check ignore rules
        if self.is_ignored(rel_path, file_path.parent):
            return False

        # Check include patterns
        if not self._matches_include(rel_path):
            return False

        # Check size
        try:
            if file_path.stat().st_size > self.max_file_size:
                return False
        except (OSError, PermissionError):
            return False

        # Check binary
        try:
            if self._is_binary(file_path):
                return False
        except (OSError, PermissionError):
            return False

        return True

    def get_valid_files(self) -> Generator[Tuple[Path, str], None, None]:
        """
        Generator that yields all valid files to process.

        This is the single source of truth for file selection, used by both
        the tree generator and the dump processor to ensure perfect parity.

        Yields:
            Tuples of (absolute_path, relative_path_str)
        """
        # Use git ls-files if enabled and available
        if self.use_git_ls_files:
            git_files = self._get_git_files()
            if git_files:
                for rel_file in git_files:
                    if not rel_file:  # Skip empty strings
                        continue
                    file_path = self.repo_path / rel_file
                    if file_path.exists() and self._is_valid_file(file_path):
                        yield (file_path, rel_file)
                return
        
        # Fallback to os.walk
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
            dir_files = self._sort_files(files, rel_dir)

            for filename in dir_files:
                rel_file = os.path.join(rel_dir, filename) if rel_dir else filename
                file_path = Path(root) / filename

                if self._is_valid_file(file_path):
                    yield (file_path, rel_file)

    def _should_include_in_tree(self, item_path: Path) -> bool:
        """
        Check if a file or directory should appear in the tree.

        For files, uses _is_valid_file for perfect parity with dump.

        Args:
            item_path: Absolute path to the item

        Returns:
            True if the item should appear in the tree
        """
        if item_path.is_file():
            return self._is_valid_file(item_path)

        # For directories, check if they contain any valid files
        rel_path = item_path.relative_to(self.repo_path).as_posix()
        if self.is_ignored(rel_path, item_path.parent):
            return False

        # Check if directory has any valid files (recursively)
        try:
            for entry in item_path.iterdir():
                if entry.is_file():
                    if self._is_valid_file(entry):
                        return True
                elif entry.is_dir():
                    if self._should_include_in_tree(entry):
                        return True
        except (PermissionError, OSError):
            return False

        return False

    def _calculate_token_budget(self, files: List[Tuple[Path, str]]) -> Dict[str, any]:
        """
        Calculate token budget and determine processing strategy for each file.
        
        Implements multi-pass approach:
        1. Calculate tokens for all files
        2. If total > max_tokens, mark non-priority files for cleaning
        3. If still > max_tokens, mark non-priority files for skeleton mode
        4. Only as last resort, exclude files based on depth
        
        Args:
            files: List of (path, rel_path) tuples
            
        Returns:
            Dict with processing instructions for each file
        """
        if not self.max_tokens:
            # No budget constraint - process all files normally
            return {rel_path: {'action': 'full', 'path': path} for path, rel_path in files}
        
        # First pass: estimate tokens for all files (without reading content)
        file_estimates = []
        for path, rel_path in files:
            try:
                size = path.stat().st_size
                est_tokens = estimate_tokens(str(size))  # Rough estimate based on size
            except (OSError, PermissionError):
                est_tokens = 0
            
            # Check if priority file
            is_priority = (rel_path in PRIORITY_FILES or 
                          Path(rel_path).name in PRIORITY_FILES)
            
            # Calculate depth (deeper files are less important)
            depth = len(Path(rel_path).parts)
            
            file_estimates.append({
                'path': path,
                'rel_path': rel_path,
                'est_tokens': est_tokens,
                'is_priority': is_priority,
                'depth': depth,
            })
        
        # Sort by priority (priority files first, then by depth)
        file_estimates.sort(key=lambda x: (not x['is_priority'], x['depth']))
        
        # Second pass: determine action for each file
        result = {}
        running_total = 0
        budget_remaining = self.max_tokens
        
        # Reserve 10% of budget for delimiters and tree
        budget_remaining = int(budget_remaining * 0.9)
        
        for file_info in file_estimates:
            rel_path = file_info['rel_path']
            est_tokens = file_info['est_tokens']
            is_priority = file_info['is_priority']
            
            if running_total + est_tokens <= budget_remaining:
                # Can include full file
                result[rel_path] = {'action': 'full', 'path': file_info['path']}
                running_total += est_tokens
            elif self.clean_mode and running_total + (est_tokens // 2) <= budget_remaining:
                # Try cleaned version (estimated 50% reduction)
                result[rel_path] = {'action': 'clean', 'path': file_info['path']}
                running_total += est_tokens // 2
            elif self.skeleton_mode and running_total + (est_tokens // 5) <= budget_remaining:
                # Try skeleton version (estimated 80% reduction)
                result[rel_path] = {'action': 'skeleton', 'path': file_info['path']}
                running_total += est_tokens // 5
            elif is_priority:
                # Priority file - include anyway, even if over budget
                result[rel_path] = {'action': 'full', 'path': file_info['path']}
                running_total += est_tokens
            # else: exclude file (don't add to result)
        
        return result

    def generate_tree_structure(self) -> str:
        """
        Generate a text-based directory tree structure using the unified file generator.
        Uses proper tree characters (└── for last item, ├── for others).

        Returns:
            String representation of the directory tree
        """
        tree_lines = ["--- REPOSITORY STRUCTURE ---"]

        # Build a set of all valid file paths for quick lookup
        valid_files = set()
        dirs_with_files = set()

        for file_path, rel_path in self.get_valid_files():
            valid_files.add(rel_path)
            # Track all parent directories
            parent = file_path.parent
            while parent != self.repo_path:
                dirs_with_files.add(parent.relative_to(self.repo_path).as_posix())
                parent = parent.parent

        def _build_tree(current_path: Path, prefix: str = ""):
            try:
                entries = sorted(
                    list(current_path.iterdir()),
                    key=lambda x: (not x.is_dir(), x.name.lower())
                )

                valid_entries = []
                for entry in entries:
                    rel_entry = entry.relative_to(self.repo_path).as_posix()
                    if entry.is_file() and rel_entry in valid_files:
                        valid_entries.append(entry)
                    elif entry.is_dir() and (rel_entry in dirs_with_files or self._should_include_in_tree(entry)):
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

        # Set up git worktree if branch/commit specified
        self._setup_git_worktree()

        if self.dry_run and self.verbose:
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

                # Collect all files first for token budgeting
                all_files = list(self.get_valid_files())
                
                # Calculate token budget strategy if max_tokens is set
                if self.max_tokens and not self.dry_run:
                    file_strategy = self._calculate_token_budget(all_files)
                else:
                    file_strategy = {rel_path: {'action': 'full', 'path': path} 
                                    for path, rel_path in all_files}

                # Process files according to strategy
                for file_path, rel_file in all_files:
                    if rel_file not in file_strategy:
                        # File was excluded by token budget
                        if self.verbose:
                            logger.info(f"Excluded '{rel_file}' due to token budget")
                        continue
                    
                    strategy = file_strategy[rel_file]
                    action = strategy['action']
                    
                    if self.dry_run:
                        if self.verbose:
                            logger.info(f"Would process: {rel_file} ({action})")
                        processed_count += 1
                        continue

                    # Build delimiter (XML format doesn't use language hints)
                    lang = get_language_from_path(rel_file)
                    start_header = self.start_delimiter.format(path=rel_file, lang=lang)
                    end_footer = self.end_delimiter.format(path=rel_file, lang=lang)

                    # Write start delimiter and count tokens
                    outfile.write(start_header + "\n")
                    if self.count_tokens:
                        self.total_tokens += get_tiktoken_token_count(start_header + "\n")

                    # Read and process file based on strategy
                    content = self._read_file_safely(file_path)
                    if content.startswith("[Error:"):
                        if self.verbose:
                            logger.warning(f"Skipping '{rel_file}' - {content}")
                        if self.count_tokens:
                            self.total_tokens -= get_tiktoken_token_count(start_header + "\n")
                        continue

                    # Apply processing based on strategy
                    if action == 'skeleton':
                        content = self._extract_skeleton(content, lang)
                    elif action == 'clean':
                        # Temporarily enable clean_mode for this file
                        original_clean = self.clean_mode
                        self.clean_mode = True
                        content = self._clean_content(content, lang)
                        self.clean_mode = original_clean
                    elif self.clean_mode:
                        # Global clean_mode is enabled but strategy is 'full'
                        # Still apply cleaning
                        content = self._clean_content(content, lang)
                    # else: action == 'full' and clean_mode off, use content as-is

                    # Check max_tokens before writing
                    if self.max_tokens:
                        content_tokens = get_tiktoken_token_count(content)
                        if self.total_tokens + content_tokens > self.max_tokens:
                            logger.warning(f"Token limit reached. Stopping dump at '{rel_file}'.")
                            outfile.write("\n")
                            outfile.write(end_footer + "\n")
                            if self.count_tokens:
                                self.total_tokens += get_tiktoken_token_count(end_footer + "\n")
                            processed_count += 1
                            break

                    # Write content
                    outfile.write(content)
                    if self.count_tokens:
                        self.total_tokens += get_tiktoken_token_count(content)

                    # Ensure file ends with newline before end delimiter
                    outfile.write("\n")
                    outfile.write(end_footer + "\n")
                    if self.count_tokens:
                        self.total_tokens += get_tiktoken_token_count(end_footer + "\n")

                    processed_count += 1

                    # Final check after file
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
            self._cleanup_git_worktree()
            sys.exit(1)

        return processed_count
