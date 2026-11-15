"""Genera el tablero de Grafana. Uso: python generate_dashboard.py dashboards/cna-red.json"""
import json, sys

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
panels = []
y = 0
pid = 0

def nid():
    global pid
    pid += 1
    return pid

def row(title):
    global y
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    y += 1

def ts(title, targets, x, w, h=8, unit="short", desc="", stack=False, extra=None):
    p = {
        "type": "timeseries", "title": title, "id": nid(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10,
                        "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": [{"datasource": PROM, "refId": chr(65 + i), "expr": e, "legendFormat": l}
                    for i, (e, l) in enumerate(targets)],
    }
    if extra:
        p["fieldConfig"]["defaults"].update(extra)
    panels.append(p)

def stat(title, expr, legend, x, w, h=4, mappings=None, thresholds=None, unit="short", desc=""):
    panels.append({
        "type": "stat", "title": title, "id": nid(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {"unit": unit, "mappings": mappings or [],
                        "thresholds": thresholds or {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
                        "overrides": []},
        "options": {"colorMode": "background", "graphMode": "none", "textMode": "value_and_name",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "orientation": "auto"},
        "targets": [{"datasource": PROM, "refId": "A", "expr": expr, "legendFormat": legend, "instant": True}],
    })

UPDOWN = [{"type": "value", "options": {"0": {"text": "CAÍDO", "color": "red"}, "1": {"text": "OK", "color": "green"}}}]
RED_GREEN = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}

row("Estado")
stat("Servicios (up)", 'max by (job) (up{job!="prometheus"})', "{{job}}", 0, 14, mappings=UPDOWN, thresholds=RED_GREEN,
     desc="1 = Prometheus lee /metrics del servicio. Cae a 0 en X1/X4.")
stat("Circuito search-svc → BookStack", "max(bookstack_circuit_state)", "estado", 14, 5,
     mappings=[{"type": "value", "options": {"0": {"text": "CERRADO", "color": "green"},
                                              "1": {"text": "SEMIABIERTO", "color": "orange"},
                                              "2": {"text": "ABIERTO", "color": "red"}}}],
     thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 2}]},
     desc="Experimento X5.")
stat("Alertas disparadas", 'count(ALERTS{alertstate="firing"}) or vector(0)', "alertas", 19, 5,
     thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]})
y += 4

row("RED por servicio (sin /metrics, /healthz, /readyz)")
ts("Rate: peticiones por segundo", [("job:http_requests:rate1m", "{{job}}")], 0, 8, unit="reqps")
ts("Errors: proporción de 5xx", [("job:http_errors:ratio1m", "{{job}}")], 8, 8, unit="percentunit",
   desc="La alerta HighErrorRate se dispara sobre 5 % durante 1 minuto.",
   extra={"min": 0, "max": 1})
ts("Duration: p50 / p95 / p99", [
    ("job:http_request_duration_seconds:p50", "{{job}} p50"),
    ("job:http_request_duration_seconds:p95", "{{job}} p95"),
    ("job:http_request_duration_seconds:p99", "{{job}} p99"),
], 16, 8, unit="s")
y += 8
ts("Peticiones por código HTTP", [("sum by (job, code) (job_code:http_requests:rate1m)", "{{job}} {{code}}")], 0, 24, h=7, unit="reqps", stack=True)
y += 7

row("Colas")
ts("JetStream: eventos pendientes por consumidor", [
    ("jetstream_consumer_num_pending", "{{stream_name}} / {{consumer_name}} pendientes"),
    ("jetstream_consumer_num_ack_pending", "{{stream_name}} / {{consumer_name}} sin ack"),
], 0, 8, desc="Sube durante X2 (audit-svc detenido) y vuelve a 0 al recuperarse.")
ts("DLQ: mensajes en BOOKSTACK_DLQ", [
    ('jetstream_stream_total_messages{stream_name="BOOKSTACK_DLQ"}', "en DLQ"),
    ('jetstream_stream_total_messages{stream_name="BOOKSTACK"}', "en BOOKSTACK"),
], 8, 8)
ts("Exportaciones: cola Redis y throughput", [
    ('export_queue_depth', "cola {{queue}}"),
    ('(sum(rate(export_worker_jobs_total{status="done"}[1m])) or vector(0)) * 60', "jobs done / min"),
    ('count(up{job="export-worker"} == 1)', "workers activos"),
], 16, 8, desc="C2: escalar export-worker de 1 a 3 y comparar jobs/min.")
y += 8

row("search-svc → API de BookStack")
ts("Llamadas por resultado", [("sum by (outcome) (rate(bookstack_client_requests_total[1m]))", "{{outcome}}")], 0, 12, unit="reqps", stack=True)
ts("Reintentos y estado del circuito", [
    ("rate(bookstack_client_retries_total[1m])", "reintentos/s"),
    ("max(bookstack_circuit_state)", "circuito (0 cerrado, 2 abierto)"),
], 12, 12)
y += 8

row("Logs")
panels.append({
    "type": "logs", "title": "Logs por servicio y request_id", "id": nid(), "datasource": LOKI,
    "description": "Escribir un request_id arriba para ver la misma operación en gateway, event-bridge, search-svc y audit-svc.",
    "gridPos": {"h": 14, "w": 24, "x": 0, "y": y},
    "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True},
    "targets": [{"datasource": LOKI, "refId": "A", "expr": '{service=~"$service"} |= "$request_id"'}],
})
y += 14

dashboard = {
    "uid": "cna-red", "title": "BookStack CNA - RED, colas y logs", "tags": ["cna", "red"],
    "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "10s",
    "time": {"from": "now-30m", "to": "now"},
    "templating": {"list": [
        {"name": "service", "label": "Servicio", "type": "query", "datasource": LOKI,
         "query": {"label": "service", "stream": "", "type": 1, "refId": "LokiVariableQueryEditor-VariableQuery"},
         "definition": "label_values(service)", "includeAll": True, "multi": True, "allValue": ".+",
         "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "refresh": 2},
        {"name": "request_id", "label": "request_id", "type": "textbox", "query": "",
         "current": {"text": "", "value": ""}},
    ]},
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                              "enable": True, "hide": True, "name": "Annotations & Alerts", "type": "dashboard"}]},
    "panels": panels,
}
json.dump(dashboard, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("panels:", len(panels))
