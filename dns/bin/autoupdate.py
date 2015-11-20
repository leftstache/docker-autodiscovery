import subprocess
import signal
import sys
import json
import os
import socket

from kazoo.client import KazooClient

import docker
from docker.utils import kwargs_from_env

BASE_PATH = "/autodiscover/services"


def main():
    print("Starting Dnsmasq")
    dnsmasq_process = subprocess.Popen(['dnsmasq', '-k'], stdout=sys.stdout, stderr=sys.stderr)
    signal.signal(signal.SIGTERM, lambda s, f: on_sigterm(dnsmasq_process, zk, dkr))

    zookeeper_connection_string = os.environ['ZOOKEEPER_CONNECTION_STRING']
    print("Connecting to Zookeeper: {}".format(zookeeper_connection_string))
    zk = KazooClient(zookeeper_connection_string)
    zk.start()
    zk.ensure_path(BASE_PATH)

    print("Connecting to Docker")
    kwargs = kwargs_from_env()
    with docker.Client(**kwargs) as dkr:
        register = Register(zk, dkr)

        print("Listening for Docker events")
        for event_string in dkr.events():
            event = json.loads(event_string.decode("utf-8"))
            if 'status' in event:
                if event['status'] == 'create':
                    register.on_started(event['id'])
                elif event['status'] == 'die':
                    register.on_destroyed(event['id'])


def on_sigterm(dnsmasq_process, zk, dkr):
    print("Received terminate signal")

    if zk:
        try:
            print("Stopping Zookeeper Client")
            zk.stop()
        except Exception as e:
            print("Exception thrown while stopping Zookeeper Client {}".format(e))

    if dkr:
        try:
            print("Stopping Docker Client")
            dkr.stop()
        except Exception as e:
            print("Exception thrown while stopping Docker Client {}".format(e))

    if dnsmasq_process:
        try:
            print("Stopping Dnsmasq process")
            dnsmasq_process.terminate()
        except Exception as e:
            print("Exception thrown while stopping Dnsmasq {}".format(e))

    sys.exit(0)


class Register:
    def __init__(self, zk=KazooClient(), dkr=docker.Client()):
        self.zk = zk
        self.dkr = dkr
        self.started_containers = {}

    def on_started(self, container_id):
        container = self.dkr.inspect_container(container_id)
        name = container['Name']
        print("started: {} ({})".format(name[1:], container_id))

        body_str = json.dumps(container)

        created_path = self.zk.create("{}/service-".format(BASE_PATH), value=bytes(body_str, 'utf-8'), sequence=True, ephemeral=True, makepath=True)
        self.started_containers[container_id] = created_path
        print("subscribed: {} as {}".format(container_id, created_path))

    def on_destroyed(self, container_id):
        print("died: {}".format(container_id))

        if container_id in self.started_containers:
            container_zk_path = self.started_containers[container_id]
            self.zk.delete(container_zk_path, recursive=True)
            print("unsubscribed: {}".format(container_id))
        else:
            print("ignoring: {}".format(container_id))


if __name__ == "__main__":
    main()