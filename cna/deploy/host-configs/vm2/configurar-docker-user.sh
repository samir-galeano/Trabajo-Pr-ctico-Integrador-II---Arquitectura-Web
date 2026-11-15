#!/bin/bash
# Reglas de la cadena DOCKER-USER (VM-2). Docker evalúa esta cadena antes que
# sus propias reglas, así que es el único lugar donde se pueden filtrar los
# puertos que publica (UFW no los alcanza).
#
#   - Servicios (8000-8004), MinIO (9000) y Loki (3100): solo VM-1 (gateway).
#   - Grafana (3000), Prometheus (9090) y Jaeger (16686): solo la red del laboratorio.
#
# Las reglas se aplican SOLO a lo que entra por la interfaz externa (-i):
# DOCKER-USER también ve el tráfico entre contenedores de la misma red
# (br_netfilter), y sin -i se cortaría export→MinIO, Alloy→Loki,
# Grafana→Prometheus o Prometheus→servicios.
set -e

VM1_IP="${VM1_IP:-192.168.0.17}"
LAB_NET="${LAB_NET:-192.168.0.0/24}"
# Interfaz por la que llega VM-1 (la LAN de las VM)
EXT_IF="${EXT_IF:-$(ip route get "$VM1_IP" | grep -o 'dev [^ ]*' | cut -d' ' -f2)}"
[ -n "$EXT_IF" ] || { echo "No se pudo detectar la interfaz externa; definir EXT_IF" >&2; exit 1; }

iptables -F DOCKER-USER

iptables -A DOCKER-USER \
  -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

# Puertos que solo usa el gateway (VM-1)
for ports in 8000:8004 9000 3100; do
  iptables -A DOCKER-USER -i "$EXT_IF" \
    -s "$VM1_IP/32" -p tcp \
    -m conntrack --ctorigdstport "$ports" -j RETURN

  iptables -A DOCKER-USER -i "$EXT_IF" \
    -p tcp -m conntrack --ctorigdstport "$ports" -j DROP
done

# Observabilidad: desde la red del laboratorio (anfitrión incluido)
for ports in 3000 9090 16686; do
  iptables -A DOCKER-USER -i "$EXT_IF" \
    -s "$LAB_NET" -p tcp \
    -m conntrack --ctorigdstport "$ports" -j RETURN

  iptables -A DOCKER-USER -i "$EXT_IF" \
    -p tcp -m conntrack --ctorigdstport "$ports" -j DROP
done

echo "DOCKER-USER configurada (interfaz externa: $EXT_IF)"
iptables -L DOCKER-USER -n -v
