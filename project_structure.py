# -*- coding: utf-8 -*-
"""
Created on Wed 2026-06-10 3:05 p.m.
Updated 2026-07-27

@project: Burnaby_Openquake
@author: colin

Export or update an md file summarizing the project directory/file structure,
formatted to match the "cookiecutter-data-science" (CCDS) style, e.g.:

    ```
    ├── LICENSE            <- Open-source license if one is chosen
    ├── data
    │   ├── external       <- Data from third party sources.
    │   └── interim        <- Intermediate data that has been transformed.
    │
    ├── docs               <- A default mkdocs project; see www.mkdocs.org for details
    ```

Two entry points:
    - export_directory_tree(root_dir, output_file=...)
        Build a brand new tree from scratch.
    - update_directory_tree(root_dir, md_file)
        Find the existing tree inside an .md file (a fenced code block containing
        tree-drawing characters), and update it in place: keep existing comments
        for files/dirs that are still present, insert new entries (blank comment)
        for anything new, drop entries for anything no longer present, and leave
        the rest of the file untouched. Prints a summary of what changed.

NOTE ON BLANK-LINE SPACING:
The CCDS README's blank-line spacing is not fully algorithmic - it's hand edited
(e.g. plain single-line entries like `docs` or `models` get a trailing blank line
for pure readability, while other equally "plain" entries elsewhere don't). This
script reproduces the one part of that spacing that IS a consistent rule: a blank
separator line is inserted after a directory's listed contents, as long as that
directory is not the last entry in its sibling group. Extra "cosmetic" blank
lines between plain files are not reproduced. Tune `alignment_col` to taste.
"""
import os
import re
import fnmatch
import argparse
import datetime as dt
from pathlib import Path
from typing import Union, List, Optional, Dict, Tuple, Set

TODAY = dt.datetime.strftime(dt.datetime.today(), '%Y-%m-%d')

DEFAULT_IGNORE_DIRS = ['.git', '__pycache__', '.pycache', '.idea', '.vscode', '.ipynb_checkpoints']
DEFAULT_IGNORE_FILES = ['.gitkeep']

BRANCH = "├── "
LAST = "└── "
PIPE = "│   "
SPACE = "    "

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _normalize_extensions(extensions: Optional[Union[str, List[str]]]) -> Optional[Set[str]]:
    if not extensions:
        return None
    if isinstance(extensions, str):
        extensions = [extensions]
    return {ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in extensions}


class _SimpleGitignoreSpec:
    """
    Minimal fallback .gitignore matcher used when the optional `pathspec`
    package isn't installed. Supports comments, blank lines, '/'-anchored
    patterns, trailing '/' (directory-only patterns), simple '*'/'?'
    wildcards, and '!' negation (last matching pattern wins, same as git).
    Does NOT support '**' globstar patterns - install `pathspec` for full
    gitignore semantics: `pip install pathspec`.
    """

    def __init__(self, lines: List[str]):
        self.patterns = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            negate = line.startswith('!')
            if negate:
                line = line[1:]
            dir_only = line.endswith('/')
            if dir_only:
                line = line[:-1]
            anchored = line.startswith('/')
            if anchored:
                line = line[1:]
            self.patterns.append((line, negate, dir_only, anchored))

    def match_file(self, relpath: str) -> bool:
        is_dir_path = relpath.endswith('/')
        path_no_slash = relpath.rstrip('/')
        name = path_no_slash.rsplit('/', 1)[-1]
        matched = False
        for pattern, negate, dir_only, anchored in self.patterns:
            if dir_only and not is_dir_path:
                continue
            if anchored:
                hit = fnmatch.fnmatch(path_no_slash, pattern)
            else:
                hit = (fnmatch.fnmatch(name, pattern)
                       or fnmatch.fnmatch(path_no_slash, pattern)
                       or fnmatch.fnmatch(path_no_slash, f'*/{pattern}'))
            if hit:
                matched = not negate
        return matched


def _load_gitignore_spec(root_dir: Path):
    """Load root_dir/.gitignore if present. Prefers the `pathspec` package
    (pip install pathspec) for full gitignore semantics; falls back to a
    simplified matcher otherwise. Returns None if no .gitignore exists."""
    gitignore_path = root_dir / '.gitignore'
    if not gitignore_path.is_file():
        return None
    lines = gitignore_path.read_text(encoding='utf-8').splitlines()
    try:
        import pathspec
        return pathspec.PathSpec.from_lines('gitwildmatch', lines)
    except ImportError:
        print("Note: 'pathspec' isn't installed - using a simplified .gitignore "
              "matcher (no '**' support). Run 'pip install pathspec' for full "
              "gitignore semantics.")
        return _SimpleGitignoreSpec(lines)


def _visible_children(
        current_dir: Path,
        ext_filter: Optional[Set[str]],
        ignore_dirs: Set[str],
        gitignore_spec=None,
        rel_parts: Optional[List[str]] = None,
        ignore_files: Optional[Set[str]] = None,
        comment_lookup: Optional[Dict[str, List[str]]] = None,
) -> List[Path]:
    rel_parts = rel_parts or []
    ignore_files = ignore_files or set()
    try:
        items = sorted(current_dir.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        return []
    visible = []
    for item in items:
        is_dir = item.is_dir()
        if is_dir and item.name in ignore_dirs:
            continue
        if not is_dir and item.name in ignore_files:
            continue
        if not is_dir and ext_filter is not None and item.suffix.lower() not in ext_filter:
            continue
        if gitignore_spec is not None:
            rel_key = "/".join(rel_parts + [item.name])
            rel = rel_key + ("/" if is_dir else "")
            already_documented = comment_lookup is not None and rel_key in comment_lookup
            if gitignore_spec.match_file(rel) and not already_documented:
                # Skip newly-encountered gitignored paths, but don't strip
                # something the user already documented just because a
                # .gitignore rule (e.g. a blanket "/data/") now matches it.
                continue
        visible.append(item)
    return visible


def _build_tree(
        root_dir: Path,
        max_levels: int,
        alignment_col: int,
        ignore_dirs: Set[str],
        ext_filter: Optional[Set[str]],
        comment_lookup: Optional[Dict[str, List[str]]],
        gitignore_spec=None,
        ignore_files: Optional[Set[str]] = None,
) -> Tuple[List[str], Set[str], List[str]]:
    """
    Walk root_dir and produce CCDS-style tree lines (no root line, uses `<- `).

    If comment_lookup is given (path -> list of comment lines, first line is the
    inline comment, any further lines are wrapped-comment continuations), reuse
    those comments for matching paths and track which paths were found/added.
    If comment_lookup is None, every entry gets a blank comment (fresh export).

    If gitignore_spec is given (see _load_gitignore_spec), anything it matches
    is skipped - UNLESS it's already a key in comment_lookup (i.e. you already
    documented it), in which case it's kept as-is. This means a newly added
    .gitignore rule (e.g. a blanket "/data/") won't cause update_directory_tree
    to report already-documented entries as removed; it only keeps *new*
    gitignored paths out of the tree. ignore_files works like ignore_dirs but
    matches on exact filename (e.g. '.gitkeep').
    """
    lines: List[str] = []
    found_paths: Set[str] = set()
    added_paths: List[str] = []
    ignore_files = ignore_files or set()

    def recurse(current_dir: Path, current_level: int, prefix: str, rel_parts: List[str]) -> None:
        if current_level > max_levels:
            return
        items = _visible_children(current_dir, ext_filter, ignore_dirs, gitignore_spec, rel_parts,
                                   ignore_files, comment_lookup)
        count = len(items)
        for i, item in enumerate(items):
            is_last = (i == count - 1)
            connector = LAST if is_last else BRANCH
            rel_path = "/".join(rel_parts + [item.name])
            is_dir = item.is_dir()
            item_rel_parts = rel_parts + [item.name]
            children = (_visible_children(item, ext_filter, ignore_dirs, gitignore_spec, item_rel_parts,
                                           ignore_files, comment_lookup)
                        if is_dir else [])
            has_children = is_dir and len(children) > 0 and (current_level + 1) <= max_levels

            if comment_lookup is not None and rel_path in comment_lookup:
                comment_lines = comment_lookup[rel_path]
                found_paths.add(rel_path)
            else:
                comment_lines = []
                if comment_lookup is not None:
                    added_paths.append(rel_path)

            head = f"{prefix}{connector}{item.name}"
            if comment_lines and comment_lines[0]:
                pad = max(2, alignment_col - len(head))
                lines.append(f"{head}{' ' * pad}<- {comment_lines[0]}")
            else:
                lines.append(head)
            if len(comment_lines) > 1:
                # Continuation lines for a wrapped comment keep the vertical
                # tree connector going (unless this is the last sibling, in
                # which case there's nothing below to connect to), and align
                # their text under the same column as the first comment line.
                cont_indent = prefix + (SPACE if is_last else PIPE)
                comment_col = alignment_col + 3  # matches the "<- " offset above
                fill = max(1, comment_col - len(cont_indent))
                for extra in comment_lines[1:]:
                    lines.append(f"{cont_indent}{' ' * fill}{extra}")

            if has_children:
                next_prefix = prefix + (SPACE if is_last else PIPE)
                recurse(item, current_level + 1, next_prefix, item_rel_parts)

            if is_dir:
                # Directories always get a trailing blank separator line, whether
                # they have listed children or are empty/single-line. Standalone
                # files never do. A dangling blank at the very end of the whole
                # tree is stripped afterwards.
                lines.append(f"{prefix}│")

    recurse(root_dir, 1, "", [])

    # Collapse runs of consecutive blank spacer lines (e.g. when a directory
    # closes right at the point its parent also closes) down to a single
    # spacer - keep the last (outermost) one, since that's the correct
    # indent level for the dedent point.
    def _is_spacer(line: str) -> bool:
        return set(line) <= {'│', ' '}

    collapsed: List[str] = []
    i = 0
    while i < len(lines):
        if _is_spacer(lines[i]):
            j = i
            while j + 1 < len(lines) and _is_spacer(lines[j + 1]):
                j += 1
            collapsed.append(lines[j])
            i = j + 1
        else:
            collapsed.append(lines[i])
            i += 1
    lines = collapsed

    # Don't leave a trailing blank spacer line at the very end of the tree.
    while lines and _is_spacer(lines[-1]):
        lines.pop()

    return lines, found_paths, added_paths


# --------------------------------------------------------------------------- #
# Create from scratch
# --------------------------------------------------------------------------- #

def export_directory_tree(
        root_dir: Union[str, Path],
        output_file: Union[str, Path] = None,
        extensions: Optional[Union[str, List[str]]] = None,
        max_levels: int = 3,
        alignment_col: int = 30,
        ignore_dirs: Optional[List[str]] = None,
        use_gitignore: bool = True,
) -> None:
    """
    Generate a CCDS-style directory tree (in a fenced code block, using `<- `
    for descriptions) and write it to a new .md file.

    :param root_dir: Path to the target directory to map.
    :param output_file: Path to the file to write. Defaults to
        `<root_dir>/directory_structure_<today>.md`.
    :param extensions: Optional single extension or list of extensions to filter files.
    :param max_levels: How many levels deep to traverse (defaults to 3).
    :param alignment_col: The character column where `<-` tags align.
    :param ignore_dirs: List of directory names to skip entirely.
    :param use_gitignore: If True (default), also skip anything matched by a
        `.gitignore` file found at root_dir. Uses `pathspec` if installed,
        otherwise a simplified fallback matcher.
    """
    root = Path(root_dir)
    if not root.is_dir():
        raise ValueError(f"The path '{root_dir}' is not a valid directory.")

    if output_file is None:
        output_file = root / f"directory_structure_{TODAY}.md"

    if ignore_dirs is None:
        ignore_dirs = DEFAULT_IGNORE_DIRS
    ext_filter = _normalize_extensions(extensions)
    gitignore_spec = _load_gitignore_spec(root) if use_gitignore else None

    tree_lines, _, _ = _build_tree(
        root, max_levels, alignment_col, set(ignore_dirs), ext_filter,
        comment_lookup=None, gitignore_spec=gitignore_spec, ignore_files=set(DEFAULT_IGNORE_FILES),
    )
    content = "```\n" + "\n".join(tree_lines) + "\n```\n"

    Path(output_file).write_text(content, encoding='utf-8')
    print(f"Successfully wrote directory structure to: {output_file}")


# --------------------------------------------------------------------------- #
# Update an existing .md file in place
# --------------------------------------------------------------------------- #

_ENTRY_RE = re.compile(
    r'^(?P<prefix>(?:(?:│   )|(?:    ))*)(?P<connector>├── |└── )(?P<name>.+?)'
    r'(?:\s{2,}(?:<-|#)\s?(?P<comment>.*))?\s*$'
)
_CONT_RE = re.compile(r'^(?P<prefix>(?:(?:│   )|(?:    ))*)\s+(?P<text>\S.*?)\s*$')


def _parse_existing_tree(block_lines: List[str]) -> Dict[str, List[str]]:
    """Parse an existing rendered tree block into {relpath: [comment, continuation...]}."""
    comment_lookup: Dict[str, List[str]] = {}
    stack: List[str] = []
    last_key: Optional[str] = None

    for raw in block_lines:
        stripped = raw.strip()
        if not stripped or set(stripped) <= {'│'}:
            continue  # blank spacer line

        m = _ENTRY_RE.match(raw)
        if m:
            prefix = m.group('prefix') or ''
            depth = len(prefix) // 4
            name = m.group('name').rstrip().rstrip('/')
            comment = (m.group('comment') or '').strip()
            stack = stack[:depth]
            stack.append(name)
            relpath = "/".join(stack)
            comment_lookup[relpath] = [comment] if comment else []
            last_key = relpath
            continue

        cm = _CONT_RE.match(raw)
        if cm and last_key is not None:
            comment_lookup[last_key].append(cm.group('text').rstrip())

    return comment_lookup


def _locate_tree_block(file_lines: List[str]) -> Optional[Tuple[int, int, List[str]]]:
    """Find the fenced code block that contains the directory tree. Returns
    (start_fence_index, end_fence_index, block_lines) or None if not found."""
    fence_indices = [i for i, l in enumerate(file_lines) if l.strip().startswith('```')]
    for start, end in zip(fence_indices[0::2], fence_indices[1::2]):
        block = file_lines[start + 1:end]
        if any(('├──' in l or '└──' in l) for l in block):
            return start, end, block
    return None


def update_directory_tree(
        root_dir: Union[str, Path],
        md_file: Union[str, Path],
        extensions: Optional[Union[str, List[str]]] = None,
        max_levels: int = 3,
        alignment_col: int = 30,
        ignore_dirs: Optional[List[str]] = None,
        use_gitignore: bool = True,
) -> None:
    """
    Update the directory tree inside an existing .md file in place: keeps
    existing comments for files/dirs still present, inserts new entries with
    blank comments where appropriate, and removes entries no longer present.
    Everything outside the tree's code block is left untouched. Prints a
    summary of added/removed paths.

    :param root_dir: Path to the project root directory to scan.
    :param md_file: Path to the existing markdown file containing the tree.
    :param use_gitignore: If True (default), also skip anything matched by a
        `.gitignore` file found at root_dir. If a previously-tracked entry now
        falls under a new/changed `.gitignore` rule, it will be reported as
        "removed", same as if the file itself had been deleted.
    """
    md_path = Path(md_file)
    if not md_path.is_file():
        raise FileNotFoundError(f"'{md_file}' does not exist.")

    root = Path(root_dir)
    if not root.is_dir():
        raise ValueError(f"The path '{root_dir}' is not a valid directory.")

    original_lines = md_path.read_text(encoding='utf-8').splitlines()
    located = _locate_tree_block(original_lines)
    if located is None:
        raise ValueError(
            f"Could not find an existing directory tree (a fenced code block "
            f"containing '├──'/'└──') in '{md_file}'."
        )
    start, end, block_lines = located
    comment_lookup = _parse_existing_tree(block_lines)

    if ignore_dirs is None:
        ignore_dirs = DEFAULT_IGNORE_DIRS
    ext_filter = _normalize_extensions(extensions)
    gitignore_spec = _load_gitignore_spec(root) if use_gitignore else None

    new_tree_lines, found_paths, added_paths = _build_tree(
        root, max_levels, alignment_col, set(ignore_dirs), ext_filter,
        comment_lookup, gitignore_spec=gitignore_spec, ignore_files=set(DEFAULT_IGNORE_FILES),
    )
    removed_paths = sorted(set(comment_lookup.keys()) - found_paths)

    updated_lines = original_lines[:start + 1] + new_tree_lines + original_lines[end:]
    md_path.write_text("\n".join(updated_lines) + "\n", encoding='utf-8')

    print(f"Updated '{md_file}':")
    if added_paths:
        print(f"  Added ({len(added_paths)}):")
        for p in added_paths:
            print(f"    + {p}")
    else:
        print("  Added: none")
    if removed_paths:
        print(f"  Removed ({len(removed_paths)}):")
        for p in removed_paths:
            print(f"    - {p}")
    else:
        print("  Removed: none")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Generate or update a CCDS-style Markdown directory tree."
    )
    parser.add_argument(
        'md_file', nargs='?', default=None,
        help="Existing markdown file to update in place (default behaviour)."
    )
    parser.add_argument(
        '-c', dest='create', nargs='?', const='__DEFAULT__', default=None,
        metavar='NEW_FILE',
        help="Create a new tree file from scratch instead of updating an existing "
             "file. Optionally give a filename; defaults to "
             "'directory_structure_<today>.md'."
    )
    parser.add_argument(
        '--root', default=os.getcwd(),
        help="Project root directory to scan (default: current working directory)."
    )
    parser.add_argument('--max-levels', type=int, default=3, dest='max_levels')
    parser.add_argument('--alignment-col', type=int, default=30, dest='alignment_col')
    parser.add_argument(
        '--ext', nargs='*', default=None,
        help="Restrict to specific file extensions, e.g. --ext .py .md"
    )
    parser.add_argument(
        '--no-gitignore', dest='use_gitignore', action='store_false',
        help="Don't skip entries matched by a .gitignore file at the project root "
             "(by default, anything a root-level .gitignore excludes is skipped)."
    )
    parser.add_argument(
        '--debug', action='store_true',
        help="Print the project root being scanned and the top-level items found "
             "there, before doing anything else - useful for tracking down "
             "unexpected 'Added'/'Removed' entries (e.g. a wrong --root)."
    )
    args = parser.parse_args()

    root = Path(args.root)

    if args.debug:
        print(f"[debug] Scanning root: {root.resolve()}")
        if not root.is_dir():
            print(f"[debug] WARNING: '{root}' is not a valid directory.")
        else:
            ignore_dirs = set(DEFAULT_IGNORE_DIRS)
            gitignore_spec = _load_gitignore_spec(root) if args.use_gitignore else None
            top_items = _visible_children(
                root, _normalize_extensions(args.ext), ignore_dirs,
                gitignore_spec, [], set(DEFAULT_IGNORE_FILES), None,
            )
            print(f"[debug] Top-level items found ({len(top_items)}):")
            for item in top_items:
                print(f"    {'[dir] ' if item.is_dir() else '[file]'} {item.name}")

    if args.create is not None:
        if args.create == '__DEFAULT__':
            new_file = root / f"directory_structure_{TODAY}.md"
        else:
            new_file = Path(args.create)
            if not new_file.is_absolute():
                new_file = root / new_file

        if new_file.exists():
            resp = input(f"'{new_file}' already exists. Overwrite? [y/n]: ").strip().lower()
            if resp != 'y':
                print("Aborted - existing file left unchanged.")
                return

        export_directory_tree(
            root, output_file=new_file,
            max_levels=args.max_levels, alignment_col=args.alignment_col,
            extensions=args.ext, use_gitignore=args.use_gitignore,
        )
    else:
        if not args.md_file:
            parser.error(
                "Provide the markdown file to update (e.g. 'README.md'), "
                "or use -c to create a new tree file."
            )
        update_directory_tree(
            root, args.md_file,
            max_levels=args.max_levels, alignment_col=args.alignment_col,
            extensions=args.ext, use_gitignore=args.use_gitignore,
        )


if __name__ == "__main__":
    main()
