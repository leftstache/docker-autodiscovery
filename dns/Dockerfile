FROM xahrepap/base

ENV DNS_DOMAIN discovery
ENV DNS_FORWARD_MAX 150
ENV DNS_CACHE_SIZE 150

ADD bin/* /bin/

ENTRYPOINT ["sh", "-c"]
CMD ["/bin/entrypoint.sh"]