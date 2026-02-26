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
        
        Args:
            relative_path: Path relative to repo root
            directory: Absolute path to the parent directory (for nested gitignore lookup)
            
        Returns:
            True if the path should be ignored
        """
        # Always ignore .git directory
        if relative_path == ".git" or relative_path.startswith(".git" + os.sep):
            return True

        # Ignore the output file if it's within the repo path
        if os.path.abspath(os.path.join(self.repo_path, relative_path)) == self.output_file:
            return True

        # Check nested gitignore for the specific directory
        if directory and self.use_gitignore and pathspec:
            nested_spec = self._load_nested_gitignore(directory)
            if nested_spec:
                # For nested gitignore, match against the filename only (or relative to that dir)
                filename = os.path.basename(relative_path)
                if nested_spec.match_file(filename):
                    return True
                # Also try matching the relative path from that directory
                if directory != self.repo_path:
                    rel_from_dir = os.path.relpath(
                        os.path.join(self.repo_path, relative_path), 
                        directory
                    )
                    if nested_spec.match_file(rel_from_dir):
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

    def generate_tree_structure(self) -> str:
        """
        Generate a text-based directory tree structure using the same ignore logic.

        Returns:
            String representation of the directory tree
        """
        tree_lines = ["--- REPOSITORY STRUCTURE ---"]

        def _add_tree_item(item_path: Path, prefix: str = "", depth: int = 0):
            # Use only the item name for display (not full relative path)
            display_name = item_path.name
            
            rel_path = item_path.relative_to(self.repo_path).as_posix()
            
            # Use the same is_ignored logic as file processing
            if self.is_ignored(rel_path, str(item_path.parent)):
                return

            if item_path.is_dir():
                # Add directory
                tree_lines.append(f"{prefix}├── {display_name}/")
                
                # Get children, filtering with is_ignored
                try:
                    children = sorted(
                        [child for child in item_path.iterdir() 
                         if not self.is_ignored(
                             child.relative_to(self.repo_path).as_posix(),
                             str(item_path)
                         )],
                        key=lambda x: (x.is_dir(), x.name.lower())
                    )
                except PermissionError:
                    return
                    
                for i, child in enumerate(children):
                    is_last = i == len(children) - 1
                    new_prefix = prefix + ("    " if is_last else "│   ")
                    _add_tree_item(child, new_prefix, depth + 1)
            else:
                # Add file
                tree_lines.append(f"{prefix}├── {display_name}")

        # Start with the root directory
        root_path = Path(self.repo_path)
        tree_lines.append(f"{root_path.name}/")
        
        try:
            children = sorted(
                [child for child in root_path.iterdir() 
                 if not self.is_ignored(child.relative_to(root_path).as_posix(), str(root_path))],
                key=lambda x: (x.is_dir(), x.name.lower())
            )
        except PermissionError:
            children = []
            
        for i, child in enumerate(children):
            is_last = i == len(children) - 1
            prefix = "" if is_last else "│   "
            _add_tree_item(child, prefix)

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
