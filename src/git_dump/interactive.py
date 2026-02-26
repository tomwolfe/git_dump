"""Interactive TUI for git-dump using questionary."""

import os
from pathlib import Path
from typing import List, Tuple, Optional, Set, Dict
from dataclasses import dataclass


@dataclass
class FileNode:
    """Represents a file or directory in the tree."""
    path: str
    is_dir: bool
    is_selected: bool = True
    children: Optional[List['FileNode']] = None
    parent: Optional['FileNode'] = None
    size: int = 0
    is_binary: bool = False


def get_file_size(path: Path) -> int:
    """Get file size safely."""
    try:
        return path.stat().st_size
    except (OSError, PermissionError):
        return 0


def is_binary_file(path: Path) -> bool:
    """Check if a file is binary using simple heuristics."""
    binary_extensions = {
        '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svg',
        '.exe', '.dll', '.so', '.dylib', '.bin', '.dat',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.zip', '.tar', '.gz', '.rar', '.7z',
        '.pyc', '.pyo', '.class', '.o', '.a',
        '.db', '.sqlite', '.sqlite3',
        '.woff', '.woff2', '.ttf', '.eot',
    }
    return path.suffix.lower() in binary_extensions


def build_file_tree(repo_path: Path, ignore_patterns: Set[str] = None) -> FileNode:
    """
    Build a tree structure of all files in the repository.
    
    Args:
        repo_path: Path to the repository root
        ignore_patterns: Set of patterns to ignore
        
    Returns:
        Root FileNode with all children
    """
    if ignore_patterns is None:
        ignore_patterns = {
            '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build',
            '.tox', '.eggs', '*.egg-info', '.pytest_cache', '.mypy_cache', '.coverage',
            '.husky', '.idea', '.vscode', '.vs', '.eclipse', '.settings',
            'target', 'bin', 'obj', '.gradle', '.mvn',
            '.cache', '.tmp', '.temp', 'tmp', 'temp',
            'coverage', '.nyc_output', '.parcel-cache',
        }
    
    root = FileNode(path=str(repo_path.name), is_dir=True, is_selected=True)
    node_map = {str(repo_path): root}
    
    # Walk the directory tree
    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Skip ignored directories
        dirnames[:] = [d for d in dirnames if d not in ignore_patterns and not d.startswith('.')]
        
        current_path = Path(dirpath)
        current_key = str(current_path)
        
        if current_key not in node_map:
            continue
            
        parent_node = node_map[current_key]
        
        # Add directories
        for dirname in dirnames:
            dir_full_path = current_path / dirname
            dir_node = FileNode(
                path=dirname,
                is_dir=True,
                is_selected=True,
                parent=parent_node
            )
            if parent_node.children is None:
                parent_node.children = []
            parent_node.children.append(dir_node)
            node_map[str(dir_full_path)] = dir_node
        
        # Add files
        for filename in filenames:
            if filename.startswith('.'):
                continue
            file_full_path = current_path / filename
            file_node = FileNode(
                path=filename,
                is_dir=False,
                is_selected=True,
                parent=parent_node,
                size=get_file_size(file_full_path),
                is_binary=is_binary_file(file_full_path)
            )
            if parent_node.children is None:
                parent_node.children = []
            parent_node.children.append(file_node)
    
    return root


def collect_selected_files(node: FileNode, repo_path: Path, current_path: str = "") -> List[str]:
    """
    Recursively collect all selected file paths.
    
    Args:
        node: Current tree node
        repo_path: Repository root path
        current_path: Current path relative to repo root
        
    Returns:
        List of relative file paths that are selected
    """
    selected = []
    
    if node.is_dir:
        if node.children:
            new_path = os.path.join(current_path, node.path) if current_path else node.path
            for child in node.children:
                selected.extend(collect_selected_files(child, repo_path, new_path))
    else:
        if node.is_selected and not node.is_binary:
            full_path = os.path.join(current_path, node.path) if current_path else node.path
            selected.append(full_path)
    
    return selected


def toggle_node_selection(node: FileNode):
    """Toggle selection state for a node and all its children."""
    node.is_selected = not node.is_selected
    if node.children:
        for child in node.children:
            toggle_node_selection(child)


def run_interactive_selection(repo_path: str, ignore_patterns: Set[str] = None) -> List[str]:
    """
    Run interactive file selection TUI.
    
    Args:
        repo_path: Path to the repository root
        ignore_patterns: Set of patterns to ignore
        
    Returns:
        List of selected file paths relative to repo root
    """
    try:
        import questionary
    except ImportError:
        raise ImportError(
            "questionary is required for interactive mode. "
            "Install with: pip install questionary"
        )
    
    repo_path = Path(repo_path).resolve()
    
    # Build file tree
    root = build_file_tree(repo_path, ignore_patterns)
    
    # Flatten the tree into a list for selection
    # Format: "path/to/file.py (1.2 KB)"
    file_choices = []
    
    def flatten_tree(node: FileNode, current_path: str = "", depth: int = 0):
        if node.is_dir:
            # Add directory as a separator/organizer
            dir_path = os.path.join(current_path, node.path) if current_path else node.path
            indent = "  " * depth
            file_choices.append(questionary.Separator(f"\n{indent}📁 {node.path}/"))
            
            if node.children:
                # Sort: directories first, then files
                dirs = [c for c in node.children if c.is_dir]
                files = [c for c in node.children if not c.is_dir]
                dirs.sort(key=lambda x: x.path.lower())
                files.sort(key=lambda x: x.path.lower())
                
                for child in dirs:
                    flatten_tree(child, dir_path, depth + 1)
                for child in files:
                    flatten_tree(child, dir_path, depth + 1)
        else:
            # Add file as a selectable choice
            if not node.is_binary:
                file_path = os.path.join(current_path, node.path) if current_path else node.path
                size_kb = node.size / 1024
                size_str = f"{size_kb:.1f} KB" if size_kb >= 1 else f"{node.size} B"
                indent = "  " * depth
                checkbox_text = f"{indent}{node.path} ({size_str})"
                
                choice = questionary.Choice(
                    title=checkbox_text,
                    value=file_path,
                    checked=node.is_selected
                )
                file_choices.append(choice)
    
    flatten_tree(root)
    
    # Show interactive checkbox selection
    selected_files = questionary.checkbox(
        "Select files to include in dump (Space to toggle, A to toggle all, Enter to confirm)",
        choices=file_choices,
        use_shortcuts=True,
        instruction="\n\nInstructions: Space=toggle, A=toggle all, Enter=confirm",
        qmark="📦",
        pointer="👉"
    ).ask()
    
    if selected_files is None:
        # User cancelled
        return []
    
    return selected_files


def run_interactive_with_tree(repo_path: str, ignore_patterns: Set[str] = None) -> Tuple[List[str], FileNode]:
    """
    Run interactive selection and return both selected files and the tree.
    
    Args:
        repo_path: Path to the repository root
        ignore_patterns: Set of patterns to ignore
        
    Returns:
        Tuple of (selected file paths, root FileNode)
    """
    try:
        import questionary
    except ImportError:
        raise ImportError(
            "questionary is required for interactive mode. "
            "Install with: pip install questionary"
        )
    
    repo_path = Path(repo_path).resolve()
    
    # Build file tree
    root = build_file_tree(repo_path, ignore_patterns)
    
    # Create flat list of files with metadata
    file_info = []
    
    def collect_files(node: FileNode, current_path: str = ""):
        if node.is_dir:
            if node.children:
                new_path = os.path.join(current_path, node.path) if current_path else node.path
                for child in node.children:
                    collect_files(child, new_path)
        else:
            if not node.is_binary:
                file_path = os.path.join(current_path, node.path) if current_path else node.path
                size_kb = node.size / 1024
                file_info.append({
                    'path': file_path,
                    'size': node.size,
                    'size_str': f"{size_kb:.1f} KB" if size_kb >= 1 else f"{node.size} B",
                    'selected': True
                })
    
    collect_files(root)
    
    # Sort by path
    file_info.sort(key=lambda x: x['path'].lower())
    
    # Create choices
    choices = []
    for info in file_info:
        choice = questionary.Choice(
            title=f"{info['path']} ({info['size_str']})",
            value=info['path'],
            checked=True
        )
        choices.append(choice)
    
    # Show interactive selection
    selected_files = questionary.checkbox(
        "Select files to include in dump",
        choices=choices,
        use_shortcuts=True,
        instruction="\nSpace=toggle, A=toggle all, Enter=confirm",
        qmark="📦",
        pointer="👉"
    ).ask()
    
    if selected_files is None:
        return [], root
    
    return selected_files, root
