"""
Cleanup utility for removing Python cache files.

This script removes all __pycache__ directories and .pyc files from the project.
Useful for cleaning up before commits or when cache files cause issues.

Usage:
    python scripts/cleanup_cache.py
    
Note: This is safe to run - it only removes cache files, not source code.
"""
import os
import shutil
from pathlib import Path


def delete_pycache(directory: str = '.') -> None:
    """
    Recursively delete all __pycache__ directories and their contents.
    python -m venv env
    env\Scripts\activate


    Args:
        directory: Root directory to search (default: current directory)
    """
    deleted_count = 0
    deleted_size = 0
    
    # Walk through the directory
    for root, dirs, files in os.walk(directory, topdown=False):
        # Skip .git directory to avoid issues
        if '.git' in root:
            continue
            
        for name in dirs:
            # Check if the folder is named '__pycache__'
            if name == '__pycache__':
                pycache_path = os.path.join(root, name)
                print(f"Deleting: {pycache_path}")
                
                try:
                    # Calculate size before deletion
                    for file in os.listdir(pycache_path):
                        file_path = os.path.join(pycache_path, file)
                        if os.path.isfile(file_path):
                            deleted_size += os.path.getsize(file_path)
                    
                    # Delete all files inside __pycache__
                    for file in os.listdir(pycache_path):
                        file_path = os.path.join(pycache_path, file)
                        try:
                            if os.path.isfile(file_path):
                                os.remove(file_path)
                            elif os.path.isdir(file_path):
                                shutil.rmtree(file_path)
                        except Exception as e:
                            print(f"  Warning: Error deleting {file_path}: {e}")
                    
                    # Remove the __pycache__ directory itself
                    os.rmdir(pycache_path)
                    deleted_count += 1
                except Exception as e:
                    print(f"  Error deleting {pycache_path}: {e}")
    
    # Also delete .pyc files in the root
    for root, dirs, files in os.walk(directory):
        if '.git' in root:
            continue
        for file in files:
            if file.endswith('.pyc'):
                file_path = os.path.join(root, file)
                try:
                    deleted_size += os.path.getsize(file_path)
                    os.remove(file_path)
                    print(f"Deleted: {file_path}")
                except Exception as e:
                    print(f"  Warning: Error deleting {file_path}: {e}")
    
    print(f"\n✅ Cleanup complete!")
    print(f"   Deleted {deleted_count} __pycache__ directories")
    print(f"   Freed {deleted_size / 1024:.2f} KB")


if __name__ == '__main__':
    # Get project root (parent of scripts directory)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    print("=" * 60)
    print("Python Cache Cleanup Utility")
    print("=" * 60)
    print(f"Cleaning: {project_root}")
    print()
    
    delete_pycache(str(project_root))

