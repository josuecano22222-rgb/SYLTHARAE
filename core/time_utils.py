"""
Time utilities - Independent functions for time measurement and statistics
No dependencies on other project modules (except logging_utils for recording).
"""

import os
import sys
import time
from typing import List, Dict, Any, Callable, Optional



# Import logging function (lazy import to avoid circular dependency)
def _get_record_function():
    """Lazy import to avoid circular dependency"""
    try:
        from Hdg_Err_Ex_Log import record_command_line_action
        return record_command_line_action
    except ImportError:
        # Fallback if logging not available
        return lambda *args, **kwargs: None


def print_execution_time(description: str, func: Callable, *args, **kwargs) -> Any:
    """
    Execute a function and print execution time.
    
    Args:
        description: Description of the task
        func: Function to execute
        *args: Positional arguments for function
        **kwargs: Keyword arguments for function
        
    Returns:
        Result from function execution
    """
    start_time = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        return result
    finally:
        elapsed = time.perf_counter() - start_time
        
        # Choose unit based on elapsed time
        if elapsed < 1e-3:
            time_str = f"{elapsed * 1e6:.2f} microseconds"
            time_value = elapsed * 1e6
            time_unit = "microseconds"
        elif elapsed < 1:
            time_str = f"{elapsed * 1e3:.2f} milliseconds"
            time_value = elapsed * 1e3
            time_unit = "milliseconds"
        else:
            time_str = f"{elapsed:.2f} seconds"
            time_value = elapsed
            time_unit = "seconds"
        
        # Neat printing: ONE atomic block so neither a report from another
        # worker thread nor a log record can land between the lines when
        # stdout and stderr are captured together, and ANSI colour only on
        # interactive terminals (redirected logs stay clean).
        try:
            use_color = sys.stdout.isatty()
        except Exception:
            use_color = False
        cyan = "\033[36m" if use_color else ""
        reset = "\033[0m" if use_color else ""

        from core.console import console
        console.block([
            f"{'-' * 40}",
            f"Task: {description}",
            f"Elapsed time: {cyan}{time_str}{reset}",
            f"{'-' * 40}",
        ])
        
        # Record execution time to log
        _get_record_function()(
            "EXECUTION_TIME",
            f"Task completed: {description}",
            {
                "task": description,
                "elapsed_time": time_value,
                "time_unit": time_unit,
                "elapsed_seconds": elapsed
            }
        )


def calculate_processing_statistics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate and display processing statistics.
    
    Args:
        results: List of processing result dictionaries
        
    Returns:
        Dictionary with calculated metrics
    """
    if not results:
        return {
            "total_files": 0,
            "successful": 0,
            "failed": 0,
            "success_rate": 0.0,
            "file_types": {},
            "total_processing_time": 0.0,
            "average_time_per_file": 0.0
        }
    
    successful = 0
    failed = 0
    file_types = {}
    total_processing_time = 0.0
    
    for result in results:
        if result:
            # Count success/failure
            content = result.get("Content", {})
            if isinstance(content, dict) and "error" not in content:
                successful += 1
            else:
                failed += 1
            
            # Track file types
            file_path = result.get("File_Path") or result.get("Metadata", {}).get("path", "unknown")
            extension = os.path.splitext(file_path)[1].lower() or "no_extension"
            file_types[extension] = file_types.get(extension, 0) + 1
            
            # Sum processing times
            processing_time = result.get("Processing_Time", 0)
            if isinstance(processing_time, (int, float)):
                total_processing_time += processing_time
    
    total_files = len(results)
    success_rate = (successful / total_files * 100) if total_files > 0 else 0.0
    average_time = total_processing_time / total_files if total_files > 0 else 0.0
    
    stats = {
        "total_files": total_files,
        "successful": successful,
        "failed": failed,
        "success_rate": success_rate,
        "file_types": file_types,
        "total_processing_time": total_processing_time,
        "average_time_per_file": average_time
    }
    
    # Display statistics
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"
    
    print(f"\n{'='*70}")
    print(f"{CYAN}📊 PROCESSING STATISTICS{RESET}")
    print(f"{'='*70}")
    print(f"Total Files:           {stats['total_files']}")
    print(f"{GREEN}Successful:            {stats['successful']}{RESET}")
    print(f"{RED}Failed:                {stats['failed']}{RESET}")
    print(f"Success Rate:          {YELLOW}{stats['success_rate']:.1f}%{RESET}")
    print(f"Total Processing Time: {stats['total_processing_time']:.2f} seconds")
    print(f"Average Time/File:     {stats['average_time_per_file']:.4f} seconds")
    
    if file_types:
        print(f"\n{CYAN}File Type Distribution:{RESET}")
        sorted_types = sorted(file_types.items(), key=lambda x: x[1], reverse=True)
        for ext, count in sorted_types[:10]:  # Show top 10
            percentage = (count / total_files * 100) if total_files > 0 else 0
            print(f"  {ext or '(no extension)':<15} {count:>4} files ({percentage:>5.1f}%)")
        if len(sorted_types) > 10:
            print(f"  ... and {len(sorted_types) - 10} more types")
    
    print(f"{'='*70}\n")
    
    # Record processing statistics to log
    _get_record_function()(
        "PROCESSING_STATISTICS",
        "Processing statistics calculated",
        {
            "total_files": stats['total_files'],
            "successful": stats['successful'],
            "failed": stats['failed'],
            "success_rate": stats['success_rate'],
            "file_types": stats['file_types'],
            "total_processing_time": stats['total_processing_time'],
            "average_time_per_file": stats['average_time_per_file']
        }
    )
    
    return stats


def calculate_file_processing_metrics(file_info: Dict[str, Any], 
                                      result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Calculate and display metrics for individual file processing.
    
    Args:
        file_info: File information dictionary
        result: Processing result dictionary
        
    Returns:
        Dictionary with metrics or None
    """
    if not result:
        return None
    
    # Get file path from result structure
    metadata = result.get("Metadata", {})
    file_path = metadata.get("path") or result.get("File_Path") or file_info.get("path", "unknown")
    file_name = metadata.get("name") or os.path.basename(file_path) if file_path != "unknown" else "unknown"
    
    # Get processing time from metadata or result
    processing_time_str = metadata.get("processing_time", "N/A")
    if isinstance(processing_time_str, str) and "seconds" in processing_time_str:
        try:
            processing_time = float(processing_time_str.replace("seconds", "").strip())
        except (ValueError, AttributeError):
            processing_time = result.get("Processing_Time", 0)
    else:
        processing_time = result.get("Processing_Time", 0)
    
    content = result.get("Content", {})
    if not isinstance(content, dict):
        content = {}

    # A reader reports a failure either at the top of its payload or inside
    # ``extraction_info`` - ``read_img_fast`` does the latter - and the router
    # passes a reader's own payload straight through. Checking only the top
    # level printed "✓ Success" for a file whose read had just failed with
    # "cannot identify image file" in the same block of output.
    content_info = content.get("extraction_info")
    has_error = False
    error_text = None
    for layer in (content, content_info if isinstance(content_info, dict) else None,
                  result):
        if isinstance(layer, dict) and layer.get("error"):
            has_error = True
            error_text = str(layer["error"])
            break
    
    # Calculate file size if available
    file_size = 0
    if file_info:
        size_bytes = file_info.get('size_bytes')
        if size_bytes is not None:
            try:
                file_size = int(size_bytes)
            except (ValueError, TypeError):
                file_size = 0
    
    # Format file size
    if file_size < 1024:
        size_str = f"{file_size} B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.2f} KB"
    else:
        size_str = f"{file_size / (1024 * 1024):.2f} MB"
    
    # Display metrics
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"
    
    status = f"{GREEN}✓ Success{RESET}" if not has_error else f"{RED}✗ Failed{RESET}"
    
    # Emitted as one atomic block: this report is written from every worker
    # thread, and printing it line by line let another thread's progress or
    # metrics output land in the middle of it.
    from core.console import console
    metric_lines = [
        f"\n{YELLOW}📈 File Metrics:{RESET}",
        f"  File:     {file_name}",
        f"  Size:     {size_str}",
        f"  Status:   {status}",
        f"  Time:     {processing_time:.4f} seconds",
    ]
    if has_error:
        metric_lines.append(f"  Error:    {error_text or 'Unknown error'}")
    metric_lines.append("")
    console.block(metric_lines)
    
    # Record file metrics to log
    _get_record_function()(
        "FILE_METRICS",
        f"File processed: {file_name}",
        {
            "file_path": file_path,
            "file_name": file_name,
            "file_size_bytes": file_size,
            "file_size_formatted": size_str,
            "processing_time": processing_time,
            "status": "Success" if not has_error else "Failed",
            "success": not has_error,
            "error": content.get('error') if has_error else None,
            "extension": metadata.get("extension", "unknown"),
            "file_type": metadata.get("type", "unknown")
        },
        level="INFO" if not has_error else "ERROR"
    )
    
    return {
        "file_path": file_path,
        "file_size": file_size,
        "processing_time": processing_time,
        "success": not has_error
    }

