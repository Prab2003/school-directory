#!/usr/bin/env python3
"""Validated DBE directory imports. Python 3.11+, standard library only.

The software is MIT licensed. Source datasets retain their own terms.
Database access uses psql and DATABASE_URL; credentials are never arguments.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, unquote, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
import xml.etree.ElementTree as ET
import zipfile

SOURCE_PAGE = 'https://www.education.gov.za/Programmes/EMIS/EMISDownloads.aspx'
ALLOWED_HOSTS = {'www.education.gov.za', 'education.gov.za'}
MAX_DOWNLOAD = 30 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
PROVINCES = dict(zip(
    ['EC', 'FS', 'GP', 'KZN', 'LP', 'MP', 'NC', 'NW', 'WC'],
    ['Eastern Cape', 'Free State', 'Gauteng', 'KwaZulu-Natal', 'Limpopo',
     'Mpumalanga', 'Northern Cape', 'North West', 'Western Cape']))
PHASES = {'PRIMARY SCHOOL': 'primary', 'SECONDARY SCHOOL': 'high',
          'COMBINED SCHOOL': 'combined', 'INTERMEDIATE SCHOOL': 'primary',
          'PRE-PRIMARY SCHOOL': 'other', 'SPECIAL SCHOOL': 'other',
          'SPECIAL NEEDS EDUCATION': 'other', 'SPECIAL NEEDS EDUCATION SCHOOL': 'other',
          'SCHOOL OF SKILLS': 'other', 'HOSPITAL SCHOOL': 'other', 'NOT APPLICABLE': 'other',
          'PRIMARY': 'primary', 'SECONDARY': 'high', 'COMBINED': 'combined',
          'OTHER': 'other'}


def compact(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def header(value):
    return re.sub(r'[^a-z0-9]', '', compact(value).lower())


ALIASES = {
    'emis_number': ('natemis', 'emisnumber', 'nationalemisnumber'),
    'name': ('officialinstitutionname', 'schoolname', 'name'),
    'province': ('province',), 'level': ('phaseped', 'phase', 'level'),
    'town': ('towncity', 'town'), 'municipality': ('lmunname', 'municipality'),
    'status': ('status',), 'type': ('typedoe', 'institutiontype'),
    'old_emis': ('oldnatemis',), 'new_emis': ('newnatemis',),
}


class SourceError(ValueError):
    pass


def official_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in ALLOWED_HOSTS or p.username or p.password or p.port:
        raise SourceError('Source URL must be HTTPS on the official education.gov.za host')
    return url


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, official_url(newurl))


def download(url, limit=MAX_DOWNLOAD):
    req = Request(official_url(url), headers={'User-Agent': 'SkoloSaka-school-directory/0.1 (+https://github.com/skolo-saka/school-directory)'})
    with build_opener(OfficialRedirects()).open(req, timeout=30) as response:
        official_url(response.url)
        data = response.read(limit + 1)
        if len(data) > limit:
            raise SourceError('Source download exceeds the size limit')
        return data


class MasterlistPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.heading = None
        self.heading_text = None
        self.link = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'h2':
            self.heading_text = []
        if tag == 'a':
            self.link = [dict(attrs).get('href', ''), [], self.heading]

    def handle_data(self, data):
        if self.heading_text is not None:
            self.heading_text.append(data)
        if self.link is not None:
            self.link[1].append(data)

    def handle_endtag(self, tag):
        if tag == 'h2' and self.heading_text is not None:
            text = compact(''.join(self.heading_text))
            match = re.search(r'Quarter\s+([1-4])\s+of\s+(20\d\d)', text, re.I)
            self.heading = (int(match[2]), int(match[1])) if match else None
            self.heading_text = None
        if tag == 'a' and self.link is not None:
            href, parts, period = self.link
            if period and compact(''.join(parts)).lower() in ('national', 'national ordinary schools', 'national: ordinary schools', 'national - ordinary schools'):
                self.links.append((period, official_url(urljoin(SOURCE_PAGE, href))))
            self.link = None


def discover(page):
    parser = MasterlistPage()
    parser.feed(page)
    if not parser.links:
        raise SourceError('No national quarterly masterlist found; source page may have changed')
    period, url = max(parser.links, key=lambda item: item[0])
    return {'key': 'dbe-national', 'release': f'{period[0]}-Q{period[1]}',
            'url': url, 'landing_page': SOURCE_PAGE,
            'licence': 'Source-specific terms; not covered by the software MIT licence',
            'min_rows': 20000, 'required_provinces': list(PROVINCES.values())}


def xml_root(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise SourceError('XML declarations and entities are not accepted')
    return ET.fromstring(data)


def xlsx_rows(data):
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > 500 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise SourceError('Expanded workbook exceeds the size limit')
        if any(i.flag_bits & 1 for i in infos):
            raise SourceError('Encrypted workbooks are not supported')
        if any('vbaProject' in i.filename for i in infos):
            raise SourceError('Macro-enabled workbooks are not supported')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = xml_root(archive.read('xl/sharedStrings.xml'))
            strings = [''.join(n.text or '' for n in si.iter(ns + 't')) for si in root]
        # This adapter deliberately accepts the observed one-sheet national workbook.
        # A structural change fails instead of importing an arbitrary worksheet.
        workbook = xml_root(archive.read('xl/workbook.xml'))
        sheets = workbook.find(ns + 'sheets')
        if sheets is None or len(sheets) != 1:
            raise SourceError('Expected one national worksheet; review this workbook manually')
        relationships = xml_root(archive.read('xl/_rels/workbook.xml.rels'))
        rid = sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        target = next((r.get('Target') for r in relationships if r.get('Id') == rid), None)
        if not target:
            raise SourceError('Worksheet relationship is missing')
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        if '..' in target.split('/') or target not in archive.namelist():
            raise SourceError('Unexpected worksheet path')
        root = xml_root(archive.read(target))
        sheet_data = root.find(ns + 'sheetData')
        if sheet_data is None:
            raise SourceError('Worksheet has no rows')
        for row in sheet_data:
            values = {}
            for cell in row:
                if cell.find(ns + 'f') is not None:
                    raise SourceError('Formula cells are not accepted')
                ref = cell.get('r', '')
                letters = re.match(r'[A-Z]+', ref)
                if not letters:
                    raise SourceError('Invalid cell address')
                index = 0
                for letter in letters[0]:
                    index = index * 26 + ord(letter) - 64
                if index > 200:
                    raise SourceError('Too many source columns')
                value = cell.find(ns + 'v')
                text = value.text or '' if value is not None else ''
                if cell.get('t') == 's':
                    try:
                        text = strings[int(text)]
                    except (ValueError, IndexError):
                        raise SourceError('Invalid shared string') from None
                elif cell.get('t') == 'inlineStr':
                    text = ''.join(n.text or '' for n in cell.iter(ns + 't'))
                values[index - 1] = text
            if values:
                yield [values.get(i, '') for i in range(max(values) + 1)]


def read_rows(data):
    if data.startswith(b'PK'):
        yield from xlsx_rows(data)
    else:
        text = data.decode('utf-8-sig')
        if text.lstrip().startswith('<'):
            raise SourceError('Expected CSV/XLSX; received HTML or XML')
        yield from csv.reader(io.StringIO(text))


def parse(data):
    rows = iter(read_rows(data))
    try:
        columns = [header(v) for v in next(rows)]
    except StopIteration:
        raise SourceError('Empty source file') from None
    mapping = {}
    for key, aliases in ALIASES.items():
        matches = [i for i, value in enumerate(columns) if value in aliases]
        if len(matches) > 1:
            raise SourceError(f'Ambiguous column: {key}')
        if matches:
            mapping[key] = matches[0]
    if not {'emis_number', 'name', 'province', 'level', 'status'} <= mapping.keys():
        raise SourceError('Required masterlist columns are missing')
    result, seen, skipped, unknown = [], set(), collections.Counter(), collections.Counter()
    for line, row in enumerate(rows, 2):
        if not any(compact(c) for c in row):
            continue
        def get(key):
            index = mapping.get(key)
            return compact(row[index]) if index is not None and index < len(row) else ''
        emis = re.sub(r'\.0+$', '', get('emis_number'))
        if not re.fullmatch(r'\d{9}', emis):
            raise SourceError(f'Invalid EMIS number at row {line}')
        if emis in seen:
            raise SourceError(f'Duplicate EMIS number at row {line}')
        seen.add(emis)
        kind = get('type').upper()
        if kind and kind not in {'ORDINARY SCHOOL', 'SPECIAL NEEDS EDUCATION', 'SPECIAL NEEDS EDUCATION CENTRE', 'SPECIAL SCHOOL', 'SPECIAL NEEDS SCHOOL'}:
            skipped[kind] += 1
            continue
        phase = get('level').upper()
        level = PHASES.get(phase)
        if level is None:
            unknown[phase] += 1
            continue
        province_code = 'GP' if get('province').upper() == 'GT' else get('province').upper()
        province = PROVINCES.get(province_code, get('province'))
        if province not in PROVINCES.values():
            raise SourceError(f'Unrecognised province at row {line}')
        status = get('status').upper()
        if status not in {'OPEN', 'CLOSED', 'OPERATIONAL', 'NON-OPERATIONAL', 'PENDING OPEN'}:
            raise SourceError(f'Unrecognised status at row {line}')
        name = get('name')
        if not name or len(name) > 300:
            raise SourceError(f'Invalid school name at row {line}')
        def location(key):
            value = get(key)
            if value.upper() in {'', '99', 'UNKNOWN', 'N/A', 'NOT APPLICABLE'}:
                return None
            if len(value) > 200:
                raise SourceError(f'Invalid location at row {line}')
            return value
        def previous(key):
            value = re.sub(r'\.0+$', '', get(key))
            return value if re.fullmatch(r'\d{9}', value) else None
        result.append({'emis_number': emis, 'name': name, 'province': province,
                       'level': level, 'municipality': location('municipality'),
                       'town': location('town'), 'status': status,
                       'old_emis': previous('old_emis'), 'new_emis': previous('new_emis')})
        if len(result) > 100000:
            raise SourceError('Too many source records')
    if unknown:
        raise SourceError('Unmapped school phases: ' + json.dumps(dict(unknown), sort_keys=True))
    if not result:
        raise SourceError('Source contains no supported schools')
    return sorted(result, key=lambda item: item['emis_number']), dict(skipped)


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def database(sql):
    url = os.environ.get('DATABASE_URL', '')
    p = urlsplit(url)
    if p.scheme not in ('postgres', 'postgresql') or not p.hostname or not p.username:
        raise SourceError('Set DATABASE_URL to the intended PostgreSQL connection')
    env = os.environ.copy()
    for key in ('PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'):
        env.pop(key, None)
    env.update(PGHOST=p.hostname, PGPORT=str(p.port or 5432), PGUSER=unquote(p.username),
               PGPASSWORD=unquote(p.password or ''), PGDATABASE=unquote(p.path.lstrip('/') or 'postgres'),
               PGSSLMODE=parse_qs(p.query).get('sslmode', ['require'])[0], PGCONNECT_TIMEOUT='15')
    if env['PGSSLMODE'] not in ('require', 'verify-ca', 'verify-full') and p.hostname not in ('localhost', '127.0.0.1'):
        raise SourceError('Remote database connections require TLS')
    completed = subprocess.run(['psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1'],
                               input=sql, capture_output=True, text=True, env=env, timeout=180)
    if completed.returncode:
        # Do not dump SQL, connection URLs, passwords or source rows into CI logs.
        message = next((line.split('ERROR:', 1)[1].strip() for line in completed.stderr.splitlines() if 'ERROR:' in line), 'connection or SQL execution failed')
        raise SourceError('Database import failed: ' + message[:240])
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def sync(source, rows, apply=False):
    run = database('select school_directory.begin_import(' + quote(json.dumps(source)) + '::jsonb);')
    if run['status'] == 'unchanged':
        return run
    run_id = run['run_id']
    for offset in range(0, len(rows), 1000):
        database('select school_directory.stage_import(' + quote(run_id) + '::uuid,' + quote(json.dumps(rows[offset:offset + 1000])) + '::jsonb);')
    preview = database('select school_directory.finish_import(' + quote(run_id) + '::uuid,false,null);')
    if not apply or preview['status'] == 'review_required':
        return preview
    return database('select school_directory.finish_import(' + quote(run_id) + '::uuid,true,' + quote(preview['plan_hash']) + ');')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, help='Private local copy of an official national masterlist')
    parser.add_argument('--release', help='Required with --file, e.g. 2025-Q3')
    parser.add_argument('--source-url', help='Official source URL for a local file')
    parser.add_argument('--database', action='store_true', help='Stage and preview against DATABASE_URL')
    parser.add_argument('--apply', action='store_true', help='Apply a validated, within-threshold database preview')
    parser.add_argument('--bootstrap', action='store_true', help='Explicitly allow a reviewed first import into an empty database only')
    parser.add_argument('--output-sql', type=Path, help='Write private staging SQL for an operator; never publish this file')
    args = parser.parse_args(argv)
    if args.file:
        if not args.release or not re.fullmatch(r'20\d\d-Q[1-4]', args.release) or not args.source_url:
            parser.error('--file requires --release YYYY-QN and --source-url')
        source = {'key': 'dbe-national', 'release': args.release, 'url': official_url(args.source_url),
                  'landing_page': SOURCE_PAGE, 'min_rows': 20000,
                  'required_provinces': list(PROVINCES.values()),
                  'licence': 'Source-specific terms; not covered by the software MIT licence'}
        if args.file.stat().st_size > MAX_DOWNLOAD:
            raise SourceError('Local source exceeds the size limit')
        data = args.file.read_bytes()
    else:
        source = discover(download(SOURCE_PAGE, 5 * 1024 * 1024).decode('utf-8-sig'))
        data = download(source['url'])
    source['sha256'] = hashlib.sha256(data).hexdigest()
    if args.bootstrap:
        if not (args.database or args.apply):
            parser.error('--bootstrap requires --database or --apply')
        source['allow_empty'] = True
    rows, skipped = parse(data)
    if len(rows) < source['min_rows'] or set(r['province'] for r in rows) != set(PROVINCES.values()):
        raise SourceError('National coverage check failed; existing schools were not changed')
    report = {'source': source, 'records': len(rows), 'skipped_types': skipped,
              'provinces': dict(sorted(collections.Counter(r['province'] for r in rows).items())),
              'statuses': dict(collections.Counter(r['status'] for r in rows)),
              'levels': dict(collections.Counter(r['level'] for r in rows))}
    if args.output_sql:
        # Private local artifact for tools/operators. No school records enter the public repo.
        run_id = __import__('uuid').uuid4()
        statements = ['select school_directory.begin_import(' + quote(json.dumps(source)) + '::jsonb,' + quote(run_id) + '::uuid);']
        for offset in range(0, len(rows), 1000):
            statements.append('select school_directory.stage_import(' + quote(run_id) + '::uuid,' + quote(json.dumps(rows[offset:offset + 1000])) + '::jsonb);')
        statements.append('select school_directory.finish_import(' + quote(run_id) + '::uuid,false,null);')
        args.output_sql.write_text('\n'.join(statements), encoding='utf-8')
        report['private_staging_run_id'] = str(run_id)
    if args.database or args.apply:
        report['database'] = sync(source, rows, apply=args.apply)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report.get('database', {}).get('status') == 'review_required':
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (SourceError, OSError, ET.ParseError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print('School directory check failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
