#!/bin/bash
source /docker-scripts/common.sh

#----------------------------------
# Environment Setup
#----------------------------------
chmod +x /entrypoint.sh
update-alternatives --set python3 /usr/bin/python3.9
rm -f /usr/bin/python
ln -s /usr/bin/python3 /usr/bin/python
source ~/.bashrc
hash -r
python --version

#----------------------------------
# Setup Certs
#----------------------------------
print_heading "Setting up Certs"
# Download
wget https://s3.amazonaws.com/rds-downloads/rds-combined-ca-bundle.pem \
    -O /usr/local/share/ca-certificates/rds.crt
check_outcome "Downloaded RDS Certs"

# Update
update-ca-certificates
check_outcome "update-ca-certificates"

#----------------------------------
# Setup phantom
#----------------------------------
print_heading "Setting up PhantomJS 2.1.1"
wget https://ccl-prod.s3.us-west-1.amazonaws.com/phantomjs-2.1.1-linux-x86_64.tar.bz2 \
     -O /tmp/phantomjs-2.1.1-linux-x86_64.tar.bz2
check_outcome "Download PhantomJS"
tar xvjf /tmp/phantomjs-2.1.1-linux-x86_64.tar.bz2 -C /usr/local/share/
check_outcome "Extract PhantomJS"
ln -sf /usr/local/share/phantomjs-2.1.1-linux-x86_64/bin/phantomjs /usr/local/bin
check_outcome "Link PhantomJS"

#----------------------------------
# Setup Python Environment
#----------------------------------
print_heading "Setting Up Python Environment"
pip install --upgrade pip setuptools
check_outcome "Upgrade pip and setuptools"
source ~/.bashrc
hash -r
pip install -U poetry
poetry export --without-hashes --output pip-freeze.txt
check_outcome "Export Poetry"
pip install -r pip-freeze.txt
check_outcome "Install Dependencies"

##----------------------------------
## Setup Node Environment
##----------------------------------
print_heading "Setting Up Node Environment"
npm install
check_outcome "Install Node Modules"

##----------------------------------
## Setup Django Environment
##----------------------------------
#print_heading "Setting Up Django"
#python manage.py collectstatic --noinput
#check_outcome "Collect Static"
## Remove compiled python files
#rm -f /rapidpro/temba/settings.pyc
#check_outcome "Remove cached temba/settings.pyc file"

#----------------------------------
# Setup Nginx
#----------------------------------
print_heading "Setting Up Nginx"
echo "daemon off;" >> /etc/nginx/nginx.conf
check_outcome "Disable Daemon"
rm -f /etc/nginx/sites-enabled/default
check_outcome "Remove Default Site"
ln -sf /rapidpro/docker/nginx.sandbox.conf /etc/nginx/sites-enabled/ccl-rapidpro.conf
check_outcome "Link Nginx Config"

#----------------------------------
# Purging Temp Files
#----------------------------------
print_heading "Purging Tmp"
rm -rf /tmp/* /var/tmp/*[~]$
