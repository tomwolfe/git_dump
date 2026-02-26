"""Core functionality for git_dump."""

import os
import sys
import logging
from pathlib import Path
from typing import List, Optional, Generator, Tuple, Dict
import fnmatch

try:
    import pathspec
except ImportError:
    pathspec = None

logger = logging.getLogger(__name__)


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


class RepoProcessor:
    def __init__(
        self,
        repo_path: str,
        output_file: str,
        ignore_patterns: Optional[List[str]] = None,
        include_patterns: Optional[List[str]] = None,
        use_gitignore: bool = True,
        start_delimiter: str = "--- FILE: {path} ---",
        end_delimiter: str = "--- END FILE ---",
        verbose: bool = True,
        dry_run: bool = False,
        max_file_size: int = 512000,  # 500KB default
        include_tree: bool = True,
        count_tokens: bool = False,
    ):
        self.repo_path = os.path.abspath(repo_path)
        self.output_file = os.path.abspath(output_file)
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
        self.total_tokens = 0
        
        # Cache for nested .gitignore specs: maps directory path -> PathSpec
        self.gitignore_cache: Dict[str, pathspec.PathSpec] = {}
        
        # Load all specs upfront
        self.spec = self._load_spec()

    def _load_spec(self):
        """Load pathspec with support for nested .gitignore files."""
        patterns = []

        # Load root .gitignore if it exists and gitignore is enabled
        if self.use_gitignore:
            gitignore_path = os.path.join(self.repo_path, ".gitignore")
            if os.path.exists(gitignore_path):
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

    def _load_nested_gitignore(self, directory: str) -> Optional[pathspec.PathSpec]:
        """
        Load .gitignore spec for a specific directory (cached).
        
        Args:
            directory: Absolute path to the directory
            
        Returns:
            PathSpec for that directory's .gitignore, or None if not found/cached
        """
        if not self.use_gitignore:
            return None
            
        if directory in self.gitignore_cache:
            return self.gitignore_cache[directory]
        
        # Check if this directory has a .gitignore
        gitignore_path = os.path.join(directory, ".gitignore")
        if os.path.exists(gitignore_path):
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    patterns = f.readlines()
                if patterns:
                    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
                    self.gitignore_cache[directory] = spec
                    return spec
            except Exception as e:
                if self.verbose:
                    logger.warning(f"Could not read .gitignore in {directory}: {e}")
        
        # Cache None to avoid re-checking
        self.gitignore_cache[directory] = None
        return None

    def _matches_include(self, relative_path: str) -> bool:
        if not self.include_patterns:
            return True
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                return True
        return False

    def is_ignored(self, relative_path: str, directory: str = None) -> bool:
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
        # Always ignore .git directory
        parts = relative_path.replace('\\', '/').split('/')
        if '.git' in parts:
            return True

        # Ignore the output file if it's within the repo path
        if os.path.abspath(os.path.join(self.repo_path, relative_path)) == self.output_file:
            return True

        # Check root-level spec and custom ignore patterns
        if self.spec and self.spec.match_file(relative_path):
            return True

        # Cumulative nested check: Check every .gitignore from root to the file
        if self.use_gitignore and pathspec:
            # Normalize path to use forward slashes
            target_rel = relative_path.replace('\\', '/')
            
            # Check each parent directory for a .gitignore
            path_parts = target_rel.split('/')
            for i in range(len(path_parts) - 1):  # -1 to exclude the file itself
                # Build the parent directory path
                parent_rel = '/'.join(path_parts[:i+1])
                abs_parent = os.path.join(self.repo_path, parent_rel)
                
                nested_spec = self._load_nested_gitignore(abs_parent)
                if nested_spec:
                    # Match relative to the directory where .gitignore lives
                    rel_to_gitignore = '/'.join(path_parts[i+1:])
                    if nested_spec.match_file(rel_to_gitignore):
                        return True

        return False

    def _is_binary(self, file_path: str) -> bool:
        """Check if a file is binary by looking at the first 8KB."""
        try:
            with open(file_path, "rb") as f:
                # Read first 8KB to check for binary content
                chunk = f.read(8192)
                # Check for null bytes
                if b"\0" in chunk:
                    return True
                # Try to decode as text - if it fails, it's likely binary
                try:
                    chunk.decode('utf-8')
                except UnicodeDecodeError:
                    return True
        except Exception:
            return True
        return False

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
        if self.is_ignored(rel_path, str(item_path.parent)):
            return False
        
        # For files, also check binary and size for perfect parity with dump
        if item_path.is_file():
            try:
                if item_path.stat().st_size > self.max_file_size:
                    return False
                if self._is_binary(str(item_path)):
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
        root_path = Path(self.repo_path)
        tree_lines.append(f"{root_path.name}/")

        _build_tree(root_path)
        tree_lines.append("--- END REPOSITORY STRUCTURE ---\n")
        return "\n".join(tree_lines)

    def process(self) -> int:
        processed_count = 0
        if self.dry_run:
            if self.verbose:
                logger.info("Dry run mode: No files will be written.")

        try:
            if self.dry_run:
                outfile = None
            else:
                outfile = open(self.output_file, "w", encoding="utf-8")

            try:
                # Write repository structure tree if requested
                if self.include_tree and not self.dry_run:
                    tree_structure = self.generate_tree_structure()
                    outfile.write(tree_structure)

                # Walk through the repository
                for root, dirs, files in os.walk(self.repo_path):
                    rel_dir = os.path.relpath(root, self.repo_path)
                    if rel_dir == ".":
                        rel_dir = ""

                    # Filter directories in-place
                    dirs_to_remove = []
                    for d in dirs:
                        rel_d = os.path.join(rel_dir, d) if rel_dir else d
                        if self.is_ignored(rel_d, root):
                            dirs_to_remove.append(d)
                    for d in dirs_to_remove:
                        dirs.remove(d)

                    for filename in sorted(files):
                        rel_file = os.path.join(rel_dir, filename) if rel_dir else filename

                        if self.is_ignored(rel_file, root):
                            continue

                        if not self._matches_include(rel_file):
                            continue

                        file_path = os.path.join(root, filename)

                        # Check file size
                        try:
                            file_size = os.path.getsize(file_path)
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

                            # STREAM file content directly to output (memory efficient)
                            outfile.write(self.start_delimiter.format(path=rel_file) + "\n")
                            
                            with open(file_path, "r", encoding="utf-8", errors='replace') as infile:
                                for chunk in infile:
                                    outfile.write(chunk)
                                    if self.count_tokens:
                                        self.total_tokens += get_tiktoken_token_count(chunk)
                            
                            # Ensure file ends with newline before end delimiter
                            outfile.write("\n" if not outfile.tell() == 0 else "")
                            outfile.write(self.end_delimiter.format(path=rel_file) + "\n")

                            processed_count += 1
                        except (UnicodeDecodeError, PermissionError) as e:
                            if self.verbose:
                                logger.warning(f"Skipping '{rel_file}' - {e}")
                        except Exception as e:
                            if self.verbose:
                                logger.error(f"Error processing '{rel_file}': {e}")
            finally:
                if outfile:
                    outfile.close()
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            sys.exit(1)

        return processed_count
