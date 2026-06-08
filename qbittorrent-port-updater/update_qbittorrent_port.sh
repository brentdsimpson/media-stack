#!/bin/bash
set -e

QBITTORRENT_URL="http://localhost:8080"
QBITTORRENT_USER="${QBITTORRENT_USER:?QBITTORRENT_USER env var is required}"
QBITTORRENT_PASS="${QBITTORRENT_PASS:?QBITTORRENT_PASS env var is required}"
FORWARDED_PORT_FILE="/tmp/gluetun/forwarded_port"
LAST_PORT=""

echo "Starting qBittorrent port updater..."

# Wait for qBittorrent to be ready
echo "Waiting for forwarded port file at $FORWARDED_PORT_FILE..."
until curl -s "${QBITTORRENT_URL}/api/v2/app/version" > /dev/null 2>&1; do
    echo "qBittorrent not ready yet, waiting..."
    sleep 5
done
echo "qBittorrent is ready!"

while true; do
    # Wait for forwarded port file
    if [ ! -f "$FORWARDED_PORT_FILE" ]; then
        echo "Waiting for PIA forwarded port file..."
        sleep 10
        continue
    fi

    # Read and validate port
    NEW_PORT=$(cat "$FORWARDED_PORT_FILE" | tr -d '[:space:]')
    if ! [[ "$NEW_PORT" =~ ^[0-9]+$ ]]; then
        echo "Invalid port: $NEW_PORT"
        sleep 10
        continue
    fi

    # Skip if port hasn't changed
    if [ "$NEW_PORT" = "$LAST_PORT" ]; then
        sleep 60  # Check every minute for port changes
        continue
    fi

    echo "New forwarded port detected: $NEW_PORT"

    # Try login
    COOKIE_JAR=$(mktemp)
    LOGIN_RESPONSE=$(curl -s -w "%{http_code}" -o /dev/null \
        --cookie-jar "$COOKIE_JAR" \
        --data "username=${QBITTORRENT_USER}&password=${QBITTORRENT_PASS}" \
        "$QBITTORRENT_URL/api/v2/auth/login")

    # 200 = normal login success; 204 = localhost auth bypass (LocalHostAuth=false)
    if [[ "$LOGIN_RESPONSE" != "200" && "$LOGIN_RESPONSE" != "204" ]]; then
        echo "qBittorrent login failed (HTTP $LOGIN_RESPONSE). Retrying..."
        rm -f "$COOKIE_JAR"
        sleep 10
        continue
    fi

    # Update port
    UPDATE_RESPONSE=$(curl -s -w "%{http_code}" -o /dev/null \
        --cookie "$COOKIE_JAR" \
        --header "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "json={\"listen_port\":${NEW_PORT},\"upnp\":false,\"random_port\":false}" \
        "$QBITTORRENT_URL/api/v2/app/setPreferences")

    rm -f "$COOKIE_JAR"

    if [ "$UPDATE_RESPONSE" = "200" ]; then
        echo "✓ qBittorrent listening port updated to $NEW_PORT"
        LAST_PORT="$NEW_PORT"
    else
        echo "Failed to update port (HTTP $UPDATE_RESPONSE)"
    fi

    sleep 60  # Check every minute
done