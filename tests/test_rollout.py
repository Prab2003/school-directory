import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples import sync_environments as rollout


class RolloutTests(unittest.TestCase):
    def source(self):
        return {'release': '2025-Q3', 'url': 'https://www.education.gov.za/fictional.csv',
                'min_rows': 1, 'required_provinces': ['Limpopo']}

    @patch.dict(os.environ, {'UAT_DATABASE_URL': '', 'PROD_DATABASE_URL': ''})
    @patch.object(rollout.sd, 'download')
    def test_missing_connections_stop_before_fetch(self, download):
        with self.assertRaises(rollout.sd.SourceError):
            rollout.run()
        download.assert_not_called()

    @patch.dict(os.environ, {'UAT_DATABASE_URL': 'uat-private', 'PROD_DATABASE_URL': 'prod-private'})
    def test_uat_review_prevents_production_and_does_not_log_connections(self):
        output = io.StringIO()
        with patch.object(rollout.sd, 'download', return_value=b'fixture'), \
             patch.object(rollout.sd, 'discover', return_value=self.source()), \
             patch.object(rollout.sd, 'parse', return_value=([{'province': 'Limpopo'}], {})), \
             patch.object(rollout.sd, 'sync', return_value={'status': 'review_required'}) as sync, \
             contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(rollout.sd.SourceError, 'subsequent environments'):
                rollout.run()
        self.assertEqual(sync.call_count, 1)
        self.assertNotIn('private', output.getvalue())

    @patch.dict(os.environ, {'UAT_DATABASE_URL': 'uat-private', 'PROD_DATABASE_URL': 'prod-private'})
    def test_same_validated_source_reaches_prod_only_after_uat(self):
        targets, sources = [], []
        def sync(source, rows, apply):
            targets.append(os.environ['DATABASE_URL']); sources.append(source.copy())
            return {'status': 'unchanged'}
        with patch.object(rollout.sd, 'download', return_value=b'fixture'), \
             patch.object(rollout.sd, 'discover', return_value=self.source()), \
             patch.object(rollout.sd, 'parse', return_value=([{'province': 'Limpopo'}], {})), \
             patch.object(rollout.sd, 'sync', side_effect=sync), \
             contextlib.redirect_stdout(io.StringIO()):
            result = rollout.run()
        self.assertEqual(targets, ['uat-private', 'prod-private'])
        self.assertEqual(sources[0], sources[1])
        self.assertEqual(result['production']['status'], 'unchanged')


if __name__ == '__main__':
    unittest.main()
