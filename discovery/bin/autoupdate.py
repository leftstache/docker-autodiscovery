import subprocess
import signal
import sys
import json
import os
import time

from kazoo.client import KazooClient

import docker
from docker.utils import kwargs_from_env

AUTODISCOVER_TYPE_KEY = 'com.leftstache.autodiscover.type'
EXPOSED_PORT_KEY = 'com.leftstache.autodiscover.ports'
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
    kwargs['version'] = '1.21'

    with docker.Client(**kwargs) as dkr:
        register = Register(zk, dkr)

        current_time = int(time.time() * 1000)

        @zk.ChildrenWatch(BASE_PATH)
        def on_add(children):
            update_load_balancer(zk, BASE_PATH, children)

        print("Looking for existing containers")
        containers = dkr.containers(filters={"label": AUTODISCOVER_TYPE_KEY})
        for container in containers:
            register.on_started(container['Id'])

        print("Listening for Docker events")
        for event_string in dkr.events(since=current_time):
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

def update_load_balancer(zk, basepath, children):
    print("Zookeeper update: {}/{}".format(basepath, children))
    # servers = []
    # for child in children:
    #     container = zk.get("{}/{}".format(basepath, child))

class Register:
    def __init__(self, zk=KazooClient(), dkr=docker.Client()):
        self.zk = zk
        self.dkr = dkr
        self.started_containers = {}

    def on_started(self, container_id):
        container = self.dkr.inspect_container(container_id)
        name = container['Name']

        container_config = container['Config']
        labels = container_config['Labels']

        host_config = container['HostConfig']

        network_settings = container['NetworkSettings']
        networks = network_settings['Networks']

        if AUTODISCOVER_TYPE_KEY in labels:
            print("subscribing: {} ({})".format(name, container_id[:6]))

            network_ports = self.get_flat_ports(labels, network_settings['Ports'], host_config['PortBindings'])

            body = {
                'Id': container['Id'],
                'Name': container['Name'][1:],
                'Networks': networks,
                'Labels': labels,
                'Ports': network_ports
            }
            body_str = json.dumps(body)

            created_path = self.zk.create("{}/service-".format(BASE_PATH), value=bytes(body_str, 'utf-8'), sequence=True, ephemeral=True, makepath=True)
            self.started_containers[container_id] = created_path
            print("subscribed: {} ({}) as {}".format(name, container_id[:6], created_path))

    @staticmethod
    def get_flat_ports(labels, ports_map, port_bindings):
        if labels is not None and EXPOSED_PORT_KEY in labels:
            ports_str = labels[EXPOSED_PORT_KEY]
            result = [int(x) for x in ports_str.split(",")]
        else:
            result = []
            if ports_map is not None:
                for port, conf in ports_map.items():
                    if '/' in port:
                        port_int = int(port[:port.find('/')])
                    else:
                        port_int = int(port)
                    if port_int not in result:
                        result.append(port_int)
            if port_bindings is not None:
                for port, conf in port_bindings.items():
                    if '/' in port:
                        port_int = int(port[:port.find('/')])
                    else:
                        port_int = int(port)
                    if port_int not in result:
                        result.append(port_int)

        return result

    def on_destroyed(self, container_id):
        if container_id in self.started_containers:
            print("died: {}".format(container_id))
            container_zk_path = self.started_containers.pop(container_id)
            self.zk.delete(container_zk_path, recursive=True)
            print("unsubscribed: {} ({})".format(container_id, container_zk_path))


if __name__ == "__main__":
    main()
