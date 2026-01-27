"""Core functionality for git_dump."""

import os
import sys
import logging
from pathlib import Path
from typing import List, Optional, Generator, Tuple
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


def generate_tree_structure(repo_path: str, max_depth: Optional[int] = None) -> str:
    """
    Generate a text-based directory tree structure.

    Args:
        repo_path: Path to the repository
        max_depth: Maximum depth to traverse (None for unlimited)

    Returns:
        String representation of the directory tree
    """
    repo_path = Path(repo_path)
    tree_lines = ["--- REPOSITORY STRUCTURE ---"]

    def _should_ignore(path: Path) -> bool:
        """Check if a path should be ignored based on common patterns."""
        rel_path = path.relative_to(repo_path).as_posix()
        # Common directories to skip for cleaner output
        ignore_patterns = ['.git', '__pycache__', '.pytest_cache', '.ruff_cache', '.venv', 'venv', 'node_modules', '.DS_Store']
        return any(ignore_part in rel_path.split('/') for ignore_part in ignore_patterns)

    def _add_tree_item(item_path: Path, prefix: str = "", depth: int = 0):
        if max_depth is not None and depth > max_depth:
            return

        if _should_ignore(item_path):
            return

        # Get relative path from repo root
        rel_path = item_path.relative_to(repo_path)
        if rel_path.name == "":
            # This is the root
            display_name = str(repo_path.name)
        else:
            display_name = rel_path.as_posix()

        if item_path.is_dir():
            # Add directory
            tree_lines.append(f"{prefix}├── {display_name}/")
            children = sorted([child for child in item_path.iterdir() if not _should_ignore(child)],
                             key=lambda x: (x.is_file(), x.name.lower()))
            for i, child in enumerate(children):
                is_last = i == len(children) - 1
                new_prefix = prefix + ("    " if is_last else "│   ")
                _add_tree_item(child, new_prefix, depth + 1)
        else:
            # Add file
            tree_lines.append(f"{prefix}├── {display_name}")

    # Start with the root directory
    tree_lines.append(f"{repo_path.name}/")
    children = sorted([child for child in repo_path.iterdir() if not _should_ignore(child)],
                     key=lambda x: (x.is_file(), x.name.lower()))
    for i, child in enumerate(children):
        is_last = i == len(children) - 1
        prefix = "" if is_last else "│   "
        _add_tree_item(child, prefix)

    tree_lines.append("--- END REPOSITORY STRUCTURE ---\n")
    return "\n".join(tree_lines)


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
        self.max_file_size = max_file_size  # Max file size in bytes
        self.include_tree = include_tree
        self.count_tokens = count_tokens
        self.total_tokens = 0
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

    def _get_nested_gitignore_specs(self, directory: str) -> List:
        """Get all pathspecs from nested .gitignore files along the path."""
        if not self.use_gitignore:
            return []

        specs = []
        # Walk up the directory tree from the given directory to repo root
        current_path = Path(directory)
        repo_path = Path(self.repo_path)

        while current_path != repo_path.parent:
            gitignore_path = current_path / ".gitignore"
            if gitignore_path.exists():
                try:
                    with open(gitignore_path, "r", encoding="utf-8") as f:
                        patterns = f.readlines()
                    if patterns:
                        spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
                        # Calculate relative path from repo root for proper matching
                        rel_path_from_root = current_path.relative_to(repo_path)
                        specs.append((spec, str(rel_path_from_root) if rel_path_from_root != Path(".") else ""))
                except Exception as e:
                    if self.verbose:
                        logger.warning(f"Could not read .gitignore in {current_path}: {e}")

            if current_path == repo_path:
                break
            current_path = current_path.parent

        return specs

    def _matches_include(self, relative_path: str) -> bool:
        if not self.include_patterns:
            return True
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                return True
        return False

    def is_ignored(self, relative_path: str, directory: str = None) -> bool:
        # Always ignore .git directory
        if relative_path == ".git" or relative_path.startswith(".git" + os.sep):
            return True

        # Ignore the output file if it's within the repo path
        if os.path.abspath(os.path.join(self.repo_path, relative_path)) == self.output_file:
            return True

        # Check nested gitignores if in a subdirectory
        if directory and self.use_gitignore:
            nested_specs = self._get_nested_gitignore_specs(directory)
            for spec, base_path in nested_specs:
                # Adjust relative_path for matching against nested gitignore
                if base_path:
                    # For nested gitignore, we need to match relative to the gitignore's directory
                    # If relative_path is 'subdir/secret.txt' and gitignore is in 'subdir',
                    # then we match 'secret.txt' against the gitignore in 'subdir'
                    if relative_path.startswith(base_path + os.sep):
                        adjusted_path = relative_path[len(base_path) + 1:]  # Remove 'subdir/' from 'subdir/secret.txt'
                        if spec.match_file(adjusted_path):
                            return True
                else:
                    # Root gitignore - match the full relative path
                    if spec.match_file(relative_path):
                        return True

        # Check root-level gitignore and user patterns
        if self.spec and self.spec.match_file(relative_path):
            return True

        return False

    def _is_binary(self, file_path: str) -> bool:
        """Check if a file is binary by looking at the first 8KB."""
        try:
            with open(file_path, "rb") as f:
                # Read first 8KB to check for binary content
                chunk = f.read(8192)
                # Check for null bytes or high proportion of non-text characters
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

    def _read_file_chunks(self, file_path: str, chunk_size: int = 8192) -> Generator[str, None, None]:
        """Generator to read file in chunks to avoid memory issues."""
        try:
            with open(file_path, "r", encoding="utf-8", errors='replace') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except Exception as e:
            if self.verbose:
                logger.error(f"Error reading file {file_path}: {e}")
            raise

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
                    tree_structure = generate_tree_structure(self.repo_path)
                    outfile.write(tree_structure)

                # Walk through the repository
                for root, dirs, files in os.walk(self.repo_path):
                    rel_dir = os.path.relpath(root, self.repo_path)
                    if rel_dir == ".":
                        rel_dir = ""

                    # Filter directories in-place
                    dirs_to_remove = []
                    for d in dirs:
                        rel_d = os.path.join(rel_dir, d)
                        if self.is_ignored(rel_d, root):
                            dirs_to_remove.append(d)
                    for d in dirs_to_remove:
                        dirs.remove(d)

                    for filename in sorted(files):
                        rel_file = os.path.join(rel_dir, filename)

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

                            # Read and write file content
                            file_content = ""
                            for chunk in self._read_file_chunks(file_path):
                                file_content += chunk

                            # Write to output file
                            outfile.write(self.start_delimiter.format(path=rel_file) + "\n")
                            outfile.write(file_content)
                            if file_content and not file_content.endswith("\n"):
                                outfile.write("\n")
                            outfile.write(self.end_delimiter.format(path=rel_file) + "\n")
                            
                            # Count tokens if requested
                            if self.count_tokens:
                                self.total_tokens += get_tiktoken_token_count(file_content)
                            
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