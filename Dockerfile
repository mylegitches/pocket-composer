# flp2score web UI + headless MuseScore 4 + homr OMR (FL Studio projects, sheet-music scans, MusicXML, MIDI).
FROM python:3.12-slim-bookworm

ARG MUSESCORE_VERSION=4.7.6
ARG MUSESCORE_BUILD=261022093

# Libraries the MuseScore AppImage links against (Qt 6, audio, fonts). It runs with the
# "offscreen" Qt platform, so no X server is needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
        libgl1 libegl1 libopengl0 libglib2.0-0 libfontconfig1 libfreetype6 \
        libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libnss3 libasound2 libpulse0 libjack-jackd2-0 \
        libx11-6 libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxrender1 \
        libxi6 libsm6 libice6 libxext6 libxfixes3 libgssapi-krb5-2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Unpack the AppImage instead of running it (AppImages need FUSE, which containers usually lack).
RUN curl -fsSL -o /tmp/musescore.AppImage \
        "https://github.com/musescore/MuseScore/releases/download/v${MUSESCORE_VERSION}/MuseScore-Studio-${MUSESCORE_VERSION}.${MUSESCORE_BUILD}-x86_64.AppImage" \
    && chmod +x /tmp/musescore.AppImage \
    && cd /opt && /tmp/musescore.AppImage --appimage-extract > /dev/null \
    && mv /opt/squashfs-root /opt/musescore \
    && rm /tmp/musescore.AppImage

# MuseScore sets QT_QPA_PLATFORM itself (to xcb) unless MU_QT_QPA_PLATFORM says otherwise.
ENV MUSESCORE=/opt/musescore/AppRun \
    MU_QT_QPA_PLATFORM=offscreen \
    QT_QPA_PLATFORM=offscreen \
    HOME=/tmp/home \
    XDG_RUNTIME_DIR=/tmp/runtime \
    DATA_DIR=/app/data \
    OUTPUT_DIR=/app/output \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# homr's recognition models (~300 MB) are downloaded once here, not on the first scan.
RUN python -c "from homr import main as hm; hm.download_weights(segnet_use_gpu=False, transformer_use_gpu=False, coreml_encoder=False)"

COPY musescore_maker ./musescore_maker
COPY tabmaker ./tabmaker
COPY flp2score ./flp2score
COPY app ./app
COPY tools ./tools

RUN mkdir -p /tmp/home /tmp/runtime /app/data /app/output && chmod 700 /tmp/runtime

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
