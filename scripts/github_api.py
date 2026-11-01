"""Small GitHub REST client for Actions, with full pagination."""
import json
import os
import urllib.request
import urllib.error

REPO = os.environ.get('GITHUB_REPOSITORY', '')

def api(path, method='GET', data=None):
    if not REPO or REPO != 'omochi-crypto/inline-skate-events':
        raise ValueError('Repository safety guard: wrong repository')
    req = urllib.request.Request('https://api.github.com/repos/'+REPO+path,
        data=json.dumps(data).encode() if data is not None else None, method=method,
        headers={'Authorization': 'Bearer '+os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            print('GitHub access stopped:', exc.code,
                  'Retry-After:', exc.headers.get('Retry-After') if exc.headers else None,
                  'Reset:', exc.headers.get('X-RateLimit-Reset') if exc.headers else None)
        raise  # Never retry or continue writes after a restriction.

def all_pages(path):
    page, output = 1, []
    while True:
        batch = api(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
        output.extend(batch)
        if len(batch) < 100:
            return output
        page += 1

