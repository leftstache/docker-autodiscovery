#!/bin/sh -e

#myip="$(ifconfig | grep -oE "[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+" | head -1)"
myip="$(ifconfig | grep -oE "172\.[0-9]+\.[0-9]+\.[0-9]+" | head -1)"

echo "configuring dnsmasq"
echo "	IP Address=${myip}"
echo "	ZOOKEEPER_CONNECTION_STRING=${ZOOKEEPER_CONNECTION_STRING}"
echo "	DNS_DOMAIN=${DNS_DOMAIN}"
echo "	DNS_FORWARD_MAX=${DNS_FORWARD_MAX}"
echo "	DNS_CACHE_SIZE=${DNS_CACHE_SIZE}"
if [ ! -z "${DNS_NO_NEGCACHE}" ]; then
	echo "	DNS_NO_NEGCACHE=true"
fi

echo "address=/.${DNS_DOMAIN}/${myip}" > /etc/dnsmasq.conf

# Configure Logging
echo "log-facility=/dev/stdout" >> /etc/dnsmasq.conf
echo "log-queries" >> /etc/dnsmasq.conf

# Don't forward non-domains upstream
echo "domain-needed" >> /etc/dnsmasq.conf
echo "bogus-priv" >> /etc/dnsmasq.conf

# Don't forward names in hosts
#echo "no-hosts" >> /etc/dnsmasq.conf

# Max number of queries to handle at once
echo "dns-forward-max=${DNS_FORWARD_MAX}" >> /etc/dnsmasq.conf

# Max number of upstream names to keep cached
echo "cache-size=${DNS_CACHE_SIZE}" >> /etc/dnsmasq.conf

if [ ! -z "${DNS_NO_NEGCACHE}" ]; then
	echo "no-negcache" >> /etc/dnsmasq.conf
fi

# Let's also use any dns servers specified for this server
echo "resolv-file=/etc/resolv.conf" >> /etc/dnsmasq.conf

# We don't want dnsmasq to poll the resolv.conf file for changes, since we won't be changing it
echo "no-poll" >> /etc/dnsmasq.conf

#echo "starting dnsmasq"
#"dnsmasq" "-k" &
#dnsmasqpid=$!
#echo "started dnsmasq with pid: ${dnsmasqpid}"

python3 -u /bin/autoupdate.py
