FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        asterisk \
        asterisk-config \
        asterisk-core-sounds-en-gsm \
        ca-certificates \
        # recordings are converted to Opus the moment MixMonitor closes them
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /entrypoint.sh
COPY rec-postprocess.sh /usr/local/bin/rec-postprocess.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/rec-postprocess.sh \
 && mkdir -p /var/spool/asterisk/recordings \
 && chown asterisk:asterisk /var/spool/asterisk/recordings
ENTRYPOINT ["/entrypoint.sh"]
