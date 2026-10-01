"""Opt-in local integration check. Mutates only the fixed disposable labs.

Not collected by pytest. Run after the Bash launcher, with a private controller
key in KRAVEL_TEST_CONTROL_KEY and explicit --allow-disposable-changes.
Never prints keys, cookies, previews, or approval credentials; never approves a fix.
"""
import argparse
import http.cookiejar
import json
import os
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-disposable-changes', action='store_true')
    args = parser.parse_args()
    key = os.environ.pop('KRAVEL_TEST_CONTROL_KEY', '')
    if not args.allow_disposable_changes or not key:
        raise SystemExit('Explicit opt-in and the private local test key are required.')
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(path, body=None, control=False):
        base = 'http://127.0.0.1:8082' if control else 'http://127.0.0.1:8080'
        headers = {'Content-Type': 'application/json', **({'Origin': 'http://127.0.0.1:8082'} if control else {})}
        req = urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with opener.open(req, timeout=45) as response: return json.load(response)
    request('/v1/console/unlock', {'token': key}, True)
    key = ''
    print('Private controller test session unlocked.', flush=True)
    def action(scenario, verb):
        preview = request('/v1/console/labs/preview', {'scenario': scenario, 'action': verb}, True)
        applied = request('/v1/console/labs/confirm', {'previewId': preview['previewId']}, True)
        assert applied['status'] == 'accepted', 'A lab action was interrupted; inspect/reset the sandbox.'
    def wait_for(predicate, seconds=60):
        deadline = time.monotonic()+seconds
        while True:
            snapshot = request('/v1/cluster?namespace=kravel-demo')
            if predicate(snapshot): return snapshot
            if time.monotonic() >= deadline: raise TimeoutError('Expected cluster observation did not appear.')
            time.sleep(2)
    try:
        types = {'imagepull': 'imagepullbackoff', 'oom': 'oomkilled', 'crashloop': 'crashloopbackoff', 'configmap': 'bad_configmap', 'network': 'service_selector'}
        for scenario, failure in types.items():
            action(scenario, 'break')
            wait_for(lambda snapshot: any(item['type'] == failure for item in snapshot['issues']))
            print(f'Observed real fault: {scenario} ({failure}).', flush=True)
            if scenario == 'imagepull':
                run = request('/v1/investigations', {'message': 'Why is image-demo failing?', 'namespace': 'kravel-demo', 'target': 'Deployment/image-demo'})
                deadline = time.monotonic()+150
                while True:
                    detail = request('/v1/investigations/'+run['id'])
                    if detail['status'] != 'running': break
                    if time.monotonic() >= deadline: raise TimeoutError('Investigation still active; do not reset underneath it.')
                    time.sleep(2)
                payload = detail['payload']
                print(json.dumps({'investigationStatus': detail['status'], 'disposition': payload.get('disposition'), 'traceId': payload.get('traceId'), 'guardrails': [{k: item.get(k) for k in ('phase', 'decision', 'reasonCode', 'latencyMs')} for item in payload.get('guardrails', {}).get('semantic', [])], 'fixes': [fix['id'] for fix in payload.get('suggestedFixes', [])]}), flush=True)
                assert detail['status'] == 'completed' and payload['disposition'] == 'success', 'Valid diagnosis did not pass; inspect the trace.'
            action(scenario, 'reset')
            wait_for(lambda snapshot: not snapshot['issues'] and snapshot['podCount'] == snapshot['healthyPods'] == 5)
            print(f'Observed healthy reset: {scenario}.', flush=True)
    finally:
        # Activity checks still apply: never reset underneath an active approval/model.
        try:
            action('all', 'reset')
            wait_for(lambda snapshot: not snapshot['issues'] and snapshot['podCount'] == snapshot['healthyPods'] == 5)
            print('All labs restored: five ready Pods, zero issues.', flush=True)
        finally:
            request('/v1/console/lock', {}, True)
            print('Private test session locked.', flush=True)


if __name__ == '__main__':
    main()
