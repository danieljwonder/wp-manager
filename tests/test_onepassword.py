"""No real CLI, vault items or WordPress calls: subprocess + HTTP fixtures only."""
import base64
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from helpers import BASE, SECRET, USER, Env, ReplayTransport, connection_string, submodule

op = submodule('core.onepassword')
service = submodule('core.service')
registry = submodule('core.registry')
credentials = submodule('core.credentials')
V = 'a' * 26
I = 'b' * 26
REF = f'op://{V}/{I}/password'


class OnePassword(unittest.TestCase):
    def setUp(self):
        self.env = Env(settings={'op_binary': '/trusted/op-wrapper'}).start()
        self.events = []
        self.failure = None
        self.mock = patch.object(op.subprocess, 'run', side_effect=self.run_cli)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.env.stop()

    def run_cli(self, argv, **kw):
        self.assertEqual(argv[0], '/trusted/op-wrapper')
        self.assertNotIn(SECRET, json.dumps(argv))
        self.assertNotIn(connection_string(), json.dumps(argv))
        self.assertFalse(kw.get('shell', False))
        self.assertTrue(kw['capture_output'])
        self.assertEqual(kw['timeout'], 15)
        cmd = argv[1]
        self.events.append(cmd)
        if self.failure == cmd:
            return SimpleNamespace(returncode=1, stdout=SECRET, stderr=SECRET + connection_string())
        if cmd == 'vault':
            self.assertEqual(argv[1:], ['vault', 'list', '--format=json'])
            self.assertEqual(kw['input'], '')
            out = json.dumps([{'id': V, 'name': 'Test vault', 'ignored': SECRET}])
        elif cmd == 'item':
            self.assertEqual(argv[1:], ['item', 'create', '-', '--format=json', '--vault', V])
            payload = json.loads(kw['input'])
            self.assertEqual(payload['vault'], {'id': V})
            self.assertEqual(payload['fields'][1]['value'], SECRET)
            self.assertEqual(payload['urls'][0]['href'], BASE)
            self.assertEqual(payload['fields'][0]['value'], USER)
            out = json.dumps({'id': I, 'vault': {'id': V}, 'fields': [{'value': SECRET}]})
        else:
            self.assertEqual(argv[1:], ['read', '--no-newline', REF])
            self.assertEqual(kw['input'], '')
            out = 'different' if self.failure == 'mismatch' else SECRET
        return SimpleNamespace(returncode=0, stdout=out, stderr='')

    def args(self, **kw):
        return {'connection_string': connection_string(), 'id': 'wp-example',
                'storage': 'onepassword', 'vault_id': V, **kw}

    def assert_clean(self, obj):
        for text in [str(obj), *self.env.all_files_text().values()]:
            self.assertNotIn(SECRET, text)
            self.assertNotIn(SECRET.replace(' ', ''), text)
            self.assertNotIn(connection_string(), text)
        self.assertFalse((self.env.home / ".env").exists())

    def test_create_read_auth_commit_order_and_privacy(self):
        transport = self.env.transport
        def auth(req):
            self.events.append('auth')
            self.assertEqual(self.events, ['vault', 'item', 'read', 'auth'])
            self.assertEqual(registry.list_sites(), [])
            self.assertEqual(req.url, BASE + '/wp-json/website-manager-hermes/v1/status')
            expected = base64.b64encode(f"{USER}:{SECRET.replace(' ', '')}".encode()).decode()
            self.assertEqual(req.headers['Authorization'], 'Basic ' + expected)
            return transport(req)
        submodule('core.client').set_transport(auth)
        result = service.add_site(self.args(), actor='desktop')
        self.assertEqual(registry.list_sites()[0]['credential'], {'op': REF})
        self.assertEqual('stored in 1Password vault Test vault', result['credential_storage'])
        self.assertNotIn('op://', result['credential_storage'])
        self.assert_clean(result)
        self.assertFalse((self.env.home / '.env').exists())

    def test_failures_never_commit_or_fallback(self):
        registry.add_site({'id': 'keep', 'label': 'Keep', 'url': 'https://keep.test', 'username': 'keep',
                           'tags': [], 'credential': {'op': REF}})
        original = registry.list_sites()
        for stage in ['vault', 'item', 'read', 'mismatch', 'auth']:
            with self.subTest(stage=stage):
                self.failure = stage
                if stage == 'auth':
                    self.env.transport.responses.update(ReplayTransport(variants=('bad_auth',)).responses)
                with self.assertRaises((credentials.CredentialError, service.ServiceError)) as cm:
                    service.add_site(self.args(), actor='desktop')
                self.assert_clean(cm.exception)
                self.assertEqual(registry.list_sites(), original)
                if stage != 'auth':
                    self.assertEqual(self.env.transport.network_calls, 0)

    def test_explicit_storage_and_vault_required_before_any_io(self):
        for extra in [{'storage': None}, {'storage': 'bogus'}, {'vault_id': ''},
                      {'vault_id': '--help'}, {'url': 'https://other.test'},
                      {'storage': 'existing'}, {'credential': {'op': REF}}]:
            with self.subTest(extra=extra):
                with self.assertRaises((service.ServiceError, registry.RegistryError)):
                    service.plan_add(self.args(**extra))
        self.assertEqual(self.events, [])
        self.assertEqual(self.env.transport.network_calls, 0)

    def test_duplicate_preserves_existing_and_no_cli_calls(self):
        original = {'id': 'wp-example', 'label': 'Old', 'url': BASE, 'username': USER,
                    'tags': [], 'credential': {'op': REF}}
        registry.add_site(original)
        before = registry.list_sites()
        with self.assertRaises(service.ServiceError):
            service.add_site(self.args(), actor='desktop')
        self.assertEqual(registry.list_sites(), before)
        self.assertEqual(self.events, [])

    def test_new_item_tool_creation_requires_approval_and_blocks_unattended(self):
        approval = submodule('approval')
        decision = approval.pre_tool_call(tool_name='wp_site_add', args=self.args())
        self.assertEqual(decision['action'], 'approve')
        self.assertEqual(decision['rule_key'], 'wp-manager:credential-store:wp-example')
        approval.set_cron_probe(lambda: object())
        self.assertEqual(approval.pre_tool_call(tool_name='wp_site_add', args=self.args())['action'], 'block')
        self.assertEqual(self.events, [])
        self.assert_clean(decision)

    def test_vault_endpoint_exposes_metadata_only(self):
        data = op.options('/trusted/op-wrapper')
        self.assertEqual(data, {'available': True, 'vaults': [{'id': V, 'name': 'Test vault'}]})
        self.assert_clean(data)
        self.failure = 'vault'
        data = op.options('/trusted/op-wrapper')
        self.assertFalse(data['available'])
        self.assert_clean(data)

    def test_invalid_or_empty_vault_metadata_disables_option_privately(self):
        for raw in ['[]', '{}', 'invalid', '[{}]', json.dumps([{'id': V, 'name': None}])]:
            with patch.object(op.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=raw, stderr=SECRET)):
                result = op.options('/trusted/op-wrapper')
            self.assertFalse(result['available'])
            self.assertEqual(result['vaults'], [])
            self.assert_clean(result)

    def test_insecure_destination_rejected_before_cli_or_http(self):
        with self.assertRaises(service.ServiceError):
            service.add_site(self.args(connection_string=connection_string(url='http://plain.example.test')), actor='desktop')
        self.assertEqual(self.events, [])
        self.assertEqual(self.env.transport.network_calls, 0)

    def test_missing_selected_vault_no_create(self):
        with self.assertRaises(credentials.CredentialError):
            service.add_site(self.args(vault_id='c' * 26), actor='desktop')
        self.assertEqual(self.events, ['vault'])

    def test_bad_create_response_no_read_auth_or_commit(self):
        for raw in ['not json', '{}', json.dumps({'id': I, 'vault': {'id': 'c' * 26}}),
                    json.dumps({'id': '--bad', 'vault': {'id': V}})]:
            with self.subTest(raw=raw):
                def runner(argv, **kw):
                    if argv[1] == 'item':
                        return SimpleNamespace(returncode=0, stdout=raw, stderr=SECRET)
                    return self.run_cli(argv, **kw)
                with patch.object(op.subprocess, 'run', side_effect=runner):
                    with self.assertRaises(credentials.CredentialError) as cm:
                        service.add_site(self.args(), actor='desktop')
                self.assert_clean(cm.exception)
                self.assertEqual(registry.list_sites(), [])
                self.assertEqual(self.env.transport.network_calls, 0)

    def test_timeout_and_oserror_are_private(self):
        for exc in [subprocess.TimeoutExpired(['op', SECRET], 15, output=SECRET, stderr=SECRET), OSError(SECRET)]:
            with patch.object(op.subprocess, 'run', side_effect=exc):
                with self.assertRaises(credentials.CredentialError) as cm:
                    service.add_site(self.args(), actor='desktop')
            self.assert_clean(cm.exception)
            self.assertIsNone(cm.exception.__cause__)

    def test_http_storage_metadata_and_create_contract(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from test_plugin_api import load_api
        api = load_api()
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/wp-manager')
        with patch.object(api, '_settings_getter', side_effect=lambda key, default=None: '/trusted/op-wrapper' if key == 'op_binary' else default):
            with TestClient(app) as client:
                metadata = client.get('/api/plugins/wp-manager/credential-storage')
                self.assertEqual(metadata.status_code, 200)
                self.assertEqual(metadata.json()['vaults'], [{'id': V, 'name': 'Test vault'}])
                missing = client.post('/api/plugins/wp-manager/sites', json=self.args(storage=None))
                self.assertEqual(missing.status_code, 400)
                result = client.post('/api/plugins/wp-manager/sites', json=self.args())
                self.assertEqual(result.status_code, 200, result.text)
                self.assertIn('1Password', result.json()['credential_storage'])
                self.assert_clean(result.text)

    def test_http_failures_do_not_echo_cli_output(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from test_plugin_api import load_api
        api = load_api()
        app = FastAPI()
        app.include_router(api.router)
        with patch.object(api, '_settings_getter', side_effect=lambda key, default=None: '/trusted/op-wrapper' if key == 'op_binary' else default):
            with TestClient(app) as client:
                self.failure = 'item'
                result = client.post('/sites', json=self.args())
                self.assertEqual(result.status_code, 400)
                self.assert_clean(result.text)
                self.assertEqual(registry.list_sites(), [])

    def test_existing_reference_ignores_connection_password(self):
        self.env.secrets['WPM_EXISTING_APP_PASSWORD'] = 'OtherExistingPassword'
        result = service.add_site(self.args(storage='existing', credential={'env': 'WPM_EXISTING_APP_PASSWORD'}), actor='desktop')
        auth = self.env.transport.calls[0].headers['Authorization']
        self.assertEqual(base64.b64decode(auth.split()[1]).decode(), USER + ':OtherExistingPassword')
        self.assertIn('nothing was written', result['credential_storage'])
        self.assertEqual(self.events, [])
        self.assert_clean(result)
