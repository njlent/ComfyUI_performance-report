"""
ComfyUI ExecutionTimeReporter
A comprehensive performance monitoring and analysis tool for ComfyUI workflows.

This module provides:
- Execution time tracking for each node
- GPU memory usage monitoring (VRAM allocation, peak usage)
- System memory tracking (RAM usage)
- Comprehensive system information reporting
- Detailed performance analysis and optimization insights
"""

# Import the node definitions
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Import the monitoring system
from .monitoring import setup_monitoring

# Set up the execution monitoring system
setup_monitoring()

print("[ComfyUI_Performance-report] Execution timing and memory monitoring system loaded successfully.")
print("[ComfyUI_Performance-report] add the -Execution Time Reporter- node to the end of your workflow to save reports.")

# Export the mappings for ComfyUI
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']