FROM python:3.10-slim

# 禁止生成字节码缓存，立即输出日志，并避免保留 pip 下载缓存。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 设置容器内的应用工作目录。
WORKDIR /app

# 创建无特权运行账号，降低容器以 root 身份运行的风险。
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

# 先复制依赖清单以利用 Docker 构建缓存，再安装锁定版本依赖。
COPY requirements.txt requirements.lock ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.lock

# 复制项目代码，并为运行数据目录设置正确权限。
COPY . .
RUN mkdir -p /app/runtime/reports /app/runtime/imports && chown -R app:app /app

# 使用无特权账号启动服务，并声明 Web 服务端口。
USER app
EXPOSE 5000

# 定期调用存活检查接口，供 Docker 判断 Web 容器健康状态。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/v1/health/live', timeout=3)"

# 使用 Waitress 启动 Flask 应用，监听容器内的 5000 端口。
CMD ["waitress-serve", "--listen=0.0.0.0:5000", "web.app:app"]
