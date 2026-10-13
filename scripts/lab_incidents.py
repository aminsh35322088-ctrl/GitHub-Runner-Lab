#!/usr/bin/env python3
"""Bounded, sanitized, deduplicated Lab incident reports via the stored gh identity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from lab_common import atomic, kit, lock

REPOSITORY = 'aminsh35322088-ctrl/GitHub-Runner-Lab'
CATEGORIES = ('HOST', 'TOOLCHAIN', 'NETWORK', 'RDC', 'DOCKER', 'MEDIA', 'PERSISTENCE')


def sanitize(text):
    text = str(text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,}|\d{8,12}:[A-Za-z0-9_-]{20,})',
                  '[REDACTED]', text)
    text = re.sub(r'(?im)^.*(?:authorization|cookie|password|secret|api[_-]?key|access[_-]?token|token|credential)\s*[:=].*$',
                  '[REDACTED credential line]', text)
    text = re.sub(r'https?://[^\s/@]+:[^\s/@]+@', 'https://[REDACTED]@', text)
    text = re.sub(r'([?&](?:token|key|secret|password|signature)=)[^&\s]+', r'\1[REDACTED]', text, flags=re.I)
    # Also remove known credential values, without recording names or environment dumps.
    for name, value in os.environ.items():
        if re.search(r'token|secret|password|api_?key', name, re.I) and len(value) >= 6:
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    text = text.replace('<!--', '&lt;!--').replace('-->', '--&gt;')
    return text[:12000]


def make_report(category, key, title, steps, expected, actual, occurrences):
    if category not in CATEGORIES:
        raise ValueError('Only Lab failure categories are reportable; project errors belong to their project')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,79}', key):
        raise ValueError('Use a stable lowercase problem key (2-80 characters)')
    if occurrences < 2:
        raise ValueError('Reproduce the failure at least twice before reporting')
    if any(not value.strip() for value in (title, steps, expected, actual)):
        raise ValueError('Title, reproduction, expected and actual results are required')
    fingerprint = hashlib.sha256(f'{REPOSITORY}:{category}:{key}'.encode()).hexdigest()[:24]
    fields = {name: sanitize(value) for name, value in
              [('title', title[:160]), ('steps', steps), ('expected', expected), ('actual', actual)]}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:24]
    marker = f'<!-- lab-incident:{fingerprint} -->'
    evidence = f'<!-- lab-evidence:{digest} -->'
    from lab_environment import capture
    environment = capture()
    context = sanitize(json.dumps(environment, sort_keys=True))
    body = (f'{marker}\n{evidence}\n## Lab incident: {category}\n\n'
            f'### Reproduction\n{fields["steps"]}\n\n### Expected\n{fields["expected"]}\n\n'
            f'### Actual\n{fields["actual"]}\n\nConfirmed observations: {occurrences}\n')
    body += f'\n### Environment\n```json\n{context}\n```\n'
    return {'schema_version': 1, 'fingerprint': fingerprint, 'marker': marker,
            'evidence_marker': evidence, 'title': f'[{category}] {fields["title"]}',
            'body': body, 'created': int(time.time())}


def api(method, endpoint, payload=None):
    args = ['gh', 'api', '--method', method, endpoint]
    if payload is not None:
        args += ['--input', '-']
    # Workflow GH_TOKEN has different scopes. Use the user's preconfigured gh identity.
    env = {k: v for k, v in os.environ.items() if k not in ('GH_TOKEN', 'GITHUB_TOKEN')}
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=25, env=env)
    if result.returncode:
        raise RuntimeError('GitHub API failed; report retained locally (no blind write retries)')
    return json.loads(result.stdout)


def pages(endpoint):
    for page in range(1, 101):
        separator = '&' if '?' in endpoint else '?'
        items = api('GET', f'{endpoint}{separator}per_page=100&page={page}')
        if not isinstance(items, list):
            raise RuntimeError('Unexpected GitHub response; refusing to create a duplicate')
        yield from items
        if len(items) < 100:
            return
    raise RuntimeError('Pagination limit reached; refusing unverified duplicate lookup')


def publish(report, dry_run=False):
    if dry_run:
        return {'status': 'dry-run', 'title': report['title'], 'body': report['body']}
    root = kit() / 'incidents'
    with lock(root / '.lock'):
        pending = root / f'{report["fingerprint"]}.json'
        atomic(pending, report)
        endpoint = f'repos/{REPOSITORY}/issues'
        for issue in pages(endpoint + '?state=open'):
            if 'pull_request' in issue or report['marker'] not in (issue.get('body') or ''):
                continue
            comments = f'{endpoint}/{int(issue["number"])}/comments'
            same = report['evidence_marker'] in (issue.get('body') or '')
            if not same:
                same = any(report['evidence_marker'] in (comment.get('body') or '') for comment in pages(comments))
            if same:
                result = {'status': 'duplicate', 'url': issue.get('html_url')}
            else:
                response = api('POST', comments, {'body': report['body']})
                result = {'status': 'commented', 'url': response['html_url']}
            pending.unlink()
            return result
        response = api('POST', endpoint, {'title': report['title'], 'body': report['body']})
        pending.unlink()
        return {'status': 'created', 'url': response['html_url']}


def observe_validation(summary):
    """Report only repeated real default-profile failures, never raw validation logs."""
    categories = sorted(set(summary.get('failure_categories', [])) & set(CATEGORIES))
    path = kit() / 'validation-incidents.json'
    with lock(kit() / 'validation-incidents.lock'):
        previous = json.loads(path.read_text()) if path.exists() else {}
        current = {category: previous.get(category, 0) + 1 for category in categories}
        atomic(path, current)
    for category, count in current.items():
        if count < 2:
            continue
        report = make_report(category, 'runner-validation-' + category.lower(),
                             'Repeated runner validation failure',
                             'Run agent-lab validate runner ' + summary['profile'],
                             'Runner validation passes',
                             'Validation repeatedly reports ' + category +
                             '. Inspect local runner validation reports for details.', count)
        publish(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--category', choices=CATEGORIES)
    parser.add_argument('--key')
    parser.add_argument('--title')
    parser.add_argument('--steps')
    parser.add_argument('--expected')
    parser.add_argument('--actual')
    parser.add_argument('--occurrences', type=int, default=1)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--flush', action='store_true')
    args = parser.parse_args()
    if args.flush:
        for path in sorted((kit() / 'incidents').glob('*.json')):
            report = json.loads(path.read_text())
            # Only process our schema and internally generated fingerprint filenames.
            if report.get('schema_version') != 1 or path.stem != report.get('fingerprint'):
                raise ValueError('Invalid pending incident')
            print(json.dumps(publish(report, args.dry_run)))
        return
    if any(getattr(args, field) is None for field in ('category', 'key', 'title', 'steps', 'expected', 'actual')):
        parser.error('--category, --key, --title, --steps, --expected and --actual are required')
    print(json.dumps(publish(make_report(args.category, args.key, args.title, args.steps,
                                        args.expected, args.actual, args.occurrences), args.dry_run)))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print('Incident report failed; check GitHub access and pending reports with issue --flush.', file=sys.stderr)
        raise SystemExit(1)
