```bash
srun --gres=gpu:1 \
  --container-image=/share/images/cuda12.8.0-devel-ubuntu24.04.sqsh \
  --container-env=SSH_PORT \
  --container-mounts=$HOME:$HOME \
  /opt/start_ssh.sh
```