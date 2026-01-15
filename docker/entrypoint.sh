#!/bin/bash
set -e

case $1 in
    app)
        poetry run python manage.py compress --extension=.haml --force
        poetry run python docker/clear-compressor-cache.py
        poetry run python manage.py migrate --noinput
        poetry run supervisord -n -c docker/supervisor-app.conf
    ;;
    celery)
        poetry run python manage.py migrate --noinput
        poetry run supervisord -n -c docker/supervisor-celery.conf
    ;;

esac

exec "$@"