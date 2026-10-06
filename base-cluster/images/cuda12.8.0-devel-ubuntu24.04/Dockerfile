# cuda12.8.0-devel-ubuntu24.04 —— CUDA 12.8 devel 基座 + sshd / conda / 常用工具。
FROM nvidia/cuda:12.8.0-devel-ubuntu24.04

SHELL [ "/bin/bash", "--login", "-c" ]

# Start as root
USER root

RUN sed -i -e 's@//.*archive.ubuntu.com@//mirrors.cernet.edu.cn@g' \
            -e 's@//security.ubuntu.com@//mirrors.cernet.edu.cn@g' \
            /etc/apt/sources.list.d/ubuntu.sources && \
    apt-get clean && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y vim git locales openssh-server tzdata

# 中文字体 locale
RUN locale-gen en_US.UTF-8 zh_CN.utf8

# 系统时间
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo "$TZ" > /etc/timezone

RUN mkdir -p /etc/ssh/sshd_config.d && \
    printf '%s\n' \
        'PermitRootLogin no' \
        'PasswordAuthentication no' \
        'PermitEmptyPasswords no' \
        'KbdInteractiveAuthentication no' \
        'ChallengeResponseAuthentication no' \
        'PubkeyAuthentication yes' \
        'AuthenticationMethods publickey' \
        > /etc/ssh/sshd_config.d/00-portal-hardening.conf && \
    chmod 644 /etc/ssh/sshd_config.d/00-portal-hardening.conf && \
    sed -i -E 's/^[#[:space:]]*PermitRootLogin[[:space:]]+.*/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -i -E 's/^[#[:space:]]*PermitEmptyPasswords[[:space:]]+.*/PermitEmptyPasswords no/' /etc/ssh/sshd_config && \
    sed -i -E 's/^[#[:space:]]*PasswordAuthentication[[:space:]]+.*/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -i -E 's/^[#[:space:]]*KbdInteractiveAuthentication[[:space:]]+.*/KbdInteractiveAuthentication no/' /etc/ssh/sshd_config

RUN echo root:218 | chpasswd

# 可编程 ssh 启动器（Slurm+pyxis/enroot：读 SSH_PORT 决定端口）
# 注意：镜像内不要设置 ENV SSH_PORT，否则会盖住用户传入值
COPY start_ssh.sh /opt/start_ssh.sh
RUN chmod +x /opt/start_ssh.sh && \
    mkdir -p /run/sshd

# miniconda3 到 /opt/miniconda3；整体 a+rwX 让所有用户都能改 base；envs 1777 防互删
RUN wget https://mirrors.cernet.edu.cn/anaconda/miniconda/Miniconda3-py310_22.11.1-1-Linux-x86_64.sh && \
    chmod +x Miniconda3-py310_22.11.1-1-Linux-x86_64.sh && \
    bash Miniconda3-py310_22.11.1-1-Linux-x86_64.sh -b -p /opt/miniconda3 && \
    rm Miniconda3-py310_22.11.1-1-Linux-x86_64.sh && \
    /opt/miniconda3/bin/conda init && \
    printf '%s\n' \
        'channels:' \
        '  - defaults' \
        'default_channels:' \
        '  - https://mirrors.cernet.edu.cn/anaconda/pkgs/main' \
        '  - https://mirrors.cernet.edu.cn/anaconda/pkgs/r' \
        '  - https://mirrors.cernet.edu.cn/anaconda/pkgs/msys2' \
        'custom_channels:' \
        '  conda-forge: https://mirrors.cernet.edu.cn/anaconda/cloud' \
        '  pytorch: https://mirrors.cernet.edu.cn/anaconda/cloud' \
        '  nvidia: https://mirrors.cernet.edu.cn/anaconda/cloud' \
        'show_channel_urls: true' \
        > /opt/miniconda3/.condarc && \
    chmod -R a+rwX /opt/miniconda3 && \
    chmod 1777 /opt/miniconda3/envs

# conda 全局可用：source 进 login(/etc/profile) 与交互非登录(/etc/bash.bashrc) shell
RUN echo '. /opt/miniconda3/etc/profile.d/conda.sh' >> /etc/profile && \
    echo '. /opt/miniconda3/etc/profile.d/conda.sh' >> /etc/bash.bashrc

# 清掉构建期 conda 缓存（须在下方 ENV 之前，否则会漏清 /opt/miniconda3/pkgs）
RUN /opt/miniconda3/bin/conda clean -afy

# 运行时环境走镜像 ENV：pyxis/enroot 不执行 entrypoint、只注入镜像 ENV，故全终端生效。
ENV PATH="/opt/miniconda3/bin:${PATH}" \
    CUDA_HOME=/usr/local/cuda \
    LANG=en_US.UTF-8 \
    LC_CTYPE=zh_CN.UTF-8 \
    PIP_INDEX_URL=https://mirrors.cernet.edu.cn/pypi/web/simple \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://mirrors.cernet.edu.cn/pypi/web/simple \
    UV_NO_CACHE=1 \
    CONDA_PKGS_DIRS=/tmp/conda-pkgs

RUN printf '%s\n' \
        'export CUDA_HOME=/usr/local/cuda' \
        'export PIP_INDEX_URL=https://mirrors.cernet.edu.cn/pypi/web/simple' \
        'export PIP_NO_CACHE_DIR=1' \
        'export UV_DEFAULT_INDEX=https://mirrors.cernet.edu.cn/pypi/web/simple' \
        'export UV_NO_CACHE=1' \
        'export UV_LINK_MODE=copy' \
        'export CONDA_PKGS_DIRS=/tmp/conda-pkgs' \
        > /etc/profile.d/99-cluster-pkg-env.sh && chmod 644 /etc/profile.d/99-cluster-pkg-env.sh

RUN printf '%s\n' \
        '# 由镜像提供：包源与缓存设置（容器内新开的交互式 shell）' \
        '[ -f /etc/profile.d/99-cluster-pkg-env.sh ] && . /etc/profile.d/99-cluster-pkg-env.sh' \
        >> /etc/bash.bashrc

RUN printf '%s\n' \
        'CUDA_HOME=/usr/local/cuda' \
        'PIP_INDEX_URL=https://mirrors.cernet.edu.cn/pypi/web/simple' \
        'PIP_NO_CACHE_DIR=1' \
        'UV_DEFAULT_INDEX=https://mirrors.cernet.edu.cn/pypi/web/simple' \
        'UV_NO_CACHE=1' \
        'UV_LINK_MODE=copy' \
        'CONDA_PKGS_DIRS=/tmp/conda-pkgs' \
        >> /etc/environment

# conda 包缓存目录预置 1777（防 root 先建导致其他用户不可写）
RUN mkdir -p /tmp/conda-pkgs && \
    chmod 1777 /tmp/conda-pkgs

# vim：4 空格缩进
RUN echo -e '\nset ts=4\nset softtabstop=4\nset shiftwidth=4\nset expandtab\nset autoindent' >> /etc/vim/vimrc

WORKDIR /workspace
