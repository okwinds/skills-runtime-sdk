#!/usr/bin/env bash
set -euo pipefail

# Linux bubblewrap（bwrap）通路探测（Docker）。
#
# 说明：
# - macOS 上无法直接运行 bwrap；这里用 Docker 起一个 Linux 容器验证 “bwrap 能跑 + 基础 mount 形态可用”。 
# - 需要 privileged（否则不同 Docker/内核配置下可能无法创建 user namespace）。
# - 本脚本不进入离线回归门禁；只用于“真实 I/O / 真实内核能力”验证。

docker run --rm \
  --privileged \
  --security-opt seccomp=unconfined \
  --entrypoint sh \
  debian:bookworm-slim -lc '
    set -eu
    apt-get update -qq
    apt-get install -y -qq bubblewrap >/dev/null
    bwrap --version
    mkdir -p /tmp/work
    echo hi >/tmp/work/hi.txt
    bwrap \
      --die-with-parent \
      --unshare-net \
      --proc /proc \
      --dev /dev \
      --ro-bind /usr /usr \
      --ro-bind /bin /bin \
      --ro-bind /lib /lib \
      --ro-bind /etc /etc \
      --bind /tmp/work /work \
      --chdir /work \
      -- \
      /bin/cat /work/hi.txt
  '
