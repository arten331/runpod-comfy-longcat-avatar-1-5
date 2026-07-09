# LongCat Avatar 1.5 RunPod Serverless Worker

This image extends `runpod/worker-comfyui:5.8.6-base` with the community
`ComfyUI_LongCat_Avatar` node pinned to commit
`b8f95ff1b4d6c8f9aa49136419bc51a09b88c4fe`.

The image contains code only. The five model files are downloaded by a one-time
`init_models` job to an attached RunPod network volume. Their total size is
approximately 28.7 GB.

The `.github/workflows/build.yml` workflow builds the image in GitHub Actions
and pushes `runpod-comfy-longcat-avatar-1-5:latest` to Docker Hub. Docker Hub
credentials are provided only through GitHub Actions secrets.

## Endpoint configuration

- Endpoint type: Queue
- GPU: A100 80 GB for the first smoke test
- GPUs per worker: 1
- Active workers: 0
- Max workers: 1
- Execution timeout: 3600 seconds
- FlashBoot: enabled
- Network volume: 80 GB, mounted at `/runpod-volume`
- Runtime secret: optional `HF_TOKEN`

## Initialize model volume

Submit once, then wait for the worker to scale down before the generation job:

```json
{
  "input": {
    "action": "init_models"
  },
  "policy": {
    "executionTimeout": 3600000,
    "ttl": 7200000
  }
}
```

## Generate

Submit `workflow_api.json` as `input.workflow` and attach the reference image and
audio as base64 files named `reference.png` and `audio.wav`:

```json
{
  "input": {
    "action": "run",
    "workflow": {},
    "files": [
      {"name": "reference.png", "data": "BASE64"},
      {"name": "audio.wav", "data": "BASE64"}
    ]
  }
}
```

Outputs up to 7 MB are returned as base64. Larger outputs are copied to
`/runpod-volume/outputs/<job-id>/` and returned as volume paths.
