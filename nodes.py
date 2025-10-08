"""
ComfyUI ExecutionTimeReporter Node Definitions
"""

class ExecutionTimeReporter:
    """
    A node that triggers comprehensive execution time and memory reporting for ComfyUI workflows.
    
    This node automatically tracks:
    - Execution time for each node in the workflow
    - GPU memory usage (VRAM allocation, peak usage)
    - System memory usage (RAM)
    - System information (CPU, GPU, software versions)
    
    The node generates detailed reports saved to ComfyUI/output/reports/ when the workflow completes.
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}
    
    RETURN_TYPES = ()
    FUNCTION = "execute"
    OUTPUT_NODE = True
    CATEGORY = "Reporting"
    DESCRIPTION = "Generates comprehensive execution time and memory usage reports for workflow analysis"
    
    def execute(self):
        """
        Execute the reporter node.
        
        This node doesn't perform any direct operations but triggers the timing report
        generation when the workflow completes. The actual report generation happens
        in the patched execution system.
        
        Returns:
            tuple: Empty tuple as this is an output node
        """
        print("[ExecutionTimeReporter] Reporter node executing...")
        return ()

# Node mappings for ComfyUI
NODE_CLASS_MAPPINGS = {
    "ExecutionTimeReporter": ExecutionTimeReporter
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ExecutionTimeReporter": "Execution Time Reporter"
}
