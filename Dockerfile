FROM python:3.13-slim

LABEL org.opencontainers.image.title="HAM Repeaters CHIRP" \
      org.opencontainers.image.description="Portuguese amateur repeater channel list from ANACOM's license registry, with email alerts when it changes" \
      org.opencontainers.image.source="https://github.com/xhico/HAM_Repeaters_CHIRP" \
      org.opencontainers.image.licenses="MIT" \
      maintainer="xhico"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHIRP_OUTPUT_DIR=/data

WORKDIR /app

# Nothing to install: the project is standard library only, which is why
# there is no requirements.txt and no pip step here.
COPY *.py ./

# Run unprivileged; /data is the only path that needs to be writable.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin chirp \
    && mkdir -p /data \
    && chown -R chirp:chirp /data /app
USER chirp

VOLUME ["/data"]

# Unhealthy once the CSV stops being refreshed. The window is generous
# because the default interval is weekly: two missed cycles plus slack.
HEALTHCHECK --interval=30m --timeout=10s --start-period=10m --retries=3 \
    CMD python3 -c "import os,sys,time; p=os.environ.get('CHIRP_OUTPUT_DIR','/data')+'/chirp.csv'; i=float(os.environ.get('CHIRP_INTERVAL_HOURS','168')); sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < max(i,1)*3600*2.5 else 1)"

ENTRYPOINT ["python3", "service.py"]
