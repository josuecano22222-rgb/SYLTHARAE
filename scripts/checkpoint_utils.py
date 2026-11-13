"""
Checkpoint Utilities - Management tools for checkpoint files

Provides utilities for managing, validating, and cleaning up checkpoint files.
"""

import os
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime


def list_checkpoints(checkpoint_dir: str = "data/checkpoints") -> List[Dict[str, Any]]:
    """
    List all checkpoint files with their metadata.
    
    Args:
        checkpoint_dir: Directory containing checkpoint files
        
    Returns:
        List of checkpoint metadata dictionaries
    """
    checkpoint_path = Path(checkpoint_dir)
    if not checkpoint_path.exists():
        return []
    
    checkpoints = []
    for checkpoint_file in checkpoint_path.glob("checkpoint_*.json"):
        try:
            with open(checkpoint_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            checkpoints.append({
                'file': str(checkpoint_file),
                'folder_path': data.get('folder_path', 'unknown'),
                'storage_source': data.get('storage_source', 'unknown'),
                'storage_side': data.get('storage_side', 'unknown'),
                'processed_count': data.get('processed_count', 0),
                'last_updated': data.get('last_updated', 'unknown'),
                'size': checkpoint_file.stat().st_size
            })
        except Exception as e:
            checkpoints.append({
                'file': str(checkpoint_file),
                'error': str(e)
            })
    
    return checkpoints


def validate_checkpoint(checkpoint_file: str) -> Dict[str, Any]:
    """
    Validate a checkpoint file.
    
    Args:
        checkpoint_file: Path to checkpoint file
        
    Returns:
        Validation result dictionary
    """
    result = {
        'valid': False,
        'errors': [],
        'warnings': [],
        'info': {}
    }
    
    checkpoint_path = Path(checkpoint_file)
    
    if not checkpoint_path.exists():
        result['errors'].append(f"Checkpoint file does not exist: {checkpoint_file}")
        return result
    
    try:
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Check required fields
        required_fields = ['folder_path', 'processed_files', 'processed_count', 'last_updated']
        for field in required_fields:
            if field not in data:
                result['errors'].append(f"Missing required field: {field}")
        
        # Validate folder path exists
        folder_path = data.get('folder_path', '')
        if folder_path:
            if not Path(folder_path).exists():
                result['warnings'].append(f"Folder path does not exist: {folder_path}")
        
        # Validate processed_files is a list
        processed_files = data.get('processed_files', [])
        if not isinstance(processed_files, list):
            result['errors'].append("processed_files must be a list")
        
        # Check consistency
        processed_count = data.get('processed_count', 0)
        if len(processed_files) != processed_count:
            result['warnings'].append(
                f"Count mismatch: processed_count={processed_count}, "
                f"but processed_files has {len(processed_files)} items"
            )
        
        # If no errors, mark as valid
        if not result['errors']:
            result['valid'] = True
        
        result['info'] = {
            'folder_path': folder_path,
            'storage_source': data.get('storage_source'),
            'storage_side': data.get('storage_side'),
            'processed_count': processed_count,
            'processed_files_count': len(processed_files),
            'last_updated': data.get('last_updated'),
            'version': data.get('version', 'unknown')
        }
        
    except json.JSONDecodeError as e:
        result['errors'].append(f"Invalid JSON: {e}")
    except Exception as e:
        result['errors'].append(f"Error reading checkpoint: {e}")
    
    return result


def delete_checkpoint(checkpoint_file: str) -> bool:
    """
    Delete a checkpoint file.
    
    Args:
        checkpoint_file: Path to checkpoint file
        
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        checkpoint_path = Path(checkpoint_file)
        if checkpoint_path.exists():
            checkpoint_path.unlink()
            return True
        return False
    except Exception as e:
        print(f"Error deleting checkpoint: {e}")
        return False


def cleanup_old_checkpoints(checkpoint_dir: str = "data/checkpoints", days: int = 30) -> int:
    """
    Clean up checkpoint files older than specified days.
    
    Args:
        checkpoint_dir: Directory containing checkpoint files
        days: Delete checkpoints older than this many days
        
    Returns:
        Number of checkpoints deleted
    """
    checkpoint_path = Path(checkpoint_dir)
    if not checkpoint_path.exists():
        return 0
    
    deleted_count = 0
    cutoff_time = datetime.now().timestamp() - (days * 24 * 60 * 60)
    
    for checkpoint_file in checkpoint_path.glob("checkpoint_*.json"):
        try:
            # Check file modification time
            if checkpoint_file.stat().st_mtime < cutoff_time:
                checkpoint_file.unlink()
                deleted_count += 1
        except Exception as e:
            print(f"Error deleting {checkpoint_file}: {e}")
    
    return deleted_count


def main():
    """CLI entry point for checkpoint utilities"""
    parser = argparse.ArgumentParser(description="Checkpoint management utilities")
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List all checkpoints')
    list_parser.add_argument('--dir', default='data/checkpoints', help='Checkpoint directory')
    
    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate a checkpoint file')
    validate_parser.add_argument('checkpoint_file', help='Path to checkpoint file')
    
    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Delete a checkpoint file')
    delete_parser.add_argument('checkpoint_file', help='Path to checkpoint file')
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old checkpoints')
    cleanup_parser.add_argument('--dir', default='data/checkpoints', help='Checkpoint directory')
    cleanup_parser.add_argument('--days', type=int, default=30, help='Delete checkpoints older than N days')
    
    args = parser.parse_args()
    
    if args.command == 'list':
        checkpoints = list_checkpoints(args.dir)
        if not checkpoints:
            print(f"No checkpoints found in {args.dir}")
        else:
            print(f"\nFound {len(checkpoints)} checkpoint(s):\n")
            for cp in checkpoints:
                if 'error' in cp:
                    print(f"  ❌ {cp['file']}: {cp['error']}")
                else:
                    print(f"  📄 {Path(cp['file']).name}")
                    print(f"     Folder: {cp['folder_path']}")
                    print(f"     Source: {cp['storage_source']}, Side: {cp['storage_side']}")
                    print(f"     Processed: {cp['processed_count']} files")
                    print(f"     Last updated: {cp['last_updated']}")
                    print(f"     Size: {cp['size']} bytes")
                    print()
    
    elif args.command == 'validate':
        result = validate_checkpoint(args.checkpoint_file)
        print(f"\nValidation result for {args.checkpoint_file}:")
        if result['valid']:
            print("  ✅ Valid checkpoint")
        else:
            print("  ❌ Invalid checkpoint")
        
        if result['errors']:
            print("\n  Errors:")
            for error in result['errors']:
                print(f"    - {error}")
        
        if result['warnings']:
            print("\n  Warnings:")
            for warning in result['warnings']:
                print(f"    - {warning}")
        
        if result['info']:
            print("\n  Info:")
            for key, value in result['info'].items():
                print(f"    {key}: {value}")
    
    elif args.command == 'delete':
        if delete_checkpoint(args.checkpoint_file):
            print(f"✅ Deleted checkpoint: {args.checkpoint_file}")
        else:
            print(f"❌ Failed to delete checkpoint: {args.checkpoint_file}")
    
    elif args.command == 'cleanup':
        deleted = cleanup_old_checkpoints(args.dir, args.days)
        print(f"✅ Deleted {deleted} checkpoint(s) older than {args.days} days")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
