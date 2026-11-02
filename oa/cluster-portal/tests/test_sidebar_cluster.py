"""Navigation contracts and all-partition read model; no real cluster operations."""
import importlib.machinery
import importlib.util
import json
import subprocess
import pytest
from pathlib import Path

from test_terminal_api import portal
from portalapp import ctl


def signed_in(app, username='alice'):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf']
    client.post('/login', data={'_csrf': csrf, 'username': username, 'password': 'TestPass123'})
    return client


def test_sidebar_routes_preserve_auth_and_profile(portal, monkeypatch):
    monkeypatch.setattr(ctl, 'cluster_status', lambda: {'ok': True, 'partitions': [], 'nodes': []}, raising=False)
    client = signed_in(portal)
    for path in ['/status', '/status/active', '/assets/datasets', '/assets/models', '/assets/images']:
        assert portal.test_client().get(path).status_code == 302
        assert client.get(path).status_code == 200, path
    for path in ['/guide', '/guide/development', '/guide/terminal']:
        assert portal.test_client().get(path).status_code == 200, path
    text = client.get('/my').get_data(as_text=True)
    for path in ['/my', '/images', '/apply', '/profile', '/logout', '/status/active', '/assets/datasets', '/guide/terminal']:
        assert 'href="'+path+'"' in text
    assert 'href="/admin/users"' not in text


def test_cluster_page_has_partitions_and_no_duplicate_personal_table(portal, monkeypatch):
    nodes = [dict(name='n3090', partitions=['3090'], gres_model='3090', cpus_idle=16, cpus_total=32, mem_mb=65536, mem_free_mb=32768, gres='gpu:3090:2', gres_total=2, gres_free=1, state='mix'),
             dict(name='n4090', partitions=['4090', 'shared'], gres_model='4090', cpus_idle=8, cpus_total=32, mem_mb=65536, mem_free_mb=None, gres='gpu:4090:4', gres_total=4, gres_free=None, state='mix')]
    monkeypatch.setattr(ctl, 'cluster_status', lambda: {'ok': True, 'nodes': nodes, 'partitions': ['3090', '4090', 'shared']}, raising=False)
    text = signed_in(portal).get('/status').get_data(as_text=True)
    assert '集群占用情况' in text and 'n3090' in text and 'n4090' in text
    assert '4090' in text and '3090' in text and 'shared' in text
    assert '我的资源（管理员视图）' not in text


def test_new_helper_queries_all_partitions_without_changing_legacy(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / 'deploy/portal-ctl'
    loader = importlib.machinery.SourceFileLoader('sidebar_ctl', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    calls = []
    def output(argv, **kwargs):
        calls.append(argv)
        if 'scontrol' == argv[0]:
            return 'NodeName=g1 AllocMem=8192\nNodeName=g2 AllocMem=16384'
        if '--Format=NodeList:256|,GresUsed:4096|' in argv:
            return 'g1|gpu:3090:1|\ng2|gpu:4090:2|'
        return '3090*|g1|mixed|8/8/0/16|gpu:3090:2|32768|up\n4090|g2|mixed|16/16/0/32|gpu:4090:4|65536|up\nshared|g2|mixed|16/16/0/32|gpu:4090:4|65536|up'
    monkeypatch.setattr(module, 'sh_out', output)
    monkeypatch.setattr(module, 'nodes_ips', lambda names: {'g1': '192.0.2.1', 'g2': '192.0.2.2'})
    monkeypatch.setattr(module, 'run', lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, output(argv), ''))
    with pytest.raises(SystemExit) as done:
        module.cmd_cluster_status()
    assert done.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result['partitions'] == ['3090', '4090', 'shared']
    assert len(result['nodes']) == 2  # g2 belongs to two partitions, one physical node
    by_name = {n['name']: n for n in result['nodes']}
    assert by_name['g2']['partitions'] == ['4090', 'shared']
    assert by_name['g1']['gres_free'] == 1
    assert by_name['g2']['gres_free'] == 2
    assert by_name['g1']['mem_free_mb'] == 24576
    assert all('-p' not in argv for argv in calls)


def test_unknown_usage_and_shared_nodes_are_not_false_zero_or_double_counted():
    from portalapp.cluster_views import cluster_context
    data = {'ok': True, 'nodes': [
        dict(name='a', partitions=['3090'], gres_model='3090', gres_total=2, gres_free=1),
        dict(name='b', partitions=['4090'], gres_model='4090', gres_total=4, gres_free=None),
        dict(name='b', partitions=['shared'], gres_model='4090', gres_total=4, gres_free=None)]}
    result = cluster_context(data)
    assert result['overall']['nodes'] == 2
    assert result['overall']['gres_total'] == 6
    assert result['overall']['gpu_used'] is None
    assert result['overall']['gpu_percent'] is None
    assert len(result['partitions']) == 3


def test_asset_placeholders_do_not_call_backend(portal, monkeypatch):
    client = signed_in(portal)
    def unavailable(*args, **kwargs):
        raise AssertionError('placeholder pages must not query a backend')
    monkeypatch.setattr(ctl, 'run', unavailable)
    for path in ['/assets/datasets', '/assets/models']:
        response = client.get(path)
        assert response.status_code == 200
        assert '功能待完善' in response.get_data(as_text=True)
        assert '尚无后端数据' in response.get_data(as_text=True)


def test_failed_cluster_reader_reports_error(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / 'deploy/portal-ctl'
    loader = importlib.machinery.SourceFileLoader('failed_cluster_ctl', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    monkeypatch.setattr(module, 'run', lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, '', 'slurm unavailable'))
    with pytest.raises(SystemExit) as done:
        module.cmd_cluster_status()
    assert done.value.code != 0
    assert json.loads(capsys.readouterr().out)['ok'] is False


@pytest.mark.parametrize('text,want', [
    ('gpu:rtx_pro_6000_blackwell:3(IDX:0-2)', 3),
    ('gpu:3090:2(IDX:0,1),gpu:4090:1(IDX:2)', 3),
    ('mps:400,gpu:4090:2(IDX:0-1)', 2),
    ('gpu:4090:0(IDX:N/A)', 0),
    ('(null)', 0),
    ('gpu:rtx_pro_6000_bla', None),
    ('gpu:3090:1(IDX:0),gpu:rtx_pro_6000_bla', None),
    ('gpu:4090:2(IDX:0-', None),
    ('N/A', None),
    ('gpu:' + 'a' * 4090 + ':1', None),
])
def test_cluster_gpu_count_rejects_truncation(text, want):
    path = Path(__file__).resolve().parents[1] / 'deploy/portal-ctl'
    loader = importlib.machinery.SourceFileLoader('gres_parser_ctl', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    assert module._cluster_gpu_count(text) == want


def test_long_and_mixed_gpu_types_survive_slurm_formatting(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / 'deploy/portal-ctl'
    loader = importlib.machinery.SourceFileLoader('long_gres_ctl', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    usage = {'long-node-name-exceeds-twenty-characters': 'gpu:rtx_pro_6000_blackwell:3(IDX:0-2)',
             'mixed-node': 'gpu:3090:2(IDX:0,1),gpu:4090:1(IDX:2)'}
    def execute(argv, **kwargs):
        if argv[0] == 'scontrol':
            text = 'NodeName=long-node-name-exceeds-twenty-characters AllocMem=0\nNodeName=mixed-node AllocMem=0'
        elif any(arg.startswith('--Format=NodeList') for arg in argv):
            fmt = next(arg for arg in argv if arg.startswith('--Format='))
            if fmt == '--Format=NodeList,GresUsed':
                text = '\n'.join(f'{name[:20]:20}{value[:20]:20}' for name, value in usage.items())
            else:
                assert fmt == '--Format=NodeList:256|,GresUsed:4096|'
                text = '\n'.join(f'{name:256}|{value:4096}|' for name, value in usage.items())
        else:
            text = 'pro6000|long-node-name-exceeds-twenty-characters|mixed|8/8/0/16|gpu:rtx_pro_6000_blackwell:4|32768|up\nmixed|mixed-node|mixed|8/8/0/16|gpu:3090:2,gpu:4090:2|32768|up'
        return subprocess.CompletedProcess(argv, 0, text, '')
    monkeypatch.setattr(module, 'run', execute)
    monkeypatch.setattr(module, 'nodes_ips', lambda names: {})
    with pytest.raises(SystemExit):
        module.cmd_cluster_status()
    result = json.loads(capsys.readouterr().out)
    rows = {n['name']: n for n in result['nodes']}
    assert rows['long-node-name-exceeds-twenty-characters']['gres_used'] == 3
    assert rows['long-node-name-exceeds-twenty-characters']['gres_free'] == 1
    assert rows['mixed-node']['gres_used'] == 3
    assert rows['mixed-node']['gres_free'] == 1
