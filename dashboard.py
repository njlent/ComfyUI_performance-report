"""
ComfyUI ExecutionTimeReporter Dashboard
Records every run (node timings plus memory and CPU samples over time), keeps the
latest runs on disk and serves them to the Performance tab in ComfyUI's bottom panel.
"""

import os
import json
import time
import asyncio
import hashlib
import threading
import psutil
from aiohttp import web
import folder_paths
import server

try:
    import torch
    CUDA_AVAILABLE = torch.cuda.is_available()
except ImportError:
    CUDA_AVAILABLE = False

# Internal ComfyUI API for step progress (e.g. sampler steps), the dashboard works without it
try:
    from comfy_execution.progress import get_progress_state
except ImportError:
    get_progress_state = None

GB = 1024 ** 3
RUNS_DIR = os.path.join(folder_paths.get_user_directory(), "performance-report", "runs")
MAX_RUNS = 200
SAMPLE_INTERVAL = 0.5
# Every this many samples the interval grows by SAMPLE_INTERVAL so long runs stay small on disk
SAMPLES_PER_STEP = 1200
MODEL_INPUTS = ("ckpt_name", "unet_name")

lock = threading.Lock()
live_run = None
last_run_id = None  # lets the dashboard notice runs that finished between two of its polls
sampler = None
history = None  # {run_id: summary}, read from RUNS_DIR on first request
process = psutil.Process()


def gpu_utilization_available():
    if not CUDA_AVAILABLE:
        return False
    try:
        torch.cuda.utilization()
    except Exception:  # needs the optional nvidia-ml-py package
        return False
    return True

GPU_UTILIZATION = gpu_utilization_available()


def model_label(prompt):
    """Name of the first checkpoint or diffusion model the prompt loads, used to tell runs apart"""
    for node in prompt.values():
        for name in MODEL_INPUTS:
            value = node.get("inputs", {}).get(name)
            if isinstance(value, str):
                return os.path.splitext(value.replace("\\", "/").rsplit("/", 1)[-1])[0]
    return None


def start_run(prompt_id, prompt):
    global live_run, sampler
    stop_sampler()

    started_at = time.time()
    device = torch.cuda.current_device() if CUDA_AVAILABLE else None
    signature = ",".join(sorted(f"{node_id}:{node.get('class_type')}" for node_id, node in prompt.items()))
    run = {
        "id": time.strftime("%Y%m%d-%H%M%S", time.localtime(started_at)) + "-" + hashlib.sha1(str(prompt_id).encode()).hexdigest()[:8],
        "prompt_id": prompt_id,
        "status": "running",
        "started_at": started_at,
        "duration": 0.0,
        "label": model_label(prompt),
        "workflow_hash": hashlib.sha1(signature.encode()).hexdigest()[:12],
        "node_total": len(prompt),
        "vram_total_gb": torch.cuda.mem_get_info(device)[1] / GB if device is not None else None,
        "ram_total_gb": psutil.virtual_memory().total / GB,
        "gpu_utilization": GPU_UTILIZATION,
        "current_node": None,
        "nodes": {},
        "samples": {"t": [], "vram_used": [], "vram_allocated": [], "ram": [], "cpu": [], "gpu": []},
    }
    with lock:
        live_run = run

    stop = threading.Event()
    thread = threading.Thread(target=run_sampler, args=(stop, device), name="performance-report-sampler", daemon=True)
    thread.start()
    sampler = (stop, thread)


def node_started(unique_id, class_type, title, start_time):
    with lock:
        if live_run is not None:
            live_run["current_node"] = {"id": unique_id, "class_type": class_type, "title": title, "start": round(start_time - live_run["started_at"], 3)}


def node_finished(unique_id, class_type, title, entry):
    with lock:
        if live_run is None:
            return
        memory = entry["memory_stats"]
        vram_peak = (memory["peak_gpu"] or {}).get("peak_allocated_gb")
        node = live_run["nodes"].get(unique_id)
        if node is None:
            live_run["nodes"][unique_id] = {
                "id": unique_id,
                "class_type": class_type,
                "title": title,
                "order": len(live_run["nodes"]) + 1,
                "start": round(entry["start_time"] - live_run["started_at"], 3),
                "duration": entry["duration"],
                "vram_peak_gb": vram_peak,
                "vram_delta_gb": memory["gpu_allocated_delta_gb"],
                "ram_delta_gb": memory["system_rss_delta_gb"],
            }
        else:
            # Async and expanding nodes run again once their results are ready
            node["duration"] += entry["duration"]
            if vram_peak is not None:
                node["vram_peak_gb"] = max(node["vram_peak_gb"] or 0, vram_peak)
            for key, delta in (("vram_delta_gb", memory["gpu_allocated_delta_gb"]), ("ram_delta_gb", memory["system_rss_delta_gb"])):
                if delta is not None:
                    node[key] = (node[key] or 0) + delta
        live_run["current_node"] = None


def finish_run(prompt_id, status):
    global live_run, last_run_id
    stop_sampler()
    with lock:
        run, live_run = live_run, None
    if run is None or run["prompt_id"] != prompt_id:
        return
    run["status"] = status
    run["duration"] = time.time() - run["started_at"]
    run["current_node"] = None
    run["nodes"] = sorted(run["nodes"].values(), key=lambda node: node["start"])
    save_run(run)
    last_run_id = run["id"]


def sample(device):
    if device is not None:
        free, total = torch.cuda.mem_get_info(device)
        vram_used = round((total - free) / GB, 3)
        vram_allocated = round(torch.cuda.memory_allocated(device) / GB, 3)
        gpu = torch.cuda.utilization(device) if GPU_UTILIZATION else None
    else:
        vram_used = vram_allocated = gpu = None
    ram = round(process.memory_info().rss / GB, 3)
    cpu = psutil.cpu_percent(None)

    with lock:
        if live_run is None:
            return 0
        samples = live_run["samples"]
        samples["t"].append(round(time.time() - live_run["started_at"], 2))
        samples["vram_used"].append(vram_used)
        samples["vram_allocated"].append(vram_allocated)
        samples["ram"].append(ram)
        samples["cpu"].append(cpu)
        samples["gpu"].append(gpu)
        return len(samples["t"])


def run_sampler(stop, device):
    psutil.cpu_percent(None)  # the first call only sets the baseline for the next one
    stop.wait(0.1)
    count = sample(device)
    while not stop.wait(SAMPLE_INTERVAL * (1 + count // SAMPLES_PER_STEP)):
        count = sample(device)
    sample(device)


def stop_sampler():
    global sampler
    if sampler is not None:
        stop, thread = sampler
        stop.set()
        thread.join(timeout=5)
        sampler = None


def summarize(run):
    nodes = run["nodes"]
    slowest = max(nodes, key=lambda node: node["duration"], default=None)
    samples = run["samples"]
    return {
        "id": run["id"],
        "status": run["status"],
        "started_at": run["started_at"],
        "duration": run["duration"],
        "label": run["label"],
        "workflow_hash": run["workflow_hash"],
        "nodes_executed": len(nodes),
        "node_total": run["node_total"],
        "peak_vram_gb": max((v for v in samples["vram_used"] if v is not None), default=None),
        "peak_ram_gb": max(samples["ram"], default=None),
        "slowest": {"title": slowest["title"], "duration": slowest["duration"]} if slowest else None,
    }


def save_run(run):
    os.makedirs(RUNS_DIR, exist_ok=True)
    with open(os.path.join(RUNS_DIR, run["id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(run, f)

    # Run ids start with their timestamp, so sorting by name puts the oldest first
    names = sorted(name for name in os.listdir(RUNS_DIR) if name.endswith(".json"))
    removed = names[:-MAX_RUNS]
    for name in removed:
        os.remove(os.path.join(RUNS_DIR, name))

    with lock:
        if history is not None:
            history[run["id"]] = summarize(run)
            for name in removed:
                history.pop(name[:-5], None)


def read_history():
    runs = {}
    if not os.path.isdir(RUNS_DIR):
        return runs
    for name in os.listdir(RUNS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(RUNS_DIR, name), encoding="utf-8") as f:
                runs[name[:-5]] = summarize(json.load(f))
        except (OSError, ValueError, KeyError) as e:
            print(f"[ExecutionTimeReporter] Skipping unreadable run file {name}: {e}")
    return runs


def list_runs():
    global history
    if history is None:
        runs = read_history()
        with lock:
            if history is None:
                history = runs
    with lock:
        return sorted(history.values(), key=lambda run: run["started_at"], reverse=True)


def load_run(run_id):
    list_runs()
    # Only ids read from RUNS_DIR are turned into paths
    if run_id not in history:
        return None
    with open(os.path.join(RUNS_DIR, run_id + ".json"), encoding="utf-8") as f:
        return json.load(f)


def live_json():
    with lock:
        if live_run is None:
            return json.dumps({"run": None, "last_run": last_run_id})
        run = dict(live_run, nodes=sorted(live_run["nodes"].values(), key=lambda node: node["start"]), duration=time.time() - live_run["started_at"])
        current = live_run["current_node"]
        if current is not None:
            run["current_node"] = dict(current, progress=node_progress(current["id"]))
        return json.dumps({"run": run, "last_run": last_run_id})


def node_progress(unique_id):
    if get_progress_state is None:
        return None
    try:
        state = get_progress_state().nodes.get(unique_id)
        return {"value": state["value"], "max": state["max"]} if state is not None else None
    except Exception:  # the registry's layout is internal and may change
        return None


routes = server.PromptServer.instance.routes


@routes.get("/performance-report/live")
async def get_live(request):
    return web.Response(text=live_json(), content_type="application/json")


@routes.get("/performance-report/runs")
async def get_runs(request):
    return web.json_response({"runs": await asyncio.to_thread(list_runs)})


@routes.get("/performance-report/runs/{run_id}")
async def get_run(request):
    run = await asyncio.to_thread(load_run, request.match_info["run_id"])
    if run is None:
        raise web.HTTPNotFound()
    return web.json_response(run)
