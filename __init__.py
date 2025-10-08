import os
import json
import time
from datetime import datetime
import execution
import server

# Global storage for execution timing data
# Format: {prompt_id: {node_id: {"start_time": float, "end_time": float, "duration": float, "class_type": str, "title": str, "execution_order": int}}}
execution_timing_data = {}

# Track workflow start times
workflow_start_times = {}

# Track execution order
execution_order_counter = {}

# --- 1. The ExecutionTimeReporter Node ---
class ExecutionTimeReporter:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {}}

    RETURN_TYPES = ()
    FUNCTION = "execute"
    OUTPUT_NODE = True
    CATEGORY = "Reporting"

    def execute(self):
        # This node triggers the timing report generation when the workflow completes
        print("[ExecutionTimeReporter] Reporter node executing...")
        return ()

# --- 2. Patch the execution system to track timing ---
original_execute = execution.execute

async def patched_execute(server_instance, dynprompt, caches, current_item, extra_data, executed, prompt_id, execution_list, pending_subgraph_results, pending_async_nodes):
    """Patched execute function that tracks timing for each node"""
    unique_id = current_item
    real_node_id = dynprompt.get_real_node_id(unique_id)

    # Get node information
    class_type = dynprompt.get_node(unique_id)["class_type"]

    # Initialize timing data structure for this prompt if needed
    if prompt_id not in execution_timing_data:
        execution_timing_data[prompt_id] = {}
        workflow_start_times[prompt_id] = time.time()
        execution_order_counter[prompt_id] = 0
        print(f"[ExecutionTimeReporter] Started tracking workflow {prompt_id}")

    # Record start time
    start_time = time.time()

    # Call the original execute function
    result = await original_execute(server_instance, dynprompt, caches, current_item, extra_data, executed, prompt_id, execution_list, pending_subgraph_results, pending_async_nodes)

    # Record end time and calculate duration
    end_time = time.time()
    duration = end_time - start_time

    # Get node title from workflow metadata if available
    node_title = f"Node {real_node_id}"
    try:
        if hasattr(extra_data, 'get') and 'extra_pnginfo' in extra_data:
            workflow = extra_data.get('extra_pnginfo', {}).get('workflow', {})
            if 'nodes' in workflow:
                for node in workflow['nodes']:
                    if str(node.get('id')) == str(real_node_id):
                        node_title = node.get('title', node_title)
                        break
    except:
        pass  # Use default title if metadata extraction fails

    # Get execution order
    execution_order_counter[prompt_id] += 1
    execution_order = execution_order_counter[prompt_id]

    # Store timing data
    execution_timing_data[prompt_id][unique_id] = {
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "class_type": class_type,
        "title": node_title,
        "real_node_id": real_node_id,
        "execution_order": execution_order
    }

    # Optionally log individual node timings (comment out to reduce console output)
    # print(f"[ExecutionTimeReporter] Node {real_node_id} ({class_type}): {duration:.3f}s")

    # Don't generate report here - we'll do it at the end of the workflow

    return result

def generate_execution_report(prompt_id, _dynprompt):
    """Generate and save the execution time report"""
    if prompt_id not in execution_timing_data:
        print("[ExecutionTimeReporter] No timing data found for this workflow.")
        return

    timing_data = execution_timing_data[prompt_id]
    if not timing_data:
        print("[ExecutionTimeReporter] No timing data was collected.")
        return

    print(f"[ExecutionTimeReporter] Generating report for {len(timing_data)} nodes...")

    # Calculate workflow total time
    workflow_start = workflow_start_times.get(prompt_id, 0)
    workflow_end = time.time()
    total_workflow_time = workflow_end - workflow_start if workflow_start else 0

    # Prepare report data
    report_entries = []
    total_node_time = 0

    for unique_id, data in timing_data.items():
        duration = data["duration"]
        total_node_time += duration

        report_entries.append({
            "title": data["title"],
            "class_type": data["class_type"],
            "duration": duration,
            "real_node_id": data["real_node_id"],
            "unique_id": unique_id,
            "execution_order": data["execution_order"]
        })

    # Sort by execution order (chronological order)
    report_entries.sort(key=lambda x: x["execution_order"])

    # Create output directory
    base_output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "output")
    reports_dir = os.path.join(base_output_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    # Generate report
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = os.path.join(reports_dir, f"execution_report_{timestamp}.txt")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("ComfyUI Workflow Execution Time Report\n")
        f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Prompt ID: {prompt_id}\n")
        f.write("="*60 + "\n\n")

        f.write("EXECUTION TIMES (in execution order):\n")
        f.write("-" * 60 + "\n")

        for entry in report_entries:
            f.write(f"#{entry['execution_order']:>2} Node {entry['real_node_id']:>3}: {entry['duration']:>8.3f}s - {entry['class_type']}")
            if entry['title'] != f"Node {entry['real_node_id']}":
                f.write(f" ({entry['title']})")
            f.write("\n")

        f.write("-" * 60 + "\n")
        f.write(f"Total Node Execution Time: {total_node_time:.3f}s\n")
        f.write(f"Total Workflow Time: {total_workflow_time:.3f}s\n")
        f.write(f"Number of Nodes: {len(report_entries)}\n")
        f.write(f"Average Time per Node: {total_node_time/len(report_entries):.3f}s\n")

        # Calculate overhead
        overhead = total_workflow_time - total_node_time
        if overhead > 0:
            f.write(f"ComfyUI Overhead: {overhead:.3f}s ({(overhead/total_workflow_time)*100:.1f}%)\n")

        # Add performance analysis
        f.write("\n" + "="*60 + "\n")
        f.write("PERFORMANCE ANALYSIS (sorted by duration, slowest first):\n")
        f.write("-" * 60 + "\n")

        # Create a copy sorted by duration for performance analysis
        performance_entries = sorted(report_entries, key=lambda x: x["duration"], reverse=True)

        for i, entry in enumerate(performance_entries, 1):
            percentage = (entry['duration'] / total_node_time) * 100
            f.write(f"{i:>2}. Node {entry['real_node_id']:>3}: {entry['duration']:>8.3f}s ({percentage:>5.1f}%) - {entry['class_type']}")
            if entry['title'] != f"Node {entry['real_node_id']}":
                f.write(f" ({entry['title']})")
            f.write("\n")

        f.write("\n" + "-" * 60 + "\n")
        if len(performance_entries) > 0:
            slowest = performance_entries[0]
            f.write(f"Slowest Node: {slowest['class_type']} ({slowest['duration']:.3f}s)\n")

            # Find nodes taking more than 10% of total node time
            significant_nodes = [e for e in performance_entries if e['duration'] > total_node_time * 0.1]
            if significant_nodes:
                f.write(f"Nodes taking >10% of total node time: {len(significant_nodes)}\n")

    print(f"[ExecutionTimeReporter] Report saved to: {report_path}")

    # Clean up timing data for this prompt to prevent memory leaks
    del execution_timing_data[prompt_id]
    if prompt_id in workflow_start_times:
        del workflow_start_times[prompt_id]
    if prompt_id in execution_order_counter:
        del execution_order_counter[prompt_id]

# --- 3. Patch workflow completion to generate reports ---
original_execute_async = execution.PromptExecutor.execute_async

async def patched_execute_async(self, prompt, prompt_id, extra_data={}, execute_outputs=[]):
    """Patched execute_async to generate report after workflow completion"""
    # Call the original execute_async
    result = await original_execute_async(self, prompt, prompt_id, extra_data, execute_outputs)

    # Check if this workflow contains an ExecutionTimeReporter node
    has_reporter = False
    for _, node_data in prompt.items():
        if node_data.get("class_type") == "ExecutionTimeReporter":
            has_reporter = True
            break

    # If it has a reporter node and we have timing data, generate the report
    if has_reporter:
        if prompt_id in execution_timing_data:
            print(f"[ExecutionTimeReporter] Workflow completed - generating final report for {len(execution_timing_data[prompt_id])} nodes...")
            try:
                generate_execution_report(prompt_id, None)
            except Exception as e:
                print(f"[ExecutionTimeReporter] Error generating report: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"[ExecutionTimeReporter] Workflow completed but no timing data found for prompt {prompt_id}")

    return result

# --- 4. Apply the patches ---
execution.execute = patched_execute
execution.PromptExecutor.execute_async = patched_execute_async
print("[ExecutionTimeReporter] Execution timing system loaded and patched successfully.")

# --- 4. Standard Node Mappings ---
NODE_CLASS_MAPPINGS = {"ExecutionTimeReporter": ExecutionTimeReporter}
NODE_DISPLAY_NAME_MAPPINGS = {"ExecutionTimeReporter": "Execution Time Reporter"}