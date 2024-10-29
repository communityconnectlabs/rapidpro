#!/bin/bash

HP=1
print_heading() {
  echo ""
  echo "$HP. $1"
  echo "------------------------------------------------------------"
  HP=$((HP + 1))
}

early_exit() {
  if [ $1 -eq 0 ]
  then
    echo "[Success] Proceeding"
  else
    echo "[Failure] Halting Build"
    exit $1
  fi
}

update_apt() {
  print_heading "Refresh Apt Cache"
  rm -f /var/cache/apt/partial/*  && \
  rm -f /var/cache/apt/*.deb && \
  yes "" | apt-get update
  early_exit $?
}

purge_apt() {
  print_heading "Purging Apt Cache"
  rm -rf /var/lib/apt/lists/*  && \
  rm -f /var/cache/apt/partial/*  && \
  rm -f /var/cache/apt/*.deb
  early_exit $?
}

log_verbose() {
  if [ "$VERBOSE" = true ] ; then
    echo "$1"
  else
    echo "Suppressed"
  fi
}

check_outcome() {
  OUTCOME=$?
  echo $1
  log_verbose $1
  early_exit $OUTCOME
}
