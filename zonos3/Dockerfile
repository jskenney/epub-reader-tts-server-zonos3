# ZONOS3-Mini's ggml server (zonos3-server, C++) built with CUDA, from upstream at a pinned commit.
# Its API is ZONOS3's own (/v2/...); the adapter service translates the reader's API to it.
#
# Build arguments:
#   ZONOS3_COMMIT   the upstream commit (docker-compose.yml pins it)
#   CUDA_ARCHS      the GPU architecture(s) to compile for: 120 = Blackwell workstation and consumer cards
#                   (RTX PRO 4000/6000 Blackwell, RTX 50xx), 90 = H100, 89 = RTX 40xx / L4, 86 = RTX 30xx,
#                   80 = A100; several separated by ";"
FROM nvidia/cuda:12.8.1-devel-ubuntu24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends git cmake ninja-build g++ curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# The text normalizer (zyphra-textnorm) is Rust; CMake builds it with cargo.
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH=/root/.cargo/bin:$PATH
ARG ZONOS3_COMMIT
ARG CUDA_ARCHS=120
RUN test -n "$ZONOS3_COMMIT" \
    && git init -q /src && cd /src \
    && git fetch -q --depth 1 https://github.com/Zyphra/ZONOS3-Mini.git "$ZONOS3_COMMIT" && git checkout -q FETCH_HEAD \
    && git submodule update --init --depth 1 servers/ggml/ggml third_party/zyphra-textnorm
# libcuda.so.1 comes with the NVIDIA driver, which a build has no access to: the NVIDIA Container Toolkit provides it
# when the container runs. So the link mustn't insist on it (--allow-shlib-undefined), as llama.cpp's CUDA image does.
RUN cd /src/servers/ggml \
    && cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DGGML_NATIVE=OFF \
       -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCHS" \
       -DCMAKE_EXE_LINKER_FLAGS="-Wl,--allow-shlib-undefined" \
    && cmake --build build --target zonos3-server \
    && mkdir -p /opt/zonos3/lib && cp build/zonos3-server /opt/zonos3/ \
    && find build -name 'libggml*.so*' -exec cp -a {} /opt/zonos3/lib/ \;

FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04
# ffmpeg decodes reference clips; curl is for the health check.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/zonos3 /opt/zonos3
ENV LD_LIBRARY_PATH=/opt/zonos3/lib
COPY start.sh /opt/zonos3/start.sh
EXPOSE 1919
CMD ["/bin/sh", "/opt/zonos3/start.sh"]
