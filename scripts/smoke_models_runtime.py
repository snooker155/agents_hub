#!/usr/bin/env python
"""
Live smoke check of the model runtime (deploy/models, docs/local-models.md).

Walks the whole path a person takes on the Models page's Local tab, against a
running runtime: health, download one small GGUF from Hugging Face, load it,
list it on the OpenAI gateway, ask it one question over ``/v1/chat/completions``,
read its structure, unload it and, unless ``--keep``, delete the file. Every
step prints a line; the script exits non-zero at the first failure with the
runtime's answer, so it doubles as the reproduction for a bug report.

Run it against the compose profile or a host-mode runtime::

    docker build -f deploy/models/Dockerfile -t agents-hub-models .
    docker run -d --name ah-models -p 127.0.0.1:8200:8200 \\
        -e MODELS_TOKEN=smoke -v $HOME/.agents_hub/models:/models agents-hub-models
    python scripts/smoke_models_runtime.py --url http://127.0.0.1:8200 --token smoke

The default model is Qwen2.5 0.5B Instruct at Q4_K_M (about 400 MB): small
enough to download and answer on a laptop CPU inside a few minutes, large
enough to have a real block graph for the structure step. The runtime's own
client (``providers.local_models.RuntimeClient``) is used for every call but
the chat, which goes through plain HTTP so the gateway is exercised the way
an external client uses it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers.local_models import LocalModelError, RuntimeClient  # noqa: E402

DEFAULT_REPO = "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
DEFAULT_FILE = "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def step(name: str, detail: str = "") -> None:
    print(f"[ok]   {name}" + (f": {detail}" if detail else ""), flush=True)


def fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}", flush=True)
    sys.exit(1)


def wait_for_job(client: RuntimeClient, job_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last_percent = -1.0
    while time.time() < deadline:
        job = client.job(job_id)
        status = job.get("status")
        percent = float(job.get("percent") or 0.0)
        if percent - last_percent >= 10 or status in ("done", "error"):
            print(f"       download {status} {percent:.0f}%", flush=True)
            last_percent = percent
        if status == "done":
            return job
        if status == "error":
            fail("download", str(job.get("error") or job))
        time.sleep(2)
    fail("download", f"not finished after {timeout:.0f}s")
    return {}


def chat(url: str, token: str, model: str, timeout: float) -> str:
    import httpx
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Answer with one word: what colour is the sky on a clear day?"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    with httpx.Client(timeout=timeout) as http:
        resp = http.post(f"{url}/v1/chat/completions", json=body,
                         headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        fail("chat", f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        fail("chat", f"unexpected shape: {json.dumps(data)[:300]}")
        return ""
    if not str(content).strip():
        fail("chat", "empty completion")
    return str(content).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("AGENTS_HUB_MODELS_URL") or "http://127.0.0.1:8200")
    ap.add_argument("--token", default=os.environ.get("AGENTS_HUB_MODELS_TOKEN")
                    or os.environ.get("MODELS_TOKEN") or "")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--download-timeout", type=float, default=1800.0)
    ap.add_argument("--keep", action="store_true", help="leave the file on the runtime afterwards")
    args = ap.parse_args()
    if not args.token:
        fail("setup", "a token is required (--token or AGENTS_HUB_MODELS_TOKEN)")

    client = RuntimeClient(args.url, args.token, timeout=60.0)
    started = time.time()

    try:
        health = client.health()
    except LocalModelError as exc:
        fail("health", str(exc))
        return
    step("health", json.dumps(health))

    already = any(m.get("file") == args.file for m in client.models())
    if already:
        step("download", f"{args.file} is already on the runtime")
    else:
        try:
            job = client.download(args.repo, args.file)
        except LocalModelError as exc:
            fail("download", str(exc))
            return
        step("download", f"job {job.get('job_id')} for {args.repo}/{args.file}")
        wait_for_job(client, str(job["job_id"]), args.download_timeout)
        step("download", "complete")

    listed = [m for m in client.models() if m.get("file") == args.file]
    if not listed:
        fail("list", f"{args.file} not listed after download: {client.models()}")
    size_mb = float(listed[0].get("size_bytes") or listed[0].get("size") or 0) / (1024 * 1024)
    step("list", f"{args.file} ({size_mb:.0f} MB)")

    try:
        loaded = client.load(args.file, context_length=2048)
    except LocalModelError as exc:
        fail("load", str(exc))
        return
    step("load", json.dumps({k: loaded.get(k) for k in ("file", "port", "status", "context_length") if k in loaded}))

    import httpx
    with httpx.Client(timeout=30.0) as http:
        resp = http.get(f"{args.url}/v1/models", headers={"Authorization": f"Bearer {args.token}"})
    if resp.status_code != 200:
        fail("v1/models", f"HTTP {resp.status_code}: {resp.text[:200]}")
    ids = [m.get("id") for m in resp.json().get("data", [])]
    served = next((i for i in ids if i and (i == args.file or i in args.file or args.file.startswith(str(i)))), None)
    if served is None:
        fail("v1/models", f"loaded model not served: {ids}")
    step("v1/models", f"served as {served}")

    answer = chat(args.url, args.token, str(served), timeout=300.0)
    step("chat", repr(answer))

    try:
        structure = client.structure(args.file)
    except LocalModelError as exc:
        fail("structure", str(exc))
        return
    blocks = structure.get("blocks") or []
    if not blocks:
        fail("structure", f"no blocks: {json.dumps(structure)[:300]}")
    tensors = sum(len(b.get("tensors") or []) for b in blocks)
    info = structure.get("model") or {}
    step("structure", f"{len(blocks)} blocks, {tensors} tensors, "
                      f"{info.get('architecture')} with {info.get('parameters')} parameters, "
                      f"{info.get('layers')} layers")

    try:
        client.unload(args.file)
    except LocalModelError as exc:
        fail("unload", str(exc))
    if any(m.get("loaded") for m in client.models() if m.get("file") == args.file):
        fail("unload", "still marked loaded")
    step("unload", "done")

    if not args.keep and not already:
        client.delete(args.file)
        step("delete", args.file)
    print(f"all steps passed in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
