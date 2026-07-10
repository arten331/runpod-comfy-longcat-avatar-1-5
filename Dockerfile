ARG WORKER_IMAGE=runpod/worker-comfyui:5.8.6-base

FROM ${WORKER_IMAGE} AS flash_builder

ARG FLASH_ATTN_VERSION=2.8.3.post1
ENV CUDA_HOME=/usr/local/cuda-13.0 \
    MAX_JOBS=2

RUN if [ ! -L /sbin ]; then ln -sf /usr/sbin/ldconfig.real /sbin/ldconfig.real; fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cuda-nvcc-13-0 \
        ninja-build \
    && rm -rf /var/lib/apt/lists/*

RUN uv pip install packaging psutil ninja \
    && mkdir -p /wheels \
    && python -m pip wheel "flash-attn==${FLASH_ATTN_VERSION}" \
        --no-build-isolation \
        --no-deps \
        --wheel-dir /wheels

FROM ${WORKER_IMAGE}

ARG LONGCAT_NODE_SHA=b8f95ff1b4d6c8f9aa49136419bc51a09b88c4fe

RUN if [ ! -L /sbin ]; then ln -sf /usr/sbin/ldconfig.real /sbin/ldconfig.real; fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /comfyui/custom_nodes/longcat_avatar \
    && wget -q \
        "https://github.com/smthemex/ComfyUI_LongCat_Avatar/archive/${LONGCAT_NODE_SHA}.tar.gz" \
        -O /tmp/longcat-avatar.tar.gz \
    && tar -xzf /tmp/longcat-avatar.tar.gz \
        --strip-components=1 \
        -C /comfyui/custom_nodes/longcat_avatar \
    && rm /tmp/longcat-avatar.tar.gz

COPY requirements-node.txt /tmp/requirements-node.txt
COPY --from=flash_builder /wheels /tmp/wheels

RUN uv pip install --no-cache-dir -r /tmp/requirements-node.txt \
    && uv pip install --no-cache-dir /tmp/wheels/*.whl \
    && uv pip install --no-cache-dir "huggingface-hub<1.0" \
    && rm -rf /tmp/wheels /tmp/requirements-node.txt

RUN mv /start.sh /start-worker-comfy.sh

COPY start.sh /start.sh
COPY handler.py /handler.py
COPY model_manifest.json /model_manifest.json
COPY workflow_api.json /workflow_api.json

RUN chmod +x /start.sh

CMD ["/start.sh"]
