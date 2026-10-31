#!/usr/bin/env bash
# 98-nvidia.sh 的前置钩子（文件名 95 < 98，保证先于它执行）；enroot 只执行带 +x 的钩子，
# 所以本文件在目标机上必须是 755。
# CPU 作业（无 CUDA_VISIBLE_DEVICES）时末行 test 会失败导致退出码 1，故两处显式 exit 0
set -eu
env_file="${ENROOT_ENVIRON:-}"
if [ -z "${env_file}" ] || [ ! -f "${env_file}" ]; then
    exit 0
fi
grep -q '^NVIDIA_DRIVER_CAPABILITIES=' "${env_file}" || \
    printf 'NVIDIA_DRIVER_CAPABILITIES=compute,utility\n' >> "${env_file}"
if grep -q '^NVIDIA_VISIBLE_DEVICES=' "${env_file}"; then
    exit 0
fi
cvd=$(sed -n 's/^CUDA_VISIBLE_DEVICES=//p' "${env_file}" | head -1)
# pyxis 有时只把 CUDA_VISIBLE_DEVICES 放在 enroot 进程环境里，不写 ENROOT_ENVIRON；
# 实测 pyxis v0.24.0 + enroot 4.2.1 就是这种情况，从 hook 自身环境兜底。
if [ -z "${cvd}" ]; then
    cvd="${CUDA_VISIBLE_DEVICES:-}"
fi
if [ -n "${cvd}" ]; then
    printf 'NVIDIA_VISIBLE_DEVICES=%s\n' "${cvd}" >> "${env_file}"
fi
exit 0
