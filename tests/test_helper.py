"""Offline integration tests: real JWT signing and Git, mocked curl only."""
import base64
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'git-credential-github-app'


class HelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ('bash', 'git', 'openssl', 'jq'):
            if not shutil.which(tool):
                raise unittest.SkipTest('Missing dependency: ' + tool)
        cls.shared = tempfile.TemporaryDirectory()
        cls.key = Path(cls.shared.name) / 'key.pem'
        subprocess.run(['openssl', 'genrsa', '-out', str(cls.key), '2048'],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='github app test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.values = dict(app_id='123', installation_id='456', owner='example',
                           private_key=str(self.key))
        self.log = self.root / 'requests.jsonl'
        fake = self.root / 'curl'
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys, datetime
args = sys.argv[1:]
headers = sys.stdin.read()
with open(os.environ['REQUEST_LOG'], 'a') as f:
    f.write(json.dumps({'args': args, 'headers': headers}) + '\\n')
if os.environ.get('FAIL_HTTP'):
    sys.exit(22)
if args[-1].endswith('/access_tokens'):
    print(json.dumps({'token': os.environ.get('FAKE_TOKEN', 'test-token'), 'expires_at': os.environ.get('FAKE_EXPIRY', (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))}))
else:
    print(json.dumps({'account': {'login': os.environ.get('INSTALL_OWNER', 'example')}}))
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                        REQUEST_LOG=str(self.log), HOME=str(self.root),
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT='0')

    def flags(self):
        names = {'app_id': '--app-id', 'installation_id': '--installation-id',
                 'owner': '--owner', 'private_key': '--key-file',
                 'contents_permission': '--contents-permission'}
        return [item for key, value in self.values.items() for item in (names[key], value)]

    def run_helper(self, operation='get', request=None):
        return subprocess.run(['bash', str(HELPER), *self.flags(), operation],
                              env=self.env, text=True,
                              input=request if request is not None else
                              'protocol=https\nhost=github.com\npath=example/repo.git\n\n',
                              capture_output=True, timeout=15)

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_get_and_jwt_signature(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'username=x-access-token\npassword=test-token\n\n')
        requests = self.requests()
        self.assertEqual(len(requests), 2)
        token = requests[0]['headers'].strip().removeprefix('Authorization: Bearer ')
        header, payload, signature = token.split('.')
        decode = lambda s: base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
        self.assertEqual(json.loads(decode(header))['alg'], 'RS256')
        claims = json.loads(decode(payload))
        self.assertEqual(claims['iss'], '123')
        self.assertLessEqual(claims['exp'] - int(time.time()), 600)
        self.assertLess(claims['iat'], time.time())
        public = self.root / 'public.pem'
        subprocess.run(['openssl', 'rsa', '-in', str(self.key), '-pubout', '-out', str(public)],
                       check=True, capture_output=True)
        sig = self.root / 'signature'
        sig.write_bytes(decode(signature))
        verified = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public),
                                   '-signature', str(sig)], input=f'{header}.{payload}'.encode(),
                                  capture_output=True)
        self.assertEqual(verified.returncode, 0)
        args = requests[1]['args']
        self.assertEqual(json.loads(args[args.index('--data') + 1]),
                         {})
        self.assertNotIn(token, ' '.join(args))
        self.assertEqual(args[0], '--disable')

    def test_unrelated_host_and_owner_declined(self):
        for host, path in [('other.example', 'example/repo'), ('github.com', 'other/repo')]:
            with self.subTest(host=host, path=path):
                r = self.run_helper(request=f'protocol=https\nhost={host}\npath={path}\n\n')
                self.assertEqual((r.returncode, r.stdout), (0, ''))
        self.assertFalse(self.log.exists())

    def test_store_needs_no_settings(self):
        self.values = {}
        for op in ('store',):
            r = self.run_helper(op)
            self.assertEqual((r.returncode, r.stdout), (0, ''))

    def test_missing_path_fails_closed(self):
        r = self.run_helper(request='protocol=https\nhost=github.com\n\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, 'quit=true\n\n')
        self.assertFalse(self.log.exists())

    def test_invalid_id_fails_before_network(self):
        self.values['installation_id'] = '456/other'
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, 'quit=true\n\n')
        self.assertFalse(self.log.exists())

    def test_wrong_installation_owner_not_minted(self):
        self.env['INSTALL_OWNER'] = 'someone-else'
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, 'quit=true\n\n')
        self.assertEqual(len(self.requests()), 1)

    def test_http_failure_fails_closed(self):
        self.env['FAIL_HTTP'] = '1'
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, 'quit=true\n\n')
        self.assertNotIn('password=', r.stdout)

    def test_token_cannot_inject_credential_fields(self):
        self.env['FAKE_TOKEN'] = 'bad\nusername=injected'
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, 'quit=true\n\n')

    def test_read_permission_and_key_path_with_spaces(self):
        self.values['contents_permission'] = 'read'
        key = self.root / 'private key.pem'
        shutil.copy(self.key, key)
        self.values['private_key'] = str(key)
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.requests()[1]['args']
        self.assertEqual(json.loads(args[args.index('--data') + 1])['permissions'], {'contents': 'read'})

    def test_missing_required_flag(self):
        del self.values['app_id']
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, 'quit=true\n\n')
        self.assertFalse(self.log.exists())

    def test_bad_arguments(self):
        for flags in (['--owner'], ['--owner', '--key-file', '/tmp/key', 'get'],
                      ['--unknown', 'get'], ['get', 'extra']):
            with self.subTest(flags=flags):
                r = subprocess.run(['bash', str(HELPER), *flags], env=self.env,
                                   text=True, capture_output=True)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(r.stdout, 'quit=true\n\n')
        self.assertFalse(self.log.exists())

    def test_token_cache_reuse_and_scope(self):
        first = self.run_helper('token')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, 'test-token\n')
        self.env['FAIL_HTTP'] = '1'
        second = self.run_helper('token')
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.requests()), 2)
        args = self.requests()[1]['args']
        self.assertEqual(json.loads(args[args.index('--data') + 1]),
                         {})
        cache = next((self.root / '.cache/git-credential-github-app').glob('*.json'))
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual(cache.parent.stat().st_mode & 0o777, 0o700)

    def test_stale_or_corrupt_cache_refresh(self):
        self.assertEqual(self.run_helper('token').returncode, 0)
        cache = next((self.root / '.cache/git-credential-github-app').glob('*.json'))
        for content in ('{bad', '{"token":"old","expires_at":"2000-01-01T00:00:00Z"}'):
            cache.write_text(content)
            result = self.run_helper('token')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'test-token\n')
        self.assertEqual(len(self.requests()), 6)

    def test_near_expiry_refresh_and_failure_does_not_return_stale_token(self):
        self.assertEqual(self.run_helper('token').returncode, 0)
        cache = next((self.root / '.cache/git-credential-github-app').glob('*.json'))
        expiry = (datetime.datetime.now(datetime.timezone.utc) +
                  datetime.timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
        cache.write_text(json.dumps({'token': 'old', 'expires_at': expiry}))
        self.env['FAIL_HTTP'] = '1'
        result = self.run_helper('token')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        del self.env['FAIL_HTTP']
        result = self.run_helper('token')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'test-token\n')

    def test_rotated_key_does_not_reuse_cache(self):
        self.assertEqual(self.run_helper('token').returncode, 0)
        rotated = self.root / 'rotated.pem'
        subprocess.run(['openssl', 'genrsa', '-out', str(rotated), '2048'],
                       check=True, capture_output=True)
        self.values['private_key'] = str(rotated)
        self.assertEqual(self.run_helper('token').returncode, 0)
        self.assertEqual(len(self.requests()), 4)

    def test_git_and_cli_share_default_token_and_erase(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_helper('token').stdout, 'test-token\n')
        result = self.run_helper(request='protocol=https\nhost=github.com\npath=example/another\n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.requests()), 2)
        result = self.run_helper('erase')
        self.assertEqual((result.returncode, result.stdout), (0, ''), result.stderr)
        self.assertEqual(len(self.requests()), 2)
        self.assertEqual(self.run_helper('token').returncode, 0)
        self.assertEqual(len(self.requests()), 4)

    def test_explicit_restrictions_have_separate_cache(self):
        self.assertEqual(self.run_helper('token').returncode, 0)
        self.values['contents_permission'] = 'read'
        self.assertEqual(self.run_helper().returncode, 0)
        args = self.requests()[-1]['args']
        self.assertEqual(json.loads(args[args.index('--data') + 1]),
                         {'repositories': ['repo'], 'permissions': {'contents': 'read'}})
        self.assertEqual(len(self.requests()), 4)
        self.assertEqual(self.run_helper('erase').returncode, 0)
        del self.values['contents_permission']
        self.assertEqual(self.run_helper('token').returncode, 0)
        self.assertEqual(len(self.requests()), 4)

    def test_cache_separates_installations(self):
        self.assertEqual(self.run_helper('token').returncode, 0)
        self.values['installation_id'] = '789'
        self.assertEqual(self.run_helper('token').returncode, 0)
        self.assertEqual(len(self.requests()), 4)

    def test_bad_expiry_and_http_failure_no_token_output(self):
        self.env['FAKE_EXPIRY'] = 'invalid'
        result = self.run_helper('token')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.env['FAIL_HTTP'] = '1'
        result = self.run_helper('token')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_wrapper_forwards_args_and_reuses_token(self):
        real = self.root / 'real gh'
        real.write_text('#!/usr/bin/env python3\nimport os,sys,json\n'
                        'print(json.dumps([os.environ["GH_TOKEN"],sys.argv[1:]]))\n'
                        'sys.exit(7)\n')
        real.chmod(0o755)
        self.env.update(GH_APP_ID='123', GH_APP_INSTALLATION_ID='456',
                        GH_APP_OWNER='example', GH_APP_KEY_FILE=str(self.key),
                        GH_APP_GH_BIN=str(real), GH_TOKEN='old-personal-token')
        wrapper = HELPER.parent / 'bin/gh'
        for _ in range(2):
            result = subprocess.run([str(wrapper), 'pr', 'list', '--search', 'two words'],
                                    env=self.env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(json.loads(result.stdout),
                             ['test-token', ['pr', 'list', '--search', 'two words']])
        self.assertEqual(len(self.requests()), 2)
        self.env['GH_APP_GH_BIN'] = str(wrapper)
        result = subprocess.run([str(wrapper)], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_git_credential_fill_integration(self):
        repo = self.root / 'checkout'
        subprocess.run(['git', 'init', '-q', str(repo)], env=self.env, check=True)
        helper = '!' + shlex.join(['bash', str(HELPER), *self.flags()])
        args = ['git', '-C', str(repo), '-c', 'credential.helper=', '-c',
                'credential.helper=' + helper, '-c', 'credential.useHttpPath=true',
                'credential', 'fill']
        r = subprocess.run(args, input='url=https://github.com/example/repo.git\n\n',
                           env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('password=test-token', r.stdout)


if __name__ == '__main__':
    unittest.main()
