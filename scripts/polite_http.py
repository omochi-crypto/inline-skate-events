"""One serial client, explicit budgets, robots checks, no automatic retries."""
import re
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urljoin, unquote
from source_policy import host_key

AGENT = 'InlineEvents/1.1 (+https://github.com/omochi-crypto/inline-skate-events)'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def robot_rules(raw, url):
    """Conservative REP subset: combine applicable groups, block disallow matches.

    Allow exceptions are intentionally not used to relax disallow rules. This can
    over-block, never expands an explicitly disallowed wildcard path.
    """
    if any(ord(c) < 32 and c not in '\t\r\n' for c in raw):
        raise ValueError('Invalid control character in robots.txt')
    groups, agents, rules = [], [], []
    for line in raw.splitlines() + ['User-agent: __end__']:
        line = line.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = (v.strip() for v in line.split(':', 1))
        key = key.lower()
        if key == 'user-agent':
            if not value:
                raise ValueError('Empty robots user-agent; review required')
            if rules:
                groups.append((agents, rules)); agents, rules = [], []
            agents.append(value.lower())
        elif agents:
            rules.append((key, value))
    explicit = [r for a, r in groups if any(x != '*' and x in 'inlineevents' for x in a)]
    selected = explicit or [r for a, r in groups if '*' in a]
    meaningful = [line.split('#', 1)[0].strip() for line in raw.splitlines()]
    if any(meaningful) and not re.search(r'(?im)^\s*user-agent\s*:', raw):
        raise ValueError('Unrecognized robots content; review required')
    target = unquote(urlparse(url).path) or '/'
    if urlparse(url).query:
        target += '?' + unquote(urlparse(url).query)
    delay = 10.0
    for rules in selected:
        for key, value in rules:
            if key == 'crawl-delay':
                try:
                    number = float(value)
                    if not math.isfinite(number) or number < 0:
                        raise ValueError('Invalid delay')
                    delay = max(delay, number)
                except ValueError: raise ValueError('Unclear crawl-delay; stop')
            if key == 'disallow' and value:
                if not value.startswith('/'):
                    raise ValueError('Unclear robots disallow path; stop')
                end = value.endswith('$')
                pattern = re.escape(unquote(value[:-1] if end else value)).replace(r'\*', '.*')
                if re.match(pattern + ('$' if end else ''), target):
                    raise ValueError('robots.txt disallows this URL')
    return delay


class Client:
    def __init__(self, hosts, opener=None, clock=time.time, sleeper=time.sleep):
        self.hosts = hosts
        self.opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.clock, self.sleep = clock, sleeper
        self.robots, self.metrics = {}, []
        self.site_counts, self.site_limits = {}, {}
        self.max_requests = 24
        self.max_bytes = 10_000_000
        self.bytes_received = 0
        self.byte_budget_used = 0
        self.source = None

    def begin(self, source):
        self.source = source
        self.remaining = 3 if source.get('small_site') else 5
        key = host_key(source['url'])
        limit = 3 if source.get('small_site') else 8
        self.site_limits[key] = min(limit, self.site_limits.get(key, limit))

    def request(self, url, limit):
        parsed = urlparse(url)
        origin = urlparse(self.source['url'])
        if parsed.scheme != 'https' or parsed.netloc != origin.netloc or parsed.username or parsed.password:
            raise ValueError('Cross-origin/non-HTTPS fetch needs separate review')
        host = self.hosts.setdefault(host_key(url), {})
        if host.get('paused'):
            raise ValueError('Host paused: ' + host['paused'])
        if self.remaining <= 0:
            raise ValueError('Per-source request budget reached')
        site = host_key(url)
        if (self.site_counts.get(site, 0) >= self.site_limits[site]
                or len(self.metrics) >= self.max_requests):
            raise ValueError('Shared site/run request budget reached')
        if self.max_bytes - self.byte_budget_used <= 1:
            raise ValueError('Run byte budget reached')
        self.remaining -= 1
        wait = host.get('last_end', 0) + host.get('delay', 10) - self.clock()
        if wait > 0:
            if wait > 60:
                raise ValueError('Site delay exceeds run allowance; defer')
            self.sleep(wait)
        allowance = min(limit, self.max_bytes-self.byte_budget_used-1)
        reservation = allowance+1
        self.byte_budget_used += reservation
        record = {'host': parsed.netloc, 'bytes': 0, 'budget_charge':reservation}
        self.metrics.append(record)
        self.site_counts[site] = self.site_counts.get(site, 0) + 1
        try:
            req = urllib.request.Request(url, headers={'User-Agent': AGENT})
            with self.opener(req, timeout=25) as response:
                raw = response.read(allowance + 1)
                self.bytes_received += len(raw)
                record.update(status=response.status, bytes=len(raw))
                if len(raw) > allowance:
                    raise ValueError('Response exceeds size limit')
                self.byte_budget_used -= reservation-len(raw)
                record['budget_charge'] = len(raw)
                return raw, response.headers.get_content_charset()
        except urllib.error.HTTPError as exc:
            record['status'] = exc.code
            # All errors stop this request. Restriction/overload responses pause
            # the whole host across runs, not just the individual URL.
            if exc.code in (401, 403, 429) or exc.code >= 500:
                host['paused'] = 'HTTP ' + str(exc.code)
                host['retry_after'] = exc.headers.get('Retry-After') if exc.headers else None
            exc.close()
            raise
        finally:
            host['last_end'] = self.clock()

    def get(self, url, limit=2_000_000):
        parsed = urlparse(url)
        robot_url = urljoin(url, '/robots.txt')
        if parsed.netloc not in self.robots:
            try:
                raw, _ = self.request(robot_url, 512_000)
                robots = raw.decode('utf-8-sig', errors='strict')
                if '<html' in robots.lower():
                    raise ValueError('robots.txt returned HTML; manual review required')
            except urllib.error.HTTPError as exc:
                if exc.code not in (404, 410):
                    self.hosts.setdefault(host_key(url), {})['paused'] = 'robots.txt unavailable; review required'
                    raise
                robots = ''
            except Exception:
                self.hosts.setdefault(host_key(url), {})['paused'] = 'robots.txt unclear; review required'
                raise
            self.robots[parsed.netloc] = robots
        delay = robot_rules(self.robots[parsed.netloc], url)
        self.hosts.setdefault(host_key(url), {})['delay'] = delay
        raw, charset = self.request(url, limit)
        prefix = raw[:100_000].lower()
        if b'<html' in prefix[:2000] and any(word in prefix for word in (
                b'cf-chl-', b'verify you are human', b'checking your browser')):
            self.hosts[host_key(url)]['paused'] = 'Access challenge; no bypass permitted'
            raise ValueError('Access challenge; no bypass permitted')
        return raw, charset
