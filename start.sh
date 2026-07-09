#!/usr/bin/env bash
set -euo pipefail

VOLUME_MODELS=/runpod-volume/models
COMFY_MODELS=/comfyui/models

if [ -d /runpod-volume ]; then
    for model_dir in diffusion_models loras vae clip audio_encoders longcat; do
        mkdir -p "${VOLUME_MODELS}/${model_dir}"
        rm -rf "${COMFY_MODELS:?}/${model_dir}"
        ln -s "${VOLUME_MODELS}/${model_dir}" "${COMFY_MODELS}/${model_dir}"
    done
fi

exec /start-worker-comfy.sh

