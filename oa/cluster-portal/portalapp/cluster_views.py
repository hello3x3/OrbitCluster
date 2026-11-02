"""Read-only grouping for cluster pages; does not influence scheduling."""
from collections import OrderedDict
from . import siteconf


def gpu_label(token):
    if str(token).lower().replace('_', '').replace('-', '').replace(' ', '') in ('pro6000', 'rtxpro6000'):
        return 'RTX PRO 6000'
    return siteconf.pretty_gpu(token) if token else 'CPU / 未标注型号'


def totals(nodes):
    result = {'nodes': len(nodes)}
    for field in ('cpus_total', 'cpus_idle', 'mem_mb', 'mem_free_mb', 'gres_total', 'gres_free'):
        values = [n.get(field) for n in nodes]
        result[field] = sum(values) if all(isinstance(v, (int, float)) for v in values) else None
    total, free = result['gres_total'], result['gres_free']
    result['gpu_used'] = max(0, total - free) if total is not None and free is not None else None
    result['gpu_percent'] = round(100 * result['gpu_used'] / total) if total and result['gpu_used'] is not None else None
    return result


def cluster_context(data):
    unique = OrderedDict()
    for raw in data.get('nodes', []):
        node = dict(raw)
        name = node.get('name')
        if not name:
            continue
        parts = node.get('partitions') or [data.get('partition') or 'gpu']
        if name in unique:
            unique[name]['partitions'] = list(dict.fromkeys(unique[name]['partitions'] + parts))
        else:
            node['partitions'] = list(dict.fromkeys(parts))
            unique[name] = node
    partitions = list(data.get('partitions') or [])
    for node in unique.values():
        for part in node['partitions']:
            if part not in partitions:
                partitions.append(part)
    views = []
    for part in partitions:
        nodes = [node for node in unique.values() if part in node['partitions']]
        grouped = OrderedDict()
        for node in nodes:
            grouped.setdefault(gpu_label(node.get('gres_model')), []).append(node)
        views.append({'name': part, 'nodes': nodes, 'totals': totals(nodes),
                      'groups': [{'name': model, 'nodes': rows} for model, rows in grouped.items()]})
    return {'partitions': views, 'overall': totals(list(unique.values())),
            'cluster_error': data.get('error') if not data.get('ok', True) else ''}
