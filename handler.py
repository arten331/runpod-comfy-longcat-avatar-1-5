import base64
import copy
import json
import mimetypes
import os
import shutil
import time
import uuid
from pathlib import Path

import requests
import runpod
import websocket


COMFY_HTTP = os.environ.get("COMFY_HTTP", "http://127.0.0.1:8188")
COMFY_WS = os.environ.get("COMFY_WS", "ws://127.0.0.1:8188/ws")
COMFY_INPUT = Path("/comfyui/input")
COMFY_OUTPUT = Path("/comfyui/output")
MODEL_ROOT = Path("/runpod-volume/models")
VOLUME_OUTPUT = Path("/runpod-volume/outputs")
MODEL_MANIFEST = Path("/model_manifest.json")
MAX_INLINE_BYTES = int(os.environ.get("MAX_INLINE_OUTPUT_BYTES", 7_000_000))


def _wait_for_comfy(timeout_seconds=300):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if requests.get(f"{COMFY_HTTP}/system_stats", timeout=5).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("ComfyUI did not become ready before the timeout")


def _decode_data(value):
    if not isinstance(value, str):
        raise ValueError("file data must be a base64 string")
    if value.startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value, validate=True)


def _safe_name(value):
    name = Path(value).name
    if not name or name in {".", ".."}:
        raise ValueError("invalid input filename")
    return name


def _stage_files(files):
    staged = []
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    for item in files or []:
        name = _safe_name(item.get("name", ""))
        target = COMFY_INPUT / name
        target.write_bytes(_decode_data(item.get("data")))
        staged.append(target)
    return staged


def _replace_tokens(value, replacements):
    if isinstance(value, dict):
        return {key: _replace_tokens(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_tokens(item, replacements) for item in value]
    if isinstance(value, str):
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
    return value


def _run_workflow(job, job_input):
    workflow = job_input.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError("input.workflow must be a ComfyUI API workflow object")

    job_id = str(job.get("id") or uuid.uuid4())
    client_id = str(uuid.uuid4())
    workflow = _replace_tokens(copy.deepcopy(workflow), {"${JOB_ID}": job_id})
    staged = _stage_files(job_input.get("files"))
    started_at = time.time()

    try:
        _wait_for_comfy()
        ws = websocket.create_connection(f"{COMFY_WS}?clientId={client_id}", timeout=30)
        try:
            response = requests.post(
                f"{COMFY_HTTP}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=60,
            )
            if not response.ok:
                raise RuntimeError(f"ComfyUI rejected workflow: {response.status_code} {response.text}")
            prompt_id = response.json()["prompt_id"]

            ws.settimeout(30)
            while True:
                try:
                    message = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                data = message.get("data", {})
                if data.get("prompt_id") != prompt_id:
                    continue
                if message.get("type") == "execution_error":
                    raise RuntimeError(
                        f"ComfyUI node {data.get('node_id')} ({data.get('node_type')}) failed: "
                        f"{data.get('exception_message')}"
                    )
                if message.get("type") == "executing" and data.get("node") is None:
                    break
        finally:
            ws.close()

        history_response = requests.get(f"{COMFY_HTTP}/history/{prompt_id}", timeout=60)
        history_response.raise_for_status()
        history = history_response.json().get(prompt_id, {})
        output_files = _discover_outputs(history, job_id, started_at)
        if not output_files:
            raise RuntimeError("workflow completed but no output files were discovered")
        return {"prompt_id": prompt_id, "files": [_package_output(path, job_id) for path in output_files]}
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def _discover_outputs(history, job_id, started_at):
    candidates = []
    for node_output in history.get("outputs", {}).values():
        for value in node_output.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                root = {
                    "input": COMFY_INPUT,
                    "output": COMFY_OUTPUT,
                    "temp": Path("/comfyui/temp"),
                }.get(item.get("type"), COMFY_OUTPUT)
                path = root / item.get("subfolder", "") / item["filename"]
                if path.is_file():
                    candidates.append(path)

    if COMFY_OUTPUT.exists():
        for path in COMFY_OUTPUT.rglob("*"):
            if not path.is_file():
                continue
            if job_id in path.name or path.stat().st_mtime >= started_at:
                candidates.append(path)

    unique = {}
    for path in candidates:
        unique[str(path.resolve())] = path
    return sorted(unique.values(), key=lambda item: item.stat().st_mtime)


def _package_output(path, job_id):
    payload = {
        "filename": path.name,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": path.stat().st_size,
    }
    if path.stat().st_size <= MAX_INLINE_BYTES:
        payload["type"] = "base64"
        payload["data"] = base64.b64encode(path.read_bytes()).decode("ascii")
        return payload

    if not VOLUME_OUTPUT.parent.exists():
        raise RuntimeError(
            f"output {path.name} is too large for an inline response and no network volume is mounted"
        )
    target_dir = VOLUME_OUTPUT / job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    shutil.copy2(path, target)
    payload["type"] = "runpod_volume"
    payload["path"] = str(target)
    return payload


def _download_models(job):
    if not MODEL_ROOT.parent.exists():
        raise RuntimeError("network volume is not mounted at /runpod-volume")

    manifest = json.loads(MODEL_MANIFEST.read_text())
    token = os.environ.get("HF_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    results = []

    for index, model in enumerate(manifest["models"], start=1):
        target = MODEL_ROOT / model["destination"]
        expected_size = int(model["size"])
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.is_file() and target.stat().st_size == expected_size:
            results.append({"name": model["name"], "status": "present", "size": expected_size})
            continue

        runpod.serverless.progress_update(job, f"Downloading model {index}/{len(manifest['models'])}: {model['name']}")
        part = target.with_suffix(target.suffix + ".part")
        part.unlink(missing_ok=True)
        with requests.get(model["url"], headers=headers, stream=True, timeout=(30, 600)) as response:
            response.raise_for_status()
            with part.open("wb") as output:
                for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        output.write(chunk)

        actual_size = part.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(
                f"size mismatch for {model['name']}: expected {expected_size}, received {actual_size}"
            )
        part.replace(target)
        results.append({"name": model["name"], "status": "downloaded", "size": actual_size})

    return {"models": results, "restart_required": True}


def handler(job):
    try:
        job_input = job.get("input") or {}
        action = job_input.get("action", "run")
        if action == "init_models":
            return _download_models(job)
        if action == "run":
            return _run_workflow(job, job_input)
        return {"error": f"unsupported action: {action}"}
    except Exception as error:
        return {"error": str(error), "error_type": type(error).__name__}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

