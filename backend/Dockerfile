FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    TZ=Asia/Shanghai

# 系统依赖：ffmpeg(pydub) / libheif(HEIC) / libsndfile / 中文字体
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        libheif1 \
        fonts-noto-cjk \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pip 用清华源加速（云托管在境内构建）
COPY requirements.txt .
RUN pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        -r requirements.txt

COPY . .

# 1) 准备运行时目录
# 2) 软链系统中文字体到 /app/font.ttf，省去把 16MB 字体打进代码包
#    （core.py 优先读取 /app/font.ttf；如果代码包里已经带了 font.ttf，下面 -f 也会被覆盖软链）
RUN mkdir -p uploads outputs && \
    if [ ! -f /app/font.ttf ]; then \
        for f in /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
                 /usr/share/fonts/opentype/noto/NotoSansCJK.ttc \
                 /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc; do \
            if [ -f "$f" ]; then ln -sf "$f" /app/font.ttf && break; fi; \
        done; \
    fi

EXPOSE 8080

# 云托管探活：要求 / 或 /health 返回 2xx；我们两个都满足
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8080')+'/health', timeout=3).status==200 else 1)" || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
