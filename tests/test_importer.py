import csv
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import school_directory as sd


HEADERS = ['NatEmis', 'Official_Institution_Name', 'Province', 'Phase_PED',
           'Status', 'Type_DoE', 'Town_City', 'LMunName', 'Email', 'Addressee']
ROW = ['900000001', 'Fictional Primary School', 'GT', 'PRIMARY SCHOOL', 'OPEN',
       'ORDINARY SCHOOL', 'Example Town', 'Example Municipality', 'private@example.invalid', 'PRIVATE PERSON']


def csv_data(*rows):
    out = io.StringIO()
    csv.writer(out).writerows([HEADERS, *rows])
    return out.getvalue().encode()


def workbook(formula=False, extra_sheet=False, doctype=False):
    out = io.BytesIO()
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rid = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    values = [HEADERS, ROW]
    cells = []
    from xml.sax.saxutils import escape
    for row_n, row in enumerate(values, 1):
        cells.append('<row>')
        for col_n, value in enumerate(row):
            f = '<f>1+1</f>' if formula and row_n == 2 and col_n == 0 else ''
            cells.append(f'<c r="{chr(65+col_n)}{row_n}" t="inlineStr">{f}<is><t>{escape(value)}</t></is></c>')
        cells.append('</row>')
    with zipfile.ZipFile(out, 'w') as archive:
        second = '<sheet name="Other" r:id="rId2"/>' if extra_sheet else ''
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="{rid}"><sheets><sheet name="National" r:id="rId1"/>{second}</sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', ('<!DOCTYPE x []>' if doctype else '') + f'<worksheet xmlns="{ns}"><sheetData>{"".join(cells)}</sheetData></worksheet>')
    return out.getvalue()


class ImporterTests(unittest.TestCase):
    def test_public_field_allowlist_and_province_alias(self):
        rows, skipped = sd.parse(csv_data(ROW))
        self.assertEqual(rows[0]['province'], 'Gauteng')
        self.assertEqual(rows[0]['level'], 'primary')
        self.assertEqual(skipped, {})
        self.assertNotIn('Email', rows[0])
        self.assertNotIn('PRIVATE PERSON', json.dumps(rows))
        self.assertNotIn('private@example.invalid', json.dumps(rows))

    def test_duplicate_emis_rejected_before_writes(self):
        with self.assertRaisesRegex(sd.SourceError, 'Duplicate EMIS'):
            sd.parse(csv_data(ROW, ROW))

    def test_invalid_emis(self):
        row = ROW.copy(); row[0] = '123'
        with self.assertRaisesRegex(sd.SourceError, 'Invalid EMIS'):
            sd.parse(csv_data(row))

    def test_unknown_phase_fails_closed(self):
        row = ROW.copy(); row[3] = 'NEW UPSTREAM PHASE'
        with self.assertRaisesRegex(sd.SourceError, 'Unmapped school phases'):
            sd.parse(csv_data(row))

    def test_missing_headers(self):
        with self.assertRaisesRegex(sd.SourceError, 'Required masterlist'):
            sd.parse(b'name,phone\nExample,123\n')

    def test_duplicate_headers(self):
        with self.assertRaisesRegex(sd.SourceError, 'Ambiguous column'):
            sd.parse(csv_data(ROW).replace(b'Addressee', b'NatEmis'))

    def test_unknown_province(self):
        row = ROW.copy(); row[2] = 'XX'
        with self.assertRaisesRegex(sd.SourceError, 'province'):
            sd.parse(csv_data(row))

    def test_unknown_status(self):
        row = ROW.copy(); row[4] = 'UNSPECIFIED'
        with self.assertRaisesRegex(sd.SourceError, 'status'):
            sd.parse(csv_data(row))

    def test_pending_open_preserved_for_database_decision(self):
        row = ROW.copy(); row[4] = 'PENDING OPEN'
        self.assertEqual(sd.parse(csv_data(row))[0][0]['status'], 'PENDING OPEN')

    def test_missing_locations_do_not_become_99(self):
        row = ROW.copy(); row[6] = '99'; row[7] = 'Unknown'
        parsed = sd.parse(csv_data(row))[0][0]
        self.assertIsNone(parsed['town']); self.assertIsNone(parsed['municipality'])

    def test_special_schools_supported(self):
        row = ROW.copy(); row[3] = 'SPECIAL NEEDS EDUCATION SCHOOL'; row[5] = 'SPECIAL NEEDS EDUCATION CENTRE'
        self.assertEqual(sd.parse(csv_data(row))[0][0]['level'], 'other')

    def test_ecd_excluded_and_counted(self):
        row = ROW.copy(); row[0] = '900000002'; row[5] = 'STAND ALONE ECD'; row[3] = 'ECD'
        rows, skipped = sd.parse(csv_data(ROW, row))
        self.assertEqual(len(rows), 1); self.assertEqual(skipped, {'STAND ALONE ECD': 1})

    def test_xlsx_inline_strings(self):
        self.assertEqual(sd.parse(workbook())[0][0]['emis_number'], '900000001')

    def test_xlsx_formula_rejected(self):
        with self.assertRaisesRegex(sd.SourceError, 'Formula'):
            sd.parse(workbook(formula=True))

    def test_xlsx_multiple_sheets_rejected(self):
        with self.assertRaisesRegex(sd.SourceError, 'one national worksheet'):
            sd.parse(workbook(extra_sheet=True))

    def test_xml_entities_rejected(self):
        with self.assertRaisesRegex(sd.SourceError, 'XML declarations'):
            sd.parse(workbook(doctype=True))

    def test_html_error_response_not_csv(self):
        with self.assertRaisesRegex(sd.SourceError, 'HTML'):
            sd.parse(b'<html>Service unavailable</html>')

    def test_discovery_selects_newest_national_not_province(self):
        page = '<h2>Quarter 2 of 2025</h2><a href="/old.xlsx">National</a><h2>Quarter 3 of 2025</h2><a href="/ec.xlsx">Eastern Cape</a><a href="/new.xlsx">National</a>'
        found = sd.discover(page)
        self.assertEqual(found['release'], '2025-Q3')
        self.assertTrue(found['url'].endswith('/new.xlsx'))

    def test_discovery_missing_national_fails(self):
        with self.assertRaisesRegex(sd.SourceError, 'No national'):
            sd.discover('<h2>Quarter 1 of 2026</h2><a href="/ec">Eastern Cape</a>')

    def test_source_host_pinned_and_https(self):
        for url in ['http://www.education.gov.za/a', 'https://evil.invalid/a',
                    'https://www.education.gov.za.evil.invalid/a', 'https://user@www.education.gov.za/a',
                    'https://www.education.gov.za:8443/a']:
            with self.subTest(url=url), self.assertRaises(sd.SourceError):
                sd.official_url(url)

    def test_sql_values_are_quoted(self):
        self.assertEqual(sd.quote("School's name"), "'School''s name'")

    @patch.dict('os.environ', {'DATABASE_URL': 'postgresql://user:secret@db.example.invalid/postgres?sslmode=disable'})
    def test_remote_database_requires_tls(self):
        with self.assertRaisesRegex(sd.SourceError, 'require TLS'):
            sd.database('select 1')

    @patch('school_directory.database')
    def test_unchanged_source_has_no_stage_or_apply(self, db):
        db.return_value = {'status': 'unchanged'}
        self.assertEqual(sd.sync({'key':'test'}, [dict()], apply=True)['status'], 'unchanged')
        self.assertEqual(db.call_count, 1)

    @patch('school_directory.database')
    def test_review_report_cannot_trigger_apply(self, db):
        db.side_effect = [{'status':'staging','run_id':'example'}, {'staged':1}, {'status':'review_required'}]
        self.assertEqual(sd.sync({'key':'test'}, [dict()], apply=True)['status'], 'review_required')
        self.assertEqual(db.call_count, 3)

    @patch('school_directory.database')
    def test_apply_uses_current_preview_fingerprint(self, db):
        db.side_effect = [{'status':'staging','run_id':'example'}, {'staged':1},
                          {'status':'previewed','plan_hash':'fingerprint'}, {'status':'applied'}]
        self.assertEqual(sd.sync({'key':'test'}, [dict()], apply=True)['status'], 'applied')
        self.assertIn("true,'fingerprint'", db.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
