
from pathlib import Path
import argparse
import shutil
from collections import Counter

# File extensions mapped to descriptive folder names.
FILE_TYPES = {
    "Images": {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
        ".webp", ".svg", ".ico", ".heic", ".raw", ".avif"
    },
    "Videos": {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
        ".m4v", ".mpeg", ".mpg", ".3gp"
    },
    "Audio": {
        ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
        ".opus", ".aiff", ".mid", ".midi"
    },
    "Documents": {
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".tex",
        ".epub", ".mobi"
    },
    "Text": {
        ".txt", ".md", ".rst", ".log", ".nfo", ".readme"
    },
    "Spreadsheets": {
        ".xls", ".xlsx", ".xlsm", ".ods", ".csv", ".tsv"
    },
    "Presentations": {
        ".ppt", ".pptx", ".odp", ".key"
    },
    "Archives": {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
        ".tgz", ".zst", ".cab", ".iso"
    },
    "Python": {".py", ".pyw", ".pyi"},
    "Programming": {
        ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css",
        ".scss", ".java", ".c", ".h", ".cpp", ".hpp", ".cs",
        ".go", ".rs", ".php", ".rb", ".swift", ".kt", ".sql",
        ".sh", ".bat", ".cmd", ".ps1", ".vue", ".svelte"
    },
    "Data": {
        ".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".conf", ".db", ".sqlite", ".sqlite3",
        ".parquet", ".avro", ".ndjson"
    },
    "Executables": {
        ".exe", ".msi", ".dll", ".sys", ".com", ".app",
        ".deb", ".rpm", ".apk"
    },
    "Fonts": {
        ".ttf", ".otf", ".woff", ".woff2", ".eot"
    },
    "Design": {
        ".psd", ".ai", ".eps", ".indd", ".sketch", ".fig",
        ".blend", ".obj", ".stl", ".fbx", ".dwg", ".dxf"
    },
}


def get_category(path: Path) -> str:
    """Return the destination folder for a file."""
    extension = path.suffix.lower()

    for category, extensions in FILE_TYPES.items():
        if extension in extensions:
            return category

    # Files without an extension or with unknown extensions.
    return "Other"


def unique_destination(path: Path) -> Path:
    """Choose a destination without overwriting existing files."""
    if not path.exists():
        return path

    number = 1
    while True:
        candidate = path.with_name(
            f"{path.stem} ({number}){path.suffix}"
        )
        if not candidate.exists():
            return candidate
        number += 1


def organize(source: Path, apply: bool = False) -> None:
    source = source.resolve()

    if not source.is_dir():
        raise NotADirectoryError(f"Directory not found: {source}")

    # Collect files before moving anything.
    # Only scan the source tree, excluding symbolic-link directories.
    files = []
    for current, directories, filenames in __import__("os").walk(
        source, followlinks=False
    ):
        current_path = Path(current)

        # Destination folders are directly inside the source.
        # Skip them on subsequent runs to prevent rescanning
        # files already organized by this script.
        if current_path == source:
            directories[:] = [
                d for d in directories
                if not (source / d).is_symlink()
                and not (source / d).is_file()
            ]

        for filename in filenames:
            file_path = current_path / filename

            if file_path.is_symlink():
                continue

            if file_path.is_file():
                files.append(file_path)

    counts = Counter()
    moved = 0
    errors = 0

    print(f"\nSource: {source}")
    print(f"Mode: {'APPLY CHANGES' if apply else 'DRY RUN'}")
    print(f"Files found: {len(files)}\n")

    for file_path in files:
        category = get_category(file_path)
        destination_folder = source / category
        destination = unique_destination(
            destination_folder / file_path.name
        )

        try:
            if apply:
                destination_folder.mkdir(
                    parents=True, exist_ok=True
                )
                shutil.move(str(file_path), str(destination))
                moved += 1

            counts[category] += 1
            action = "MOVED" if apply else "WOULD MOVE"
            print(
                f"[{action}] {file_path.relative_to(source)}"
                f" -> {category}/{destination.name}"
            )

        except OSError as error:
            errors += 1
            print(f"[ERROR] {file_path}: {error}")

    print("\n--- Summary ---")
    print(f"Files scanned: {len(files)}")
    print(f"Files moved:   {moved}")
    print(f"Errors:        {errors}")
    print("\nFiles by category:")

    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")

    if not apply:
        print("\nDry run only. No files were moved.")
        print("Review the output, then rerun with --apply.")


def main():
    parser = argparse.ArgumentParser(
        description="Organize files into type-based subfolders."
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory to scan and organize"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually move files; default is a dry run"
    )
    args = parser.parse_args()

    try:
        organize(args.directory, apply=args.apply)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
