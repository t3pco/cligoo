#!/bin/sh
set -e

# If no arguments or 'daemon' is specified, keep the container running forever
if [ "$#" -eq 0 ] || [ "$1" = "daemon" ]; then
    echo "[cligoo] Running in daemon mode. Container is active and ready for backup scripts / docker exec."
    
    # If custom startup/entrypoint script exists, execute it
    if [ -x "/scripts/entrypoint.sh" ]; then
        echo "[cligoo] Executing custom /scripts/entrypoint.sh..."
        exec /scripts/entrypoint.sh
    fi

    exec sleep infinity
fi

# If first argument is a known cligoo command or flag, invoke cligoo
case "$1" in
    config|login|logout|token|whoami|quota|usage|ls|tree|search|stats|cat|open|view|mkdir|upload|download|get|mv|rename|rm|restore|empty-trash|share|unshare|links|history|devices|shell|sync|-*)
        exec cligoo "$@"
        ;;
    cligoo)
        exec "$@"
        ;;
    *)
        exec "$@"
        ;;
esac

