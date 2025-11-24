FROM greatnonprofits/ccl-base:v5

RUN apt-get update
RUN apt-get install -y xmlsec1 libxml2 libxmlsec1t64 libxmlsec1t64-openssl

RUN wget https://s3.amazonaws.com/rds-downloads/rds-combined-ca-bundle.pem \
    -O /usr/local/share/ca-certificates/rds.crt
RUN update-ca-certificates

RUN wget https://ccl-prod.s3.us-west-1.amazonaws.com/phantomjs-2.1.1-linux-x86_64.tar.bz2
RUN tar xvjf phantomjs-2.1.1-linux-x86_64.tar.bz2 -C /usr/local/share/
RUN ln -sf /usr/local/share/phantomjs-2.1.1-linux-x86_64/bin/phantomjs /usr/local/bin

RUN mkdir /rapidpro
WORKDIR /rapidpro

COPY ./pyproject.toml /rapidpro/pyproject.toml
COPY ./poetry.lock /rapidpro/poetry.lock

RUN pip3 install --break-system-packages -U poetry==1.6.1

RUN poetry config virtualenvs.in-project true
RUN poetry install --no-dev --no-interaction --no-ansi
RUN poetry run pip install setuptools
RUN poetry run pip install --force-reinstall --no-cache-dir gunicorn

COPY . /rapidpro
RUN openssl genrsa -out /rapidpro/certs/sp-key.pem 2048
RUN openssl req -new -x509 -key /rapidpro/certs/sp-key.pem -out /rapidpro/certs/sp-cert.pem -days 3650 -subj "/CN=communityconnectlabs.saml"
COPY docker/docker.settings /rapidpro/temba/settings.py

RUN npm install --legacy-peer-deps --ignore-scripts
RUN npm rebuild node-sass --force || true

RUN poetry run python manage.py collectstatic --noinput

RUN echo "daemon off;" >> /etc/nginx/nginx.conf

RUN rm -f /etc/nginx/sites-enabled/default
RUN ln -sf /rapidpro/docker/nginx.conf /etc/nginx/sites-enabled/

RUN rm -f /rapidpro/temba/settings.pyc

COPY docker/entrypoint.sh /
RUN chmod +x /entrypoint.sh

RUN ln -s /usr/bin/python3 /usr/bin/python
RUN rm -rf /tmp/* /var/tmp/*[~]$

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

CMD ["app"]
