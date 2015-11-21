FROM alpine:3.2

RUN apk -U add python3 py-pip nginx dnsmasq
RUN pip3 install docker-py kazoo

ADD conf/nginx.conf /etc/nginx/nginx.conf

EXPOSE 80 53 53/udp