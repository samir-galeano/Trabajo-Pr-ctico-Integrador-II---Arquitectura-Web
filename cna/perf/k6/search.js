// C2 (F7-E6): búsqueda del monolito vs. GET /v2/search de search-svc,
// ambas por HTTPS a través del gateway (VM-1).
//
// Variables:
//   TARGET     monolith | service
//   VUS        usuarios concurrentes (10 = misma concurrencia que la Fase 4, 50 = carga alta)
//   DURATION   duración (por defecto 60s)
//   TERM       término de búsqueda (por defecto nginx)
//   MONOLITH_URL, GATEWAY_URL
//   BOOKSTACK_EMAIL / BOOKSTACK_PASSWORD  si la búsqueda del monolito requiere sesión
//   API_TOKEN  token Bearer de /v2/* (API_AUTH_TOKEN); run-k6.sh lo toma de search-svc/.env
//   OUT        archivo JSON de resumen (lo escribe handleSummary)
//
// Ejemplo (ver cna/perf/run-k6.sh):
//   k6 run -e TARGET=service -e VUS=10 search.js

import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const TARGET = __ENV.TARGET || 'service';
const VUS = parseInt(__ENV.VUS || '10', 10);
const TERM = __ENV.TERM || 'nginx';
const MONOLITH_URL = __ENV.MONOLITH_URL || 'https://bookstack.local';
const GATEWAY_URL = __ENV.GATEWAY_URL || 'https://api.bookstack.local';

const rateLimited = new Counter('rate_limited_429');

export const options = {
  insecureSkipTLSVerify: true,
  scenarios: {
    search: {
      executor: 'constant-vus',
      vus: VUS,
      duration: __ENV.DURATION || '60s',
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

function login() {
  const page = http.get(`${MONOLITH_URL}/login`);
  const match = /name="_token" value="([^"]+)"/.exec(page.body || '');
  const res = http.post(`${MONOLITH_URL}/login`, {
    _token: match ? match[1] : '',
    email: __ENV.BOOKSTACK_EMAIL,
    password: __ENV.BOOKSTACK_PASSWORD,
  }, { redirects: 0, tags: { name: 'login' } });
  check(res, { 'login 302': (r) => r.status === 302 });
}

export default function () {
  let res;

  if (TARGET === 'monolith') {
    if (__ITER === 0 && __ENV.BOOKSTACK_EMAIL) {
      login();
    }
    res = http.get(`${MONOLITH_URL}/search?term=${TERM}`, {
      redirects: 0,
      tags: { name: 'monolith_search' },
    });
  } else {
    res = http.get(`${GATEWAY_URL}/v2/search?q=${TERM}`, {
      headers: __ENV.API_TOKEN ? { Authorization: `Bearer ${__ENV.API_TOKEN}` } : {},
      tags: { name: 'service_search' },
    });
  }

  if (res.status === 429) {
    rateLimited.add(1);
  }

  check(res, {
    'status 200': (r) => r.status === 200,
  });
}

export function handleSummary(data) {
  const m = data.metrics;
  const value = (metric, stat) => (m[metric] ? m[metric].values[stat] : 0);

  const result = {
    target: TARGET,
    vus: VUS,
    requests: value('http_reqs', 'count'),
    rps: value('http_reqs', 'rate'),
    error_rate: value('http_req_failed', 'rate'),
    rate_limited_429: value('rate_limited_429', 'count'),
    duration_ms: {
      avg: value('http_req_duration', 'avg'),
      p50: value('http_req_duration', 'med'),
      p95: value('http_req_duration', 'p(95)'),
      p99: value('http_req_duration', 'p(99)'),
      max: value('http_req_duration', 'max'),
    },
    tls_handshake_avg_ms: value('http_req_tls_handshaking', 'avg'),
  };

  const line = `| ${TARGET} | ${VUS} | ${result.requests} | ${result.rps.toFixed(1)} | `
    + `${(result.error_rate * 100).toFixed(2)} % | ${result.rate_limited_429} | `
    + `${result.duration_ms.avg.toFixed(1)} | ${result.duration_ms.p95.toFixed(1)} | `
    + `${result.duration_ms.p99.toFixed(1)} |\n`;

  const out = { stdout: `\n${JSON.stringify(result, null, 2)}\n${line}` };
  if (__ENV.OUT) {
    out[__ENV.OUT] = JSON.stringify(result, null, 2);
    out[__ENV.OUT.replace(/\.json$/, '.row.md')] = line;
  }
  return out;
}
