#!/bin/bash
set -e

case $1 in
    app)
        python3.9 manage.py compress --extension=.haml --force
        python3.9 docker/clear-compressor-cache.py
        python3.9 manage.py migrate --noinput
        /usr/local/bin/supervisord -n -c docker/supervisor-app.conf
    ;;
    celery)
        python3.9 docker/clear-compressor-cache.py
        python3.9 manage.py migrate --noinput
        /usr/local/bin/supervisord -n -c docker/supervisor-celery.conf
    ;;
     sandbox)
        if [ -n "${NPM_INIT}" ] && [ "${NPM_INIT}" != "0" ]; then
          npm install
          python manage.py collectstatic --noinput
        fi
        python3.9 manage.py compress --extension=.haml --force
        python3.9 docker/clear-compressor-cache.py
        python3.9 manage.py migrate --noinput
        /usr/local/bin/supervisord -n -c docker/supervisor-app.conf
    ;;

esac

exec "$@"
