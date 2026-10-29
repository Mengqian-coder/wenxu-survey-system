"""Read local document references without changing historical evidence."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def markdown_targets(text):
    # Repository documents use inline links; balance parentheses for real names.
    clean = re.sub(r'^\s*```.*?^\s*```\s*$', '', text, flags=re.M | re.S)
    for match in re.finditer(r'!?\[[^\n]*?\]\(', clean):
        start = match.end()
        depth, end = 1, start
        while end < len(clean) and depth:
            char = clean[end]
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            end += 1
        if depth == 0:
            target = clean[start:end - 1].strip()
            if target.startswith('<'):
                target = target[1:target.find('>')]
            else:
                target = re.split(r'\s+[\"\']', target, maxsplit=1)[0]
            yield target
    for match in re.finditer(r'^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?', clean, re.M):
        yield match.group(1)


def local_target(document, target, repo):
    target = target.replace('\\', '/')
    if not target or target.startswith(('#', '?', '//')):
        return None
    parsed = urlsplit(target)
    if parsed.scheme:
        return None
    path = unquote(parsed.path)
    if not path:
        return None
    return (repo / path.lstrip('/') if path.startswith('/') else document.parent / path).resolve()


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ('src', 'href', 'poster'):
            if key in attrs and (tag != 'a' or key == 'href'):
                self.targets.append(attrs[key])


def check(repo):
    documents = [repo / 'README.md', repo / 'update.md']
    documents.extend(repo / '01_代码' / name / 'README.md' for name in ('backend', 'frontend'))
    for directory in ('02_文档', '03_测试', '04_项目完成报告'):
        documents.extend(sorted((repo / directory).rglob('*.md')))
    rows = []
    for document in documents:
        if not document.exists():
            continue
        for target in markdown_targets(document.read_text(encoding='utf-8-sig')):
            resolved = local_target(document, target, repo)
            if resolved is None:
                continue
            try:
                relative = resolved.relative_to(repo).as_posix()
            except ValueError:
                relative = str(resolved)
            rows.append({'document': document.relative_to(repo).as_posix(), 'target': target,
                         'resolved': relative, 'exists': resolved.exists(),
                         'runtimeOutputDependency': relative.startswith('01_代码/output/')})
    resources = []
    html_files = list((repo / '03_测试' / '证据').rglob('*.html'))
    html_files += list((repo / '01_代码' / 'frontend' / 'dist').rglob('*.html'))
    for document in html_files:
        parser = Resources()
        parser.feed(document.read_text(encoding='utf-8-sig'))
        for target in parser.targets:
            # Dist absolute asset URLs are rooted in dist, not the repository.
            root = repo / '01_代码' / 'frontend' / 'dist' if 'dist' in document.parts else repo
            resolved = local_target(document, target, root)
            if resolved is not None:
                resources.append({'document': document.relative_to(repo).as_posix(), 'target': target,
                                  'exists': resolved.exists()})
    return {'documents': [p.relative_to(repo).as_posix() for p in documents if p.exists()],
            'documentCount': sum(p.exists() for p in documents), 'localReferenceCount': len(rows),
            'missingReferences': [row for row in rows if not row['exists']],
            'runtimeOutputDependencies': [row for row in rows if row['runtimeOutputDependency']],
            'htmlResourceCount': len(resources), 'missingHtmlResources': [r for r in resources if not r['exists']],
            'references': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repo', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = check(args.repo.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('references', 'documents')}, ensure_ascii=False))
    raise SystemExit(1 if result['missingReferences'] or result['runtimeOutputDependencies'] or result['missingHtmlResources'] else 0)
