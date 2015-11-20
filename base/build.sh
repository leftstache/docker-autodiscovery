#!/bin/bash

docker build --no-cache=true -t leftstache/discovery-base:latest .
docker rmi `docker images -q --filter=dangling=true`