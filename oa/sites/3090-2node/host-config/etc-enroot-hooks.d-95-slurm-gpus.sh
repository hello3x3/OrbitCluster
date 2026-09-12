#!/usr/bin/env bash
# 98-nvidia.sh 的前置钩子（文件名 95 < 98，保证先于它执行）
# 修复 2026-09-05: CPU 作业(无 CUDA_VISIBLE_DEVICES)时末行 test 失败导致退出码 1
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
if [ -n "${cvd}" ]; then
    printf 'NVIDIA_VISIBLE_DEVICES=%s\n' "${cvd}" >> "${env_file}"
fi
exit 0
