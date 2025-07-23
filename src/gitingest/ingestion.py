"""Functions to ingest and analyze a codebase directory or single file."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pathspec import PathSpec

from gitingest.config import MAX_DIRECTORY_DEPTH, MAX_FILES, MAX_TOTAL_SIZE_BYTES
from gitingest.output_formatter import format_node
from gitingest.schemas import FileSystemNode, FileSystemNodeType, FileSystemStats
from gitingest.utils.ingestion_utils import _should_exclude, _should_include

if TYPE_CHECKING:
    from gitingest.query_parser import IngestionQuery


def ingest_query(query: IngestionQuery) -> tuple[str, str, str]:
    """Run the ingestion process for a parsed query.

    This is the main entry point for analyzing a codebase directory or single file. It processes the query
    parameters, reads the file or directory content, and generates a summary, directory structure, and file content,
    along with token estimations.

    Parameters
    ----------
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.

    Returns
    -------
    tuple[str, str, str]
        A tuple containing the summary, directory structure, and file contents.

    Raises
    ------
    ValueError
        If the path cannot be found, is not a file, or the file has no content.

    """
    subpath = Path(query.subpath.strip("/")).as_posix()
    path = query.local_path / subpath

    if not path.exists():
        msg = f"{query.slug} cannot be found"
        raise ValueError(msg)

    if (query.type and query.type == "blob") or query.local_path.is_file():
        # TODO: We do this wrong! We should still check the branch and commit!
        if not path.is_file():
            msg = f"Path {path} is not a file"
            raise ValueError(msg)

        relative_path = path.relative_to(query.local_path)

        file_node = FileSystemNode(
            name=path.name,
            type=FileSystemNodeType.FILE,
            size=path.stat().st_size,
            file_count=1,
            path_str=str(relative_path),
            path=path,
        )

        if not file_node.content:
            msg = f"File {file_node.name} has no content"
            raise ValueError(msg)

        return format_node(file_node, query=query)

    root_node = FileSystemNode(
        name=path.name,
        type=FileSystemNodeType.DIRECTORY,
        path_str=str(path.relative_to(query.local_path)),
        path=path,
    )

    stats = FileSystemStats()

    include_files: set[str] | None = None
    include_dirs: set[str] | None = None

    if query.include_patterns:
        spec = PathSpec.from_lines("gitwildmatch", query.include_patterns)
        matched = [path / p for p in spec.match_tree(path)]
        include_files = {m.relative_to(query.local_path).as_posix() for m in matched if m.is_file() or m.is_symlink()}
        include_dirs = set()
        for m in matched:
            cur = m.parent
            while True:
                include_dirs.add(cur.relative_to(query.local_path).as_posix())
                if cur == query.local_path:
                    break
                cur = cur.parent

    _process_node(
        node=root_node,
        query=query,
        stats=stats,
        include_files=include_files,
        include_dirs=include_dirs,
    )

    return format_node(root_node, query=query)


def _process_node(
    node: FileSystemNode,
    query: IngestionQuery,
    stats: FileSystemStats,
    *,
    include_files: set[str] | None = None,
    include_dirs: set[str] | None = None,
) -> None:
    # pylint: disable=too-many-branches
    """Process a file or directory item within a directory.

    This function handles each file or directory item, checking if it should be included or excluded based on the
    provided patterns. It handles symlinks, directories, and files accordingly.

    Parameters
    ----------
    node : FileSystemNode
        The current directory or file node being processed.
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.
    stats : FileSystemStats
        Statistics tracking object for the total file count and size.
    include_files : set[str] | None, optional
        Relative file paths that should be processed. If ``None``, all files are
        considered.
    include_dirs : set[str] | None, optional
        Relative directory paths that should be traversed. If ``None``, all
        directories are considered.

    """
    if limit_exceeded(stats, depth=node.depth):
        return

    for sub_path in node.path.iterdir():
        if query.ignore_patterns and _should_exclude(sub_path, query.local_path, query.ignore_patterns):
            continue

        rel = sub_path.relative_to(query.local_path).as_posix()

        if include_dirs is not None or include_files is not None:
            if sub_path.is_dir():
                if include_dirs is not None and rel not in include_dirs:
                    continue
            elif include_files is not None and rel not in include_files:
                continue
        elif query.include_patterns and not _should_include(sub_path, query.local_path, query.include_patterns):
            continue

        if sub_path.is_symlink():
            _process_symlink(path=sub_path, parent_node=node, stats=stats, local_path=query.local_path)
        elif sub_path.is_file():
            if sub_path.stat().st_size > query.max_file_size:
                print(f"Skipping file {sub_path}: would exceed max file size limit")
                continue
            _process_file(path=sub_path, parent_node=node, stats=stats, local_path=query.local_path)
        elif sub_path.is_dir():
            child_directory_node = FileSystemNode(
                name=sub_path.name,
                type=FileSystemNodeType.DIRECTORY,
                path_str=str(sub_path.relative_to(query.local_path)),
                path=sub_path,
                depth=node.depth + 1,
            )

            _process_node(
                node=child_directory_node,
                query=query,
                stats=stats,
                include_files=include_files,
                include_dirs=include_dirs,
            )

            if not child_directory_node.children:
                continue

            node.children.append(child_directory_node)
            node.size += child_directory_node.size
            node.file_count += child_directory_node.file_count
            node.dir_count += 1 + child_directory_node.dir_count
        else:
            print(f"Warning: {sub_path} is an unknown file type, skipping")

    node.sort_children()


def _process_symlink(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path) -> None:
    """Process a symlink in the file system.

    This function checks the symlink's target.

    Parameters
    ----------
    path : Path
        The full path of the symlink.
    parent_node : FileSystemNode
        The parent directory node.
    stats : FileSystemStats
        Statistics tracking object for the total file count and size.
    local_path : Path
        The base path of the repository or directory being processed.

    """
    child = FileSystemNode(
        name=path.name,
        type=FileSystemNodeType.SYMLINK,
        path_str=str(path.relative_to(local_path)),
        path=path,
        depth=parent_node.depth + 1,
    )
    stats.total_files += 1
    parent_node.children.append(child)
    parent_node.file_count += 1


def _process_file(path: Path, parent_node: FileSystemNode, stats: FileSystemStats, local_path: Path) -> None:
    """Process a file in the file system.

    This function checks the file's size, increments the statistics, and reads its content.
    If the file size exceeds the maximum allowed, it raises an error.

    Parameters
    ----------
    path : Path
        The full path of the file.
    parent_node : FileSystemNode
        The dictionary to accumulate the results.
    stats : FileSystemStats
        Statistics tracking object for the total file count and size.
    local_path : Path
        The base path of the repository or directory being processed.

    """
    if stats.total_files + 1 > MAX_FILES:
        print(f"Maximum file limit ({MAX_FILES}) reached")
        return

    file_size = path.stat().st_size
    if stats.total_size + file_size > MAX_TOTAL_SIZE_BYTES:
        print(f"Skipping file {path}: would exceed total size limit")
        return

    stats.total_files += 1
    stats.total_size += file_size

    child = FileSystemNode(
        name=path.name,
        type=FileSystemNodeType.FILE,
        size=file_size,
        file_count=1,
        path_str=str(path.relative_to(local_path)),
        path=path,
        depth=parent_node.depth + 1,
    )

    parent_node.children.append(child)
    parent_node.size += file_size
    parent_node.file_count += 1


def limit_exceeded(stats: FileSystemStats, depth: int) -> bool:
    """Check if any of the traversal limits have been exceeded.

    This function checks if the current traversal has exceeded any of the configured limits:
    maximum directory depth, maximum number of files, or maximum total size in bytes.

    Parameters
    ----------
    stats : FileSystemStats
        Statistics tracking object for the total file count and size.
    depth : int
        The current depth of directory traversal.

    Returns
    -------
    bool
        ``True`` if any limit has been exceeded, ``False`` otherwise.

    """
    if depth > MAX_DIRECTORY_DEPTH:
        print(f"Maximum depth limit ({MAX_DIRECTORY_DEPTH}) reached")
        return True

    if stats.total_files >= MAX_FILES:
        print(f"Maximum file limit ({MAX_FILES}) reached")
        return True  # TODO: end recursion

    if stats.total_size >= MAX_TOTAL_SIZE_BYTES:
        print(f"Maxumum total size limit ({MAX_TOTAL_SIZE_BYTES / 1024 / 1024:.1f}MB) reached")
        return True  # TODO: end recursion

    return False
