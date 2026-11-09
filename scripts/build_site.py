"""Build only approved, audited event data. No candidate data is copied."""
import json
import os
import shutil
from pathlib import Path
from event_model import validate, digest
ROOT = Path(__file__).resolve().parents[1]

def build(root=ROOT):
    data = json.loads((root/'events/approved.json').read_text())
    if not isinstance(data, list):
        raise ValueError('approved.json must be an array')
    ids = set()
    owner = os.getenv('GITHUB_REPOSITORY', 'omochi-crypto/inline-skate-events').split('/')[0]
    for event in data:
        validate(event, require_future=False)
        if (event.get('status') != 'approved' or event.get('approved_by') != owner
                or not isinstance(event.get('approval_comment_id'), int)
                or event.get('approval_digest') != digest(event) or event['id'] in ids):
            raise ValueError('Missing/invalid human approval audit or duplicate event')
        ids.add(event['id'])
    # Separate practice listing: same human-approval audit as competitions.
    # Unreviewed candidates must never be copied to the public site.
    practices = json.loads((root/'events/practices-approved.json').read_text())
    if not isinstance(practices, list):
        raise ValueError('practices-approved.json must be an array')
    for practice in practices:
        validate(practice, require_future=False)
        if (practice.get('status') != 'approved'
                or practice.get('approved_by') != owner
                or not isinstance(practice.get('approval_comment_id'), int)
                or practice.get('approval_digest') != digest(practice)
                or practice['id'] in ids):
            raise ValueError('Missing/invalid practice approval audit or duplicate ID')
        ids.add(practice['id'])
    public = root/'public'
    public.mkdir(exist_ok=True)
    shutil.copy(root/'site/index.html', public/'index.html')
    shutil.copy(root/'site/practices.html', public/'practices.html')
    # Explicit whitelist: candidates, evidence and operational reports are never deployed.
    (public/'approved.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    (public/'practices-approved.json').write_text(json.dumps(practices, ensure_ascii=False, indent=2)+'\n')
    (public/'.nojekyll').touch()
    print(f'Built approved-only site: {len(data)} events')

if __name__ == '__main__':
    build()
