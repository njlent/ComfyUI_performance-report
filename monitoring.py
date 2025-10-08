"""
ComfyUI ExecutionTimeReporter Monitoring System
Handles execution time tracking, memory monitoring, and report generation.
"""

import os
import json
import time
import psutil
import gc
from datetime import datetime
import execution
import server

# Try to import torch for GPU memory tracking
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    print("[ExecutionTimeReporter] PyTorch not available - GPU memory tracking disabled")

# Try to import comfy model management for more detailed GPU stats
try:
    import comfy.model_management as model_management
    COMFY_MODEL_MGMT_AVAILABLE = True
except ImportError:
    COMFY_MODEL_MGMT_AVAILABLE = False

# Global storage for execution timing data
# Format: {prompt_id: {node_id: {"start_time": float, "end_time": float, "duration": float, "class_type": str, "title": str, "execution_order": int, "memory_stats": dict}}}
execution_timing_data = {}

# Track workflow start times
workflow_start_times = {}

# Track execution order
execution_order_counter = {}

# Store original functions for patching
original_execute = None
original_execute_async = None

def get_gpu_memory_info():
    """Get current GPU memory usage"""
    if not TORCH_AVAILABLE or not torch.cuda.is_available():
        return None
    
    try:
        # Get memory info for the current device
        device = torch.cuda.current_device()
        allocated = torch.cuda.memory_allocated(device) / (1024**3)  # GB
        reserved = torch.cuda.memory_reserved(device) / (1024**3)   # GB
        max_allocated = torch.cuda.max_memory_allocated(device) / (1024**3)  # GB
        
        return {
            "allocated_gb": allocated,
            "reserved_gb": reserved,
            "max_allocated_gb": max_allocated,
            "device": device
        }
    except Exception as e:
        print(f"[ExecutionTimeReporter] Error getting GPU memory: {e}")
        return None

def get_system_memory_info():
    """Get current system memory usage"""
    try:
        process = psutil.Process()
        memory_info = process.memory_info()
        system_memory = psutil.virtual_memory()
        
        return {
            "process_rss_gb": memory_info.rss / (1024**3),
            "process_vms_gb": memory_info.vms / (1024**3),
            "system_used_gb": system_memory.used / (1024**3),
            "system_available_gb": system_memory.available / (1024**3),
            "system_percent": system_memory.percent
        }
    except Exception as e:
        print(f"[ExecutionTimeReporter] Error getting system memory: {e}")
        return None

def get_model_management_info():
    """Get ComfyUI model management info if available"""
    if not COMFY_MODEL_MGMT_AVAILABLE:
        return None
    
    try:
        info = {}
        if hasattr(model_management, 'get_torch_device'):
            info["torch_device"] = str(model_management.get_torch_device())
        if hasattr(model_management, 'get_total_memory'):
            info["total_memory_gb"] = model_management.get_total_memory() / (1024**3)
        if hasattr(model_management, 'get_free_memory'):
            info["free_memory_gb"] = model_management.get_free_memory() / (1024**3)
        return info
    except Exception as e:
        print(f"[ExecutionTimeReporter] Error getting model management info: {e}")
        return None

def get_system_info():
    """Get comprehensive system information"""
    try:
        import platform
        import sys
        
        info = {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "python_version": sys.version.split()[0],
            "python_implementation": platform.python_implementation(),
        }
        
        # CPU Information
        info["cpu_count_physical"] = psutil.cpu_count(logical=False)
        info["cpu_count_logical"] = psutil.cpu_count(logical=True)
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        info["cpu_freq"] = psutil.cpu_freq()
        
        # Memory Information
        memory = psutil.virtual_memory()
        info["total_ram_gb"] = memory.total / (1024**3)
        info["available_ram_gb"] = memory.available / (1024**3)
        info["ram_percent"] = memory.percent
        
        # PyTorch and CUDA Information
        if TORCH_AVAILABLE:
            info["torch_version"] = torch.__version__
            info["cuda_available"] = torch.cuda.is_available()
            if torch.cuda.is_available():
                info["cuda_version"] = torch.version.cuda
                info["cudnn_version"] = torch.backends.cudnn.version()
                info["cuda_device_count"] = torch.cuda.device_count()
                
                # Get info for all GPU devices
                gpu_info = []
                for i in range(torch.cuda.device_count()):
                    props = torch.cuda.get_device_properties(i)
                    gpu_info.append({
                        "name": props.name,
                        "total_memory_gb": props.total_memory / (1024**3),
                        "major": props.major,
                        "minor": props.minor,
                        "multi_processor_count": props.multi_processor_count
                    })
                info["gpu_devices"] = gpu_info
        
        # ComfyUI Version (try to get it)
        try:
            import comfyui_version
            info["comfyui_version"] = comfyui_version.__version__
        except:
            try:
                # Alternative method to get ComfyUI version
                comfyui_path = os.path.join(os.path.dirname(__file__), "..", "..")
                version_file = os.path.join(comfyui_path, "comfyui_version.py")
                if os.path.exists(version_file):
                    with open(version_file, 'r') as f:
                        content = f.read()
                        if '__version__' in content:
                            # Extract version from file
                            for line in content.split('\n'):
                                if '__version__' in line and '=' in line:
                                    version = line.split('=')[1].strip().strip('"\'')
                                    info["comfyui_version"] = version
                                    break
                else:
                    info["comfyui_version"] = "Unknown"
            except:
                info["comfyui_version"] = "Unknown"
        
        # ComfyUI Model Management Info
        if COMFY_MODEL_MGMT_AVAILABLE:
            try:
                if hasattr(model_management, 'get_torch_device'):
                    info["comfy_device"] = str(model_management.get_torch_device())
                if hasattr(model_management, 'get_total_memory'):
                    info["comfy_total_memory_gb"] = model_management.get_total_memory() / (1024**3)
            except:
                pass
        
        return info
    except Exception as e:
        print(f"[ExecutionTimeReporter] Error getting system info: {e}")
        return None

async def patched_execute(server_instance, dynprompt, caches, current_item, extra_data, executed, prompt_id, execution_list, pending_subgraph_results, pending_async_nodes):
    """Patched execute function that tracks timing and memory usage"""
    unique_id = current_item

    # Initialize tracking for this prompt if needed
    if prompt_id not in execution_timing_data:
        execution_timing_data[prompt_id] = {}
        execution_order_counter[prompt_id] = 0
        print(f"[ExecutionTimeReporter] Started tracking workflow {prompt_id}")

    # Get node information
    class_type = dynprompt.get_node(unique_id)["class_type"]
    node_title = f"Node {unique_id}"
    real_node_id = unique_id

    # Increment execution order
    execution_order_counter[prompt_id] += 1
    execution_order = execution_order_counter[prompt_id]

    # Record start time and memory stats
    start_time = time.time()
    start_gpu_memory = get_gpu_memory_info()
    start_system_memory = get_system_memory_info()
    start_model_mgmt = get_model_management_info()

    # Reset GPU memory stats to track peak usage for this node
    if TORCH_AVAILABLE and torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    # Call the original execute function
    result = await original_execute(server_instance, dynprompt, caches, current_item, extra_data, executed, prompt_id, execution_list, pending_subgraph_results, pending_async_nodes)

    # Record end time and memory stats
    end_time = time.time()
    duration = end_time - start_time
    end_gpu_memory = get_gpu_memory_info()
    end_system_memory = get_system_memory_info()
    end_model_mgmt = get_model_management_info()

    # Get peak GPU memory usage during this node's execution
    peak_gpu_memory = None
    if TORCH_AVAILABLE and torch.cuda.is_available():
        device = torch.cuda.current_device()
        peak_allocated = torch.cuda.max_memory_allocated(device) / (1024**3)
        peak_gpu_memory = {"peak_allocated_gb": peak_allocated}

    # Calculate memory deltas
    gpu_allocated_delta_gb = None
    gpu_reserved_delta_gb = None
    system_rss_delta_gb = None
    system_used_delta_gb = None

    if start_gpu_memory and end_gpu_memory:
        gpu_allocated_delta_gb = end_gpu_memory["allocated_gb"] - start_gpu_memory["allocated_gb"]
        gpu_reserved_delta_gb = end_gpu_memory["reserved_gb"] - start_gpu_memory["reserved_gb"]

    if start_system_memory and end_system_memory:
        system_rss_delta_gb = end_system_memory["process_rss_gb"] - start_system_memory["process_rss_gb"]
        system_used_delta_gb = end_system_memory["system_used_gb"] - start_system_memory["system_used_gb"]

    # Store comprehensive memory statistics
    memory_stats = {
        "start_gpu": start_gpu_memory,
        "end_gpu": end_gpu_memory,
        "peak_gpu": peak_gpu_memory,
        "start_system": start_system_memory,
        "end_system": end_system_memory,
        "start_model_mgmt": start_model_mgmt,
        "end_model_mgmt": end_model_mgmt,
        "gpu_allocated_delta_gb": gpu_allocated_delta_gb,
        "gpu_reserved_delta_gb": gpu_reserved_delta_gb,
        "system_rss_delta_gb": system_rss_delta_gb,
        "system_used_delta_gb": system_used_delta_gb
    }

    # Store the timing and memory data
    execution_timing_data[prompt_id][unique_id] = {
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "class_type": class_type,
        "title": node_title,
        "real_node_id": real_node_id,
        "execution_order": execution_order,
        "memory_stats": memory_stats
    }

    print(f"[ExecutionTimeReporter] Node {real_node_id} ({class_type}): {duration:.3f}s [Total nodes tracked: {len(execution_timing_data[prompt_id])}]")

    return result

async def patched_execute_async(self, prompt, prompt_id, extra_data=None, execute_outputs=[]):
    """Patched execute_async function that generates reports when workflows complete"""
    # Record workflow start time
    workflow_start_times[prompt_id] = time.time()

    # Call the original execute_async function
    result = await original_execute_async(self, prompt, prompt_id, extra_data, execute_outputs)

    # Check if this workflow contains an ExecutionTimeReporter node
    has_reporter = False
    if prompt_id in execution_timing_data:
        for node_data in execution_timing_data[prompt_id].values():
            if node_data["class_type"] == "ExecutionTimeReporter":
                has_reporter = True
                break

    # Generate report if we have timing data and a reporter node
    if has_reporter and prompt_id in execution_timing_data:
        node_count = len(execution_timing_data[prompt_id])
        print(f"[ExecutionTimeReporter] Workflow completed - generating final report for {node_count} nodes...")
        generate_execution_report(prompt_id, prompt)

        # Clean up data for this prompt
        if prompt_id in execution_timing_data:
            del execution_timing_data[prompt_id]
        if prompt_id in workflow_start_times:
            del workflow_start_times[prompt_id]
        if prompt_id in execution_order_counter:
            del execution_order_counter[prompt_id]

    return result

def generate_execution_report(prompt_id, _dynprompt):
    """Generate and save the execution time report"""
    if prompt_id not in execution_timing_data:
        print("[ExecutionTimeReporter] No timing data found for this workflow.")
        return

    timing_data = execution_timing_data[prompt_id]

    # Calculate total times
    total_node_time = sum(data["duration"] for data in timing_data.values())
    workflow_start_time = workflow_start_times.get(prompt_id, time.time())
    total_workflow_time = time.time() - workflow_start_time

    # Prepare report data
    report_entries = []
    for unique_id, data in timing_data.items():
        duration = data["duration"]

        report_entries.append({
            "title": data["title"],
            "class_type": data["class_type"],
            "duration": duration,
            "real_node_id": data["real_node_id"],
            "unique_id": unique_id,
            "execution_order": data["execution_order"],
            "memory_stats": data.get("memory_stats", {})
        })

    # Sort by execution order for chronological view
    report_entries.sort(key=lambda x: x["execution_order"])

    # Create performance analysis (sorted by duration)
    performance_entries = sorted(report_entries, key=lambda x: x["duration"], reverse=True)

    # Generate report
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_filename = f"execution_report_{timestamp}.txt"

    # Ensure output directory exists
    output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "output", "reports")
    os.makedirs(output_dir, exist_ok=True)

    report_path = os.path.join(output_dir, report_filename)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("ComfyUI Workflow Execution Time & Memory Report\n")
        f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Prompt ID: {prompt_id}\n")
        f.write("="*80 + "\n")

        # Add comprehensive system information
        system_info = get_system_info()
        if system_info:
            f.write("\nSYSTEM INFORMATION:\n")
            f.write("-" * 80 + "\n")

            # Platform and OS
            f.write(f"Operating System: {system_info.get('platform', 'Unknown')} {system_info.get('platform_release', '')}\n")
            f.write(f"Architecture: {system_info.get('architecture', 'Unknown')}\n")
            f.write(f"Processor: {system_info.get('processor', 'Unknown')}\n")

            # Python Information
            f.write(f"Python Version: {system_info.get('python_version', 'Unknown')} ({system_info.get('python_implementation', 'Unknown')})\n")

            # ComfyUI Information
            f.write(f"ComfyUI Version: {system_info.get('comfyui_version', 'Unknown')}\n")
            if system_info.get('comfy_device'):
                f.write(f"ComfyUI Device: {system_info['comfy_device']}\n")

            # CPU Information
            cpu_physical = system_info.get('cpu_count_physical', 'Unknown')
            cpu_logical = system_info.get('cpu_count_logical', 'Unknown')
            f.write(f"CPU Cores: {cpu_physical} physical, {cpu_logical} logical\n")
            f.write(f"CPU Usage: {system_info.get('cpu_percent', 0):.1f}%\n")

            cpu_freq = system_info.get('cpu_freq')
            if cpu_freq:
                f.write(f"CPU Frequency: {cpu_freq.current:.0f} MHz (max: {cpu_freq.max:.0f} MHz)\n")

            # Memory Information
            total_ram = system_info.get('total_ram_gb', 0)
            available_ram = system_info.get('available_ram_gb', 0)
            ram_percent = system_info.get('ram_percent', 0)
            f.write(f"System RAM: {total_ram:.1f} GB total, {available_ram:.1f} GB available ({ram_percent:.1f}% used)\n")

            # PyTorch and CUDA Information
            if system_info.get('torch_version'):
                f.write(f"PyTorch Version: {system_info['torch_version']}\n")

                if system_info.get('cuda_available'):
                    f.write(f"CUDA Version: {system_info.get('cuda_version', 'Unknown')}\n")
                    f.write(f"cuDNN Version: {system_info.get('cudnn_version', 'Unknown')}\n")

                    gpu_devices = system_info.get('gpu_devices', [])
                    f.write(f"GPU Devices: {len(gpu_devices)}\n")

                    for i, gpu in enumerate(gpu_devices):
                        f.write(f"  GPU {i}: {gpu['name']}\n")
                        f.write(f"    VRAM: {gpu['total_memory_gb']:.1f} GB\n")
                        f.write(f"    Compute Capability: {gpu['major']}.{gpu['minor']}\n")
                        f.write(f"    Multiprocessors: {gpu['multi_processor_count']}\n")

                        # Current GPU memory usage
                        if i == 0:  # Show current usage for primary GPU
                            current_gpu = get_gpu_memory_info()
                            if current_gpu:
                                f.write(f"    Current Usage: {current_gpu['allocated_gb']:.2f} GB allocated, {current_gpu['reserved_gb']:.2f} GB reserved\n")
                else:
                    f.write(f"CUDA: Not available\n")

            if system_info.get('comfy_total_memory_gb'):
                f.write(f"ComfyUI Memory Pool: {system_info['comfy_total_memory_gb']:.1f} GB\n")

        f.write("="*80 + "\n\n")

        # Execution timeline
        f.write("EXECUTION TIMES (in execution order):\n")
        f.write("-" * 80 + "\n")

        for entry in report_entries:
            f.write(f"#{entry['execution_order']:>2} Node {entry['real_node_id']:>3}: {entry['duration']:>8.3f}s - {entry['class_type']}")
            if entry['title'] != f"Node {entry['real_node_id']}":
                f.write(f" ({entry['title']})")

            # Add memory info if available
            mem_stats = entry.get('memory_stats', {})
            memory_info = []

            if mem_stats.get('gpu_allocated_delta_gb') is not None:
                delta = mem_stats['gpu_allocated_delta_gb']
                if abs(delta) > 0.001:  # Only show if change > 1MB
                    sign = "+" if delta > 0 else ""
                    memory_info.append(f"GPU: {sign}{delta:.3f}GB")

            if mem_stats.get('peak_gpu') and mem_stats['peak_gpu'].get('peak_allocated_gb'):
                peak = mem_stats['peak_gpu']['peak_allocated_gb']
                if peak > 0.001:
                    memory_info.append(f"Peak: {peak:.3f}GB")

            if mem_stats.get('system_rss_delta_gb') is not None:
                delta = mem_stats['system_rss_delta_gb']
                if abs(delta) > 0.01:  # Only show if change > 10MB
                    sign = "+" if delta > 0 else ""
                    memory_info.append(f"RAM: {sign}{delta:.3f}GB")

            if memory_info:
                f.write(f" [{', '.join(memory_info)}]")

            f.write("\n")

        # Summary statistics
        f.write("\n" + "="*80 + "\n")
        f.write("SUMMARY:\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total Node Execution Time: {total_node_time:.3f}s\n")
        f.write(f"Total Workflow Time: {total_workflow_time:.3f}s\n")
        f.write(f"Number of Nodes: {len(report_entries)}\n")
        f.write(f"Average Time per Node: {total_node_time/len(report_entries):.3f}s\n")

        # Calculate overhead
        overhead = total_workflow_time - total_node_time
        if overhead > 0:
            f.write(f"ComfyUI Overhead: {overhead:.3f}s ({(overhead/total_workflow_time)*100:.1f}%)\n")

        # Memory summary
        total_gpu_allocated = sum(entry['memory_stats'].get('gpu_allocated_delta_gb', 0) for entry in report_entries)
        total_system_rss = sum(entry['memory_stats'].get('system_rss_delta_gb', 0) for entry in report_entries)
        max_peak_gpu = max((entry['memory_stats'].get('peak_gpu', {}).get('peak_allocated_gb', 0) for entry in report_entries), default=0)

        f.write(f"\nMEMORY SUMMARY:\n")
        f.write(f"Total GPU Memory Change: {total_gpu_allocated:+.3f}GB\n")
        f.write(f"Total System Memory Change: {total_system_rss:+.3f}GB\n")
        f.write(f"Peak GPU Memory Usage: {max_peak_gpu:.3f}GB\n")

        # Performance analysis
        f.write("\n" + "="*80 + "\n")
        f.write("PERFORMANCE ANALYSIS (sorted by duration, slowest first):\n")
        f.write("-" * 80 + "\n")

        for i, entry in enumerate(performance_entries, 1):
            percentage = (entry["duration"] / total_node_time) * 100 if total_node_time > 0 else 0
            f.write(f"{i:>2}. Node {entry['real_node_id']:>3}: {entry['duration']:>8.3f}s ({percentage:>5.1f}%) - {entry['class_type']}\n")

        f.write("\n" + "-" * 80 + "\n")
        if len(performance_entries) > 0:
            slowest = performance_entries[0]
            f.write(f"Slowest Node: {slowest['class_type']} ({slowest['duration']:.3f}s)\n")

            # Find nodes taking more than 10% of total node time
            significant_nodes = [e for e in performance_entries if e['duration'] > total_node_time * 0.1]
            if significant_nodes:
                f.write(f"Nodes taking >10% of total node time: {len(significant_nodes)}\n")

        # Memory analysis section
        f.write("\n" + "="*80 + "\n")
        f.write("MEMORY ANALYSIS:\n")
        f.write("-" * 80 + "\n")

        # GPU memory analysis
        gpu_nodes = [e for e in report_entries if e['memory_stats'].get('gpu_allocated_delta_gb') is not None]
        if gpu_nodes:
            f.write("GPU Memory Changes (nodes with significant changes):\n")
            gpu_sorted = sorted(gpu_nodes, key=lambda x: abs(x['memory_stats'].get('gpu_allocated_delta_gb', 0)), reverse=True)
            for entry in gpu_sorted[:10]:  # Top 10
                delta = entry['memory_stats']['gpu_allocated_delta_gb']
                if abs(delta) > 0.001:  # > 1MB
                    sign = "+" if delta > 0 else ""
                    f.write(f"  Node {entry['real_node_id']:>3}: {sign}{delta:>8.3f}GB - {entry['class_type']}\n")

        # Peak GPU memory analysis
        peak_nodes = [e for e in report_entries if e['memory_stats'].get('peak_gpu', {}).get('peak_allocated_gb', 0) > 0.001]
        if peak_nodes:
            f.write(f"\nPeak GPU Memory Usage (top nodes):\n")
            peak_sorted = sorted(peak_nodes, key=lambda x: x['memory_stats']['peak_gpu']['peak_allocated_gb'], reverse=True)
            for entry in peak_sorted[:10]:  # Top 10
                peak = entry['memory_stats']['peak_gpu']['peak_allocated_gb']
                f.write(f"  Node {entry['real_node_id']:>3}: {peak:>8.3f}GB - {entry['class_type']}\n")

        # System memory analysis
        ram_nodes = [e for e in report_entries if e['memory_stats'].get('system_rss_delta_gb') is not None]
        if ram_nodes:
            f.write(f"\nSystem Memory Changes (nodes with significant changes):\n")
            ram_sorted = sorted(ram_nodes, key=lambda x: abs(x['memory_stats'].get('system_rss_delta_gb', 0)), reverse=True)
            for entry in ram_sorted[:10]:  # Top 10
                delta = entry['memory_stats']['system_rss_delta_gb']
                if abs(delta) > 0.01:  # > 10MB
                    sign = "+" if delta > 0 else ""
                    f.write(f"  Node {entry['real_node_id']:>3}: {sign}{delta:>8.3f}GB - {entry['class_type']}\n")

    print(f"[ExecutionTimeReporter] Report saved to: {report_path}")

def setup_monitoring():
    """Set up the execution monitoring system by patching ComfyUI's execution functions"""
    global original_execute, original_execute_async

    # Store original functions
    original_execute = execution.execute
    original_execute_async = execution.PromptExecutor.execute_async

    # Patch the execute function
    execution.execute = patched_execute
    execution.PromptExecutor.execute_async = patched_execute_async

    print("[ExecutionTimeReporter] Monitoring system initialized - execution tracking enabled")
