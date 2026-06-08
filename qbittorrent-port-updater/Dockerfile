FROM alpine:3.20
RUN apk add --no-cache bash curl
COPY update_qbittorrent_port.sh /scripts/update_qbittorrent_port.sh
RUN chmod +x /scripts/update_qbittorrent_port.sh
ENTRYPOINT ["/bin/bash", "/scripts/update_qbittorrent_port.sh"]
