#!/usr/bin/env bash
set -eEuo pipefail # fail if any errors are encountered

# This script is used to start the MiraMedia service.

# Initialize config if it doesn't exist
CONFIG_DIR=${MIRAMEDIA_CONFIG_DIR:-${CONFIG_DIR:-/app/config}}
export MIRAMEDIA_CONFIG_DIR="$CONFIG_DIR"
CONFIG_FILE="$CONFIG_DIR/config.toml"
EXAMPLE_CONFIG="/app/config.example.toml"

echo "Checking configuration setup..."

# Create config directory if it doesn't exist
if [ ! -d "$CONFIG_DIR" ]; then
    echo "Creating config directory: $CONFIG_DIR"
    mkdir -p "$CONFIG_DIR"
fi

# Copy example config if config.toml doesn't exist
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config file not found. Copying example config to: $CONFIG_FILE"
    if [ -f "$EXAMPLE_CONFIG" ]; then
        cp "$EXAMPLE_CONFIG" "$CONFIG_FILE"
        echo "Example config copied successfully!"
        echo "Please edit $CONFIG_FILE to configure MiraMedia for your environment."
        echo "Important: Make sure to change the token_secret value!"
    else
        echo "ERROR: Example config file not found at $EXAMPLE_CONFIG"
        exit 1
    fi
else
    echo "Config file found at: $CONFIG_FILE"
fi

# check if running as root, if yes, fix permissions
if [ "$(id -u)" = '0' ]; then
    echo "Running as root. Ensuring file permissions for miramedia user..."
    chown -R miramedia:miramedia "$CONFIG_DIR"

    if [ -d "/data" ]; then
        if [ "$(stat -c '%U' /data)" != "miramedia" ]; then
            echo "Fixing ownership of /data (this may take a while for large media libraries)..."
            chown -R miramedia:miramedia /data
        else
            echo "/data ownership is already correct."
        fi
    fi
else
    echo "Running as non-root user ($(id -u)). Skipping permission fixes."
    echo "Note: Ensure your host volumes are manually set to the correct permissions."
fi

echo "Running DB migrations..."
if [ "$(id -u)" = '0' ]; then
   migrate_cmd=(gosu miramedia uv run alembic upgrade head)
else
   migrate_cmd=(uv run alembic upgrade head)
fi
# `if ! cmd` so set -e doesn't abort before the banner. A bare alembic failure
# (e.g. a stored alembic_version that no on-disk revision matches) otherwise
# exits with just a Python traceback, and with restart:always the container
# silently crash-loops. Surface a clear reason in `docker logs`.
if ! "${migrate_cmd[@]}"; then
    echo "============================================================" >&2
    echo "FATAL: 'alembic upgrade head' failed — backend NOT started." >&2
    echo "Check the traceback above (stored alembic_version may not match" >&2
    echo "any on-disk revision). The container will restart and retry." >&2
    echo "============================================================" >&2
    exit 1
fi

echo "Starting MiraMedia backend service..."
echo ""
echo "   LOGIN INFORMATION:"
echo "   If this is a fresh installation, a default admin user will be created automatically."
echo "   Check the application logs for the login credentials."
echo "   You can also register a new user and it will become admin if the email"
echo "   matches one of the admin_emails in your config.toml"
echo ""

# Start a virtual display for the Cloudflare bypass (headful browser in container)
if [ -z "${DISPLAY:-}" ] && command -v Xvfb > /dev/null 2>&1; then
    # A crashed Xvfb leaves a stale lock (/tmp/.X99-lock + the X11 socket) behind.
    # /tmp survives a `docker restart` (restart != recreate), so without this the
    # new Xvfb sees :99 "in use", exits instantly, and the headful browser can
    # never open its DevTools port — the bypass then just loops "version probe
    # failed; Connection refused" until every solve times out. Clear it first so
    # the display always comes back cleanly on restart.
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true
    echo "Starting Xvfb virtual display..."
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp -ac &
    export DISPLAY=:99
fi

# Start a per-container dbus session bus. Chromium probes dbus on launch and
# loops on retries for ~25s before giving up when no bus is reachable, which
# causes the cloudflare bypass to time out waiting for the DevTools port.
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && command -v dbus-launch > /dev/null 2>&1; then
    echo "Starting dbus session bus..."
    eval "$(dbus-launch --sh-syntax)"
    export DBUS_SESSION_BUS_ADDRESS DBUS_SESSION_BUS_PID
fi

DEVELOPMENT_MODE=${MIRAMEDIA_MISC__DEVELOPMENT:-FALSE}
PORT=${PORT:-8000}
WORKERS="${MIRAMEDIA_WEB_WORKERS:-1}"

# Workers > 1: the in-process taskiq scheduler MUST run in a separate
# container with MIRAMEDIA_SCHEDULER_DISABLED=false, while these API
# workers are launched with MIRAMEDIA_SCHEDULER_DISABLED=true. Otherwise
# each worker spawns its own Receiver and every scheduled task fires N
# times. See .env.example + the commented miramedia-scheduler block in
# docker-compose.yaml.
echo "Starting with $WORKERS uvicorn worker(s)."
if [ "$WORKERS" -gt 1 ] && [ "${MIRAMEDIA_SCHEDULER_DISABLED:-false}" != "true" ]; then
    echo "WARNING: MIRAMEDIA_WEB_WORKERS=$WORKERS but MIRAMEDIA_SCHEDULER_DISABLED is not 'true'."
    echo "         Scheduled tasks will fire $WORKERS times. Set MIRAMEDIA_SCHEDULER_DISABLED=true"
    echo "         on the API workers and run a dedicated scheduler container."
fi

if [ "$DEVELOPMENT_MODE" == "TRUE" ]; then
    echo "Development mode is enabled, enabling auto-reload..."
    # --reload is incompatible with --workers; reload always implies 1 worker.
    DEV_OPTIONS="--reload"
    WORKER_OPTIONS=""
else
    DEV_OPTIONS=""
    WORKER_OPTIONS="--workers $WORKERS"
fi

# exec the server so it replaces this shell and becomes the direct child of
# tini (init: true). Backgrounding it + `trap kill` did NOT reliably deliver
# SIGTERM to the actual uvicorn process on `docker stop`, so on the NAS's
# frequent restarts the server was SIGKILLed after the stop-grace — severing
# asyncpg connections mid-write and truncating the libtorrent resume-data
# flush. With exec, tini → uvicorn directly and SIGTERM triggers a graceful
# lifespan shutdown. The Xvfb/dbus background jobs are orphaned and reaped by
# tini (they're throwaway and die with the container).
if [ "$(id -u)" = '0' ]; then
    exec gosu miramedia uv run fastapi run /app/miramedia/main.py --port "$PORT" --proxy-headers $WORKER_OPTIONS $DEV_OPTIONS
else
    exec uv run fastapi run /app/miramedia/main.py --port "$PORT" --proxy-headers $WORKER_OPTIONS $DEV_OPTIONS
fi