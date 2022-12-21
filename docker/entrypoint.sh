#!/bin/bash
set -e

case $1 in
    app)
        python3.9 manage.py compress --extension=.haml --force
        python3.9 docker/clear-compressor-cache.py
#        python3.9 manage.py migrate --noinput
        /usr/local/bin/supervisord -n -c docker/supervisor-app.conf
    ;;
    celery)
        python3.9 docker/clear-compressor-cache.py
#        python3.9 manage.py migrate --noinput
        /usr/local/bin/supervisord -n -c docker/supervisor-celery.conf
    ;;

esac

exec "$@"