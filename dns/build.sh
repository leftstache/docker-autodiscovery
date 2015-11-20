#!/bin/bash

docker build --no-cache=true -t xahrepap/$(basename $PWD):latest .
docker rmi `docker images -q --filter=dangling=true`