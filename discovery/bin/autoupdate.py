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
SERVER_WEIGHT_KEY = 'com.leftstache.autodiscover.weight'
EXPOSED_PORT_KEY = 'com.leftstache.autodiscover.ports'
BASE_PATH = "/autodiscover/services"


def main():
    print("Starting Dnsmasq")
    dnsmasq_process = subprocess.Popen(['dnsmasq', '-k', '--port=8053'], stdout=sys.stdout, stderr=sys.stderr)
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
        host_networks = get_and_normalize_networks(dkr)
        register = Register(zk, dkr, host_networks)

        current_time = int(time.time() * 1000)

        @zk.ChildrenWatch(BASE_PATH)
        def on_add(children):
            try:
                update_load_balancer(zk, dkr, BASE_PATH, children, host_networks)
            except Exception as e:
                print("Error while updating load balancer: {}".format(e))

        print("Looking for existing containers")
        containers = dkr.containers(filters={"label": AUTODISCOVER_TYPE_KEY})
        for container in containers:
            try:
                register.on_started(container['Id'])
            except Exception as e:
                print("Error while running on_started during warm up: {}".format(e))

        print("Listening for Docker events")
        for event_string in dkr.events(since=current_time):
            event = json.loads(event_string.decode("utf-8"))
            if 'status' in event:
                if event['status'] == 'start':
                    try:
                        register.on_started(event['id'])
                    except Exception as e:
                        print("Error while running on_started: {}".format(e))
                elif event['status'] == 'die':
                    try:
                        register.on_destroyed(event['id'])
                    except Exception as e:
                        print("Error while running on_destroyed: {}".format(e))


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


def get_and_normalize_networks(dkr):
    result = {}
    networks = dkr.networks()
    for network in networks:
        result[network['Id']] = network
    return result


def update_load_balancer(zk, dkr, basepath, children, host_networks):
    print("Zookeeper update: {}/{}".format(basepath, children))

    domain = os.getenv('DNS_DOMAIN', 'discovery')

    servers = {}
    for child in children:
        container_json, stat = zk.get("{}/{}".format(basepath, child))
        container = json.loads(container_json.decode("utf-8"))

        if 'Ports' not in container or container['Ports'] is None:
            print("Cannot load balance without ports: {} ({})", container['Name'], child)
            continue

        ip = find_ip(container, host_networks)
        if ip is not None:
            server = {
                'ip': ip,
                'ports': container['Ports']
            }

            if SERVER_WEIGHT_KEY in container['Labels']:
                server['weight'] = container['Labels'][SERVER_WEIGHT_KEY]

            server_name = container['Labels'][AUTODISCOVER_TYPE_KEY]
            if server_name not in servers:
                servers[server_name] = []
            servers[server_name].append(server)

    os.makedirs('/etc/nginx/conf.d', exist_ok=True)
    conf_file = open('/etc/nginx/conf.d/default.conf', mode='w')

    for name, server_list in servers.items():
        unique_ports = {}

        print("upstream {} {{".format(name), file=conf_file)
        for server in server_list:
            for port in server['ports']:
                unique_ports[port] = None
                weight = ""
                if 'weight' in server:
                    weight = " weight=".format(server['weight'])
                print("\tserver {}:{}{};".format(server['ip'], port, weight), file=conf_file)
        print("}", file=conf_file)

        # TODO: handle tcp -v- http
        print("server {", file=conf_file)
        for port, _ignore in unique_ports.items():
            print("\tlisten\t{};".format(port), file=conf_file)
        print("\tserver_name\t{}.{};".format(name, domain), file=conf_file)
        # TODO: configure logging better
        print("\taccess_log\t/dev/stdout;", file=conf_file)

        print("\tlocation / {", file=conf_file)
        print("\t\tproxy_pass   http://{};".format(name), file=conf_file)
        print("\t}", file=conf_file)

        print("}", file=conf_file)


def find_ip(zk_container, host_networks):
    """
    Finds the IP address our docker host can reach the given container at, based on configured networks.
    :param zk_container: The container data from Zookeeper
    :param host_networks: The networks of the docker host this python script is running on
    :return:
    """
    if 'Networks' not in zk_container or zk_container['Networks'] is None:
        return None

    for container_network_name, container_network in zk_container['Networks'].items():
        if container_network['Id'] in host_networks:
            return container_network['IPAddress']

    return None


class Register:
    def __init__(self, zk=KazooClient(), dkr=docker.Client(), host_networks={}):
        self.zk = zk
        self.dkr = dkr
        self.started_containers = {}
        self.host_networks = host_networks

    def on_started(self, container_id):
        container = self.dkr.inspect_container(container_id)
        name = container['Name']

        container_config = container['Config']
        labels = container_config['Labels']

        host_config = container['HostConfig']

        network_settings = container['NetworkSettings']
        networks = self.add_id_to_network(network_settings['Networks'])

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

    def add_id_to_network(self, container_networks):
        if container_networks is None:
            return None

        for host_network_id, host_network in self.host_networks.items():
            if host_network['Name'] in container_networks:
                container_networks[host_network['Name']]['Id'] = host_network_id
        return container_networks


if __name__ == "__main__":
    main()
