#!/bin/bash
set -e

iptables -F DOCKER-USER

iptables -A DOCKER-USER \
  -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

iptables -A DOCKER-USER \
  -s 192.168.0.17/32 -p tcp \
  -m conntrack --ctorigdstport 8000:8004 -j RETURN

iptables -A DOCKER-USER \
  -p tcp -m conntrack --ctorigdstport 8000:8004 -j DROP
