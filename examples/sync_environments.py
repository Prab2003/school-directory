"""Private deployment runner: validate/apply one source on UAT before production.

Keep UAT_DATABASE_URL and PROD_DATABASE_URL in the deployment project's protected
secrets. The public repository never runs this against Skolo Saka from pull requests.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import school_directory as sd


def run():
    uat = os.environ.get('UAT_DATABASE_URL')
    prod = os.environ.get('PROD_DATABASE_URL')
    if not uat or not prod or uat == prod:
        raise sd.SourceError('Distinct UAT_DATABASE_URL and PROD_DATABASE_URL are required')
    source = sd.discover(sd.download(sd.SOURCE_PAGE, 5 * 1024 * 1024).decode('utf-8-sig'))
    data = sd.download(source['url'])
    source['sha256'] = hashlib.sha256(data).hexdigest()
    rows, skipped = sd.parse(data)
    if len(rows) < source['min_rows'] or set(r['province'] for r in rows) != set(source['required_provinces']):
        raise sd.SourceError('National source coverage validation failed')
    result = {'release': source['release'], 'sha256': source['sha256'], 'records': len(rows), 'skipped_types': skipped}
    previous = os.environ.get('DATABASE_URL')
    try:
        for label, connection in [('uat', uat), ('production', prod)]:
            os.environ['DATABASE_URL'] = connection
            report = sd.sync(source, rows, apply=True)
            result[label] = report
            print(json.dumps({'environment': label, 'report': report}, sort_keys=True), flush=True)
            if report['status'] not in ('applied', 'unchanged'):
                raise sd.SourceError(f'{label} import requires review; subsequent environments were not changed')
    finally:
        if previous is None:
            os.environ.pop('DATABASE_URL', None)
        else:
            os.environ['DATABASE_URL'] = previous
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(run(), indent=2, sort_keys=True))
    except (sd.SourceError, OSError, ValueError) as exc:
        print('Directory rollout stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)
