"""One serial client, explicit budgets, robots checks, no automatic retries."""
import re
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urljoin, unquote

AGENT = 'InlineEvents/1.1 (+https://github.com/omochi-crypto/inline-skate-events)'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def robot_rules(raw, url):
    """Conservative REP subset: combine applicable groups, block disallow matches.

    Allow exceptions are intentionally not used to relax disallow rules. This can
    over-block, never expands an explicitly disallowed wildcard path.
    """
    groups, agents, rules = [], [], []
    for line in raw.splitlines() + ['User-agent: __end__']:
        line = line.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = (v.strip() for v in line.split(':', 1))
        key = key.lower()
        if key == 'user-agent':
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
        target += '?' + urlparse(url).query
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
        self.source = None

    def begin(self, source):
        self.source = source
        self.remaining = 3 if source.get('small_site') else 5

    def request(self, url, limit):
        parsed = urlparse(url)
        origin = urlparse(self.source['url'])
        if parsed.scheme != 'https' or parsed.netloc != origin.netloc:
            raise ValueError('Cross-origin/non-HTTPS fetch needs separate review')
        host = self.hosts.setdefault(parsed.netloc, {})
        if host.get('paused'):
            raise ValueError('Host paused: ' + host['paused'])
        if self.remaining <= 0:
            raise ValueError('Per-source request budget reached')
        self.remaining -= 1
        wait = host.get('last_end', 0) + host.get('delay', 10) - self.clock()
        if wait > 0:
            if wait > 60:
                raise ValueError('Site delay exceeds run allowance; defer')
            self.sleep(wait)
        record = {'host': parsed.netloc, 'bytes': 0}
        self.metrics.append(record)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': AGENT})
            with self.opener(req, timeout=25) as response:
                raw = response.read(limit + 1)
                record.update(status=response.status, bytes=len(raw))
                if len(raw) > limit:
                    raise ValueError('Response exceeds size limit')
                return raw, response.headers.get_content_charset()
        except urllib.error.HTTPError as exc:
            record['status'] = exc.code
            # All errors stop this request. Restriction/overload responses pause
            # the whole host across runs, not just the individual URL.
            if exc.code in (401, 403, 429) or exc.code >= 500:
                host['paused'] = 'HTTP ' + str(exc.code)
                host['retry_after'] = exc.headers.get('Retry-After') if exc.headers else None
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
                    self.hosts.setdefault(parsed.netloc, {})['paused'] = 'robots.txt unavailable; review required'
                    raise
                robots = ''
            except Exception:
                self.hosts.setdefault(parsed.netloc, {})['paused'] = 'robots.txt unclear; review required'
                raise
            self.robots[parsed.netloc] = robots
        delay = robot_rules(self.robots[parsed.netloc], url)
        self.hosts.setdefault(parsed.netloc, {})['delay'] = delay
        return self.request(url, limit)
