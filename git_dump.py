#!/usr/bin/env python3
"""
Git Repository Content Concatenator

This script traverses a Git repository and combines the content of all files
into a single text file, respecting .gitignore if pathspec is available.
"""

import os
import sys
import argparse
import fnmatch
from typing import List, Optional

try:
    import pathspec
except ImportError:
    pathspec = None


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
        max_size: int = 512000,
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
        self.max_size = max_size
        self.spec = self._load_spec()

    def _load_spec(self):
        patterns = []
        if self.use_gitignore:
            gitignore_path = os.path.join(self.repo_path, ".gitignore")
            if os.path.exists(gitignore_path):
                try:
                    with open(gitignore_path, "r", encoding="utf-8") as f:
                        patterns.extend(f.readlines())
                except Exception as e:
                    if self.verbose:
                        print(f"Warning: Could not read .gitignore: {e}")

        patterns.extend(self.ignore_patterns)

        if pathspec and patterns:
            return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        return None

    def _matches_include(self, relative_path: str) -> bool:
        if not self.include_patterns:
            return True
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                return True
        return False

    def is_ignored(self, relative_path: str) -> bool:
        # Always ignore .git directory
        if relative_path == ".git" or relative_path.startswith(".git" + os.sep):
            return True
        
        # Ignore the output file if it's within the repo path
        if os.path.abspath(os.path.join(self.repo_path, relative_path)) == self.output_file:
            return True

        if self.spec and self.spec.match_file(relative_path):
            return True
            
        return False

    def process(self) -> int:
        processed_count = 0
        if self.dry_run:
            if self.verbose:
                print("Dry run mode: No files will be written.")
        
        try:
            if self.dry_run:
                outfile = None
            else:
                outfile = open(self.output_file, "w", encoding="utf-8")

            try:
                for root, dirs, files in os.walk(self.repo_path):
                    rel_dir = os.path.relpath(root, self.repo_path)
                    if rel_dir == ".":
                        rel_dir = ""

                    # Filter directories in-place
                    dirs_to_remove = []
                    for d in dirs:
                        rel_d = os.path.join(rel_dir, d)
                        if self.is_ignored(rel_d):
                            dirs_to_remove.append(d)
                    for d in dirs_to_remove:
                        dirs.remove(d)

                    for filename in sorted(files):
                        rel_file = os.path.join(rel_dir, filename)
                        
                        if self.is_ignored(rel_file):
                            continue
                        
                        if not self._matches_include(rel_file):
                            continue

                        file_path = os.path.join(root, filename)
                        try:
                            if self.max_size is not None:
                                try:
                                    if os.path.getsize(file_path) > self.max_size:
                                        if self.verbose:
                                            print(f"Skipping '{rel_file}' - exceeds max size ({self.max_size} bytes)")
                                        continue
                                except OSError:
                                    pass

                            if self._is_binary(file_path):
                                continue

                            if self.dry_run:
                                if self.verbose:
                                    print(f"Would process: {rel_file}")
                                processed_count += 1
                                continue

                            with open(file_path, "r", encoding="utf-8") as infile:
                                content = infile.read()

                            outfile.write(self.start_delimiter.format(path=rel_file) + "\n")
                            outfile.write(content)
                            if content and not content.endswith("\n"):
                                outfile.write("\n")
                            outfile.write(self.end_delimiter.format(path=rel_file) + "\n")
                            processed_count += 1
                        except (UnicodeDecodeError, PermissionError) as e:
                            if self.verbose:
                                print(f"Warning: Skipping '{rel_file}' - {e}")
                        except Exception as e:
                            if self.verbose:
                                print(f"Error processing '{rel_file}': {e}")
            finally:
                if outfile:
                    outfile.close()
        except Exception as e:
            print(f"Fatal error: {e}")
            sys.exit(1)

        return processed_count

    def _is_binary(self, file_path: str) -> bool:
        """Check if a file is binary."""
        try:
            with open(file_path, "rb") as f:
                chunk = f.read(1024)
                return b"\0" in chunk
        except Exception:
            return True


def main():
    parser = argparse.ArgumentParser(
        description="Concatenate all files in a Git repository into one file."
    )
    parser.add_argument("repo_path", help="Path to the root of the Git repository")
    parser.add_argument(
        "-o", "--output", default="repository_contents.txt", help="Output filename"
    )
    parser.add_argument(
        "--no-gitignore",
        action="store_false",
        dest="use_gitignore",
        help="Do not respect .gitignore files",
    )
    parser.add_argument(
        "-i", "--ignore", action="append", help="Additional patterns to ignore"
    )
    parser.add_argument(
        "--include", action="append", help="Patterns to include (e.g., '*.py')"
    )
    parser.add_argument(
        "--start-delimiter", default="--- FILE: {path} ---", help="Custom start delimiter"
    )
    parser.add_argument(
        "--end-delimiter", default="--- END FILE ---", help="Custom end delimiter"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_false", dest="verbose", help="Quiet mode"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not write output file"
    )
    parser.add_argument(
        "--max-size", type=int, default=512000, help="Maximum file size in bytes to include (default: 512000)"
    )

    args = parser.parse_args()

    if not os.path.isdir(args.repo_path):
        print(f"Error: Path '{args.repo_path}' is not a directory.")
        sys.exit(1)

    processor = RepoProcessor(
        args.repo_path,
        args.output,
        ignore_patterns=args.ignore,
        include_patterns=args.include,
        use_gitignore=args.use_gitignore,
        start_delimiter=args.start_delimiter,
        end_delimiter=args.end_delimiter,
        verbose=args.verbose,
        dry_run=args.dry_run,
        max_size=args.max_size,
    )

    if args.verbose:
        print(f"Processing repository at: {processor.repo_path}")
    
    files_processed = processor.process()

    if args.verbose:
        if args.dry_run:
            print(f"\nDry run summary: Would have processed {files_processed} files.")
        elif os.path.exists(processor.output_file):
            output_size = os.path.getsize(processor.output_file)
            print("\nSummary:")
            print(f"Total files processed: {files_processed}")
            print(f"Output file size: {output_size} bytes")
            print(f"Result saved to: {processor.output_file}")
        else:
            print("\nNo files were processed.")


if __name__ == "__main__":
    main()
