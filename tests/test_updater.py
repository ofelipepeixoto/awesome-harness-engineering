"""Offline control-flow tests; requests replaced before module import."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

request_stub = types.ModuleType('requests')
request_stub.RequestException = OSError
request_stub.get = Mock(side_effect=AssertionError('network forbidden'))
with patch.dict(sys.modules, {'requests':request_stub}):
    spec = importlib.util.spec_from_file_location('updater', Path(__file__).parents[1]/'scripts/update_stars.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

def response(status, headers=None, data=None):
    return Mock(status_code=status,headers=headers or {},json=lambda:data)

class UpdateTests(unittest.TestCase):
    def test_retry_budget(self):
        for status in (429,500,503,403):
            headers={'Retry-After':'0'}
            with self.subTest(status=status), patch.object(m.requests,'get',return_value=response(status,headers)) as get, patch.object(m.time,'sleep') as sleep:
                self.assertIsNone(m.fetch_stars('Synthetic/Repo'))
                self.assertEqual(get.call_count,3)
                self.assertEqual(sleep.call_count,2)
                self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_authorization_redirect_and_long_delay_do_not_retry(self):
        for r in [response(403),response(302),response(429,{'Retry-After':'3600'}),
                  response(429,{'Retry-After':'NaN'}),response(429,{'Retry-After':'inf'})]:
            with patch.object(m.requests,'get',return_value=r) as get,patch.object(m.time,'sleep') as sleep:
                self.assertIsNone(m.fetch_stars('Synthetic/Repo'))
                self.assertEqual(get.call_count,1)
                sleep.assert_not_called()

    def test_success_after_retry_and_malformed_header(self):
        with patch.object(m.requests,'get',side_effect=[response(429,{'Retry-After':'bad'}),response(200,data={'stargazers_count':12})]) as get,patch.object(m.time,'sleep'):
            self.assertEqual(m.fetch_stars('Synthetic/Repo'),12)
            self.assertEqual(get.call_count,2)

    def test_normalization_and_duplicate(self):
        self.assertEqual(m.canonical_url('https://GitHub.com/Owner/Repo.git/'),'https://github.com/owner/repo')
        rows=m.deduplicate([{'name':'Owner/Repo','url':'https://github.com/Owner/Repo'},
                            {'name':'owner/repo','url':'https://github.com/owner/repo/'}])
        self.assertEqual(len(rows),1)
        for url in ['http://github.com/a/b','https://github.com.evil.invalid/a/b',
                    'https://user@github.com/a/b','https://github.com:443/a/b',
                    'https://github.com/a/b?x=1','https://github.com/a/b/issues','https://github.com/a/..']:
            with self.subTest(url=url),self.assertRaises(ValueError):m.canonical_url(url)

    def test_atomic_replace_failure_preserves_file(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'data.json';path.write_text('old')
            with patch.object(m.os,'replace',side_effect=OSError('fixture')):
                with self.assertRaises(OSError):m.atomic_write(path,'new')
            self.assertEqual(path.read_text(),'old')
            self.assertEqual(list(Path(d).iterdir()),[path])
            m.atomic_write(path,'new')
            self.assertEqual(path.read_text(),'new')

    def test_refresh_failure_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'data.json'
            path.write_text(json.dumps([{'name':'a/b','url':'https://github.com/a/b','stars':20}]))
            with patch.object(m,'DATA_FILE',path),patch.object(m,'fetch_stars',return_value=None),patch.object(m.time,'sleep'),patch.object(m,'atomic_write') as write:
                with self.assertRaises(RuntimeError):m.main()
                write.assert_not_called()

    def test_search_failure_and_incomplete_not_empty_success(self):
        for r in [response(403),response(200,data={'items':[],'incomplete_results':True})]:
            with patch.object(m,'bounded_get',return_value=r),self.assertRaises((RuntimeError,ValueError)):
                m.search_github('synthetic')

if __name__=='__main__':unittest.main()
