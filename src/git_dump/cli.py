"""CLI entry point for git_dump."""

import argparse
import logging
import os
import sys
from .core import RepoProcessor, get_tiktoken_token_count


def setup_logging(verbose: bool):
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s'
    )


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
        help="Do not respect .gitignore files"
    )
    parser.add_argument(
        "-i", "--ignore", action="append", help="Additional patterns to ignore"
    )
    parser.add_argument(
        "--include", action="append", help="Patterns to include (e.g., '*.py')"
    )
    parser.add_argument(
        "--start-delimiter",
        default="### File: {path}\n```{lang}",
        help="Custom start delimiter (use {path} and {lang} placeholders)"
    )
    parser.add_argument(
        "--end-delimiter",
        default="```",
        help="Custom end delimiter"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_false", dest="verbose", help="Quiet mode"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not write output file"
    )
    parser.add_argument(
        "--max-size", type=int, default=512000, help="Maximum file size to include in bytes (default: 512000 = 500KB)"
    )
    parser.add_argument(
        "--no-tree", action="store_false", dest="include_tree", help="Do not include directory tree structure"
    )
    parser.add_argument(
        "--count-tokens", action="store_true", help="Count total tokens in output (requires tiktoken if available)"
    )
    parser.add_argument(
        "--clipboard", "--clip", action="store_true", dest="use_clipboard",
        help="Copy output to clipboard (requires pyperclip: pip install pyperclip)"
    )
    parser.add_argument(
        "--max-tokens", type=int, default=None,
        help="Maximum token count limit. Stops processing when reached (requires tiktoken for accurate counting)"
    )
    parser.add_argument(
        "--clean", action="store_true", dest="clean_mode",
        help="Clean files by removing comments and excessive whitespace to reduce token count"
    )
    parser.add_argument(
        "--no-sort", action="store_false", dest="sort_priority",
        help="Disable priority sorting (README, config files first)"
    )
    parser.add_argument(
        "--branch", type=str, default=None,
        help="Git branch to dump (uses git worktree temporarily)"
    )
    parser.add_argument(
        "--commit", type=str, default=None,
        help="Git commit hash to dump (uses git worktree temporarily)"
    )

    args = parser.parse_args()

    if not os.path.isdir(args.repo_path):
        print(f"Error: Path '{args.repo_path}' is not a directory.")
        sys.exit(1)

    setup_logging(args.verbose)

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
        max_file_size=args.max_size,
        include_tree=args.include_tree,
        count_tokens=args.count_tokens,
        use_clipboard=args.use_clipboard,
        max_tokens=args.max_tokens,
        clean_mode=args.clean_mode,
        sort_priority=args.sort_priority,
        git_branch=args.branch,
        git_commit=args.commit,
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
            if args.count_tokens:
                print(f"Total estimated tokens: {processor.total_tokens}")
            print(f"Result saved to: {processor.output_file}")
            if args.use_clipboard:
                print("Output also copied to clipboard.")
        else:
            print("\nNo files were processed.")


if __name__ == "__main__":
    main()