"""Validate collection configuration before any API or origin access."""
import re
from urllib.parse import urlparse
from event_model import CATEGORIES


def host_key(url):
    return (urlparse(url).hostname or '').lower()


def fingerprint(source):
    return {k: source.get(k) for k in ('url', 'adapter', 'selector', 'category', 'prefecture',
                                      'small_site', 'min_interval_seconds')}


def validate_sources(sources):
    ids, urls, small_hosts, host_types = set(), set(), set(), {}
    if not isinstance(sources, list) or not sources:
        raise ValueError('Source list must be nonempty')
    for source in sources:
        if (not isinstance(source, dict) or not isinstance(source.get('id'), str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', source['id'])):
            raise ValueError('Invalid source identifier')
        url = urlparse(source.get('url', ''))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or url.port not in (None,443):
            raise ValueError('Source URL needs reviewed HTTPS without credentials/fragment')
        if type(source.get('small_site')) is not bool:
            raise ValueError('Source must explicitly declare small_site')
        if source.get('adapter') not in ('cards','calendar','gia','articles','pdf_page'):
            raise ValueError('Unknown source adapter')
        if source.get('category') not in CATEGORIES:
            raise ValueError('Unknown source category')
        interval=source.get('min_interval_seconds',172800)
        if type(interval) is not int or interval < (604800 if source['small_site'] else 172800):
            raise ValueError('Collection interval is shorter than policy')
        canonical = url._replace(netloc=url.hostname.lower()).geturl().rstrip('/')
        if source['id'] in ids or canonical in urls:
            raise ValueError('Duplicate source ID/URL would reset a fetch budget')
        host = host_key(source['url'])
        if host in host_types and host_types[host] != source['small_site']:
            raise ValueError('All entries for one host must use the same small-site policy')
        host_types[host] = source['small_site']
        if source['small_site'] and host in small_hosts:
            raise ValueError('Use one entry point per small-site host')
        ids.add(source['id']);urls.add(canonical)
        if source['small_site']:small_hosts.add(host)
