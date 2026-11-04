import json
import httpx

resp = httpx.get('http://127.0.0.1:8000/api/v1/alerts/stats')
print('Alerts Stats:', resp.status_code, json.dumps(resp.json(), indent=2))

resp_list = httpx.get('http://127.0.0.1:8000/api/v1/alerts')
data = resp_list.json()
print('Total Alerts Generated:', data['total'])
for a in data['alerts']:
    print(f"Alert #{a['id']} | {a['rule_code']} | Severity: {a['severity']} | Score: {a['risk_score']} | Status: {a['status']}")
    print(f"  Reason: {a['description']}")
