#!/usr/bin/env python3
"""
Git Repository Content Concatenator

This script traverses a Git repository and combines the content of all files
into a single text file, excluding the .git directory.
"""

import os
import sys
import argparse


def process_repository(repo_path, output_filename):
    """
    Traverses the repository and writes file contents to the output file.
    
    Args:
        repo_path (str): The root directory of the repository.
        output_filename (str): The name of the file to write contents to.
        
    Returns:
        int: The number of files successfully processed.
    """
    processed_count = 0

    try:
        with open(output_filename, 'w', encoding='utf-8') as outfile:
            for root, dirs, files in os.walk(repo_path):
                # Skip .git directory by modifying dirs in-place
                if '.git' in dirs:
                    dirs.remove('.git')

                for filename in sorted(files):
                    file_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(file_path, repo_path)

                    # Skip the output file itself if it's in the repo path
                    if os.path.abspath(file_path) == os.path.abspath(output_filename):
                        continue

                    try:
                        with open(file_path, 'r', encoding='utf-8') as infile:
                            content = infile.read()
                            
                        # Write delimiters and content
                        outfile.write(f"--- FILE: {relative_path} ---\n")
                        outfile.write(content)
                        # Ensure content ends with a newline before the end delimiter
                        if content and not content.endswith('\n'):
                            outfile.write('\n')
                        outfile.write("--- END FILE ---\n")
                        
                        processed_count += 1
                    except (UnicodeDecodeError, PermissionError) as e:
                        print(f"Warning: Skipping '{relative_path}' - {e}")
                    except Exception as e:
                        print(f"Error processing '{relative_path}': {e}")

    except Exception as e:
        print(f"Fatal error writing to output file: {e}")
        sys.exit(1)

    return processed_count


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Concatenate all files in a Git repository into one file."
    )
    parser.add_argument(
        "repo_path", 
        help="Path to the root of the Git repository"
    )
    args = parser.parse_args()

    # Validate input path
    repo_path = os.path.abspath(args.repo_path)
    if not os.path.exists(repo_path):
        print(f"Error: Path '{repo_path}' does not exist.")
        sys.exit(1)
    if not os.path.isdir(repo_path):
        print(f"Error: Path '{repo_path}' is not a directory.")
        sys.exit(1)

    output_file = "repository_contents.txt"
    
    print(f"Processing repository at: {repo_path}")
    files_processed = process_repository(repo_path, output_file)

    # Final summary
    if os.path.exists(output_file):
        output_size = os.path.getsize(output_file)
        print("\nSummary:")
        print(f"Total files processed: {files_processed}")
        print(f"Output file size: {output_size} bytes")
        print(f"Result saved to: {os.path.abspath(output_file)}")
    else:
        print("\nNo files were processed.")


if __name__ == "__main__":
    main()
