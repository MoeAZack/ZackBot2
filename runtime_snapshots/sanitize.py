"""Conservative local redaction. Reports positions/kinds, never matched values."""
import ast
import io
import re
import tokenize

MARKER = 'REDACTED_VALUE'
SENSITIVE = re.compile(r'(?:api[_-]?(?:key|secret)|secret|token|password|passwd|private[_-]?key|(?:account|user|chat)[_-]?id)', re.I)
PATTERNS = [
    ('private key', re.compile(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\r\n]*PRIVATE KEY-----|$)')),
    ('known token format', re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{16,}|\b\d{6,}:[A-Za-z0-9_-]{20,})')),
    ('long hex string', re.compile(r'(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])')),
    ('long base64-like string', re.compile(r'(?<![A-Za-z0-9_])[A-Za-z0-9_+/-]{40,}={0,2}(?![A-Za-z0-9_])')),
]
DIRECT = re.compile(r'''(?i)(?:api[_-]?(?:key|secret)|secret|token|password|passwd|private[_-]?key|(?:account|user|chat)[_-]?id)[\w-]*["']?\s*[:=]\s*(?:["']([^"'\r\n]+)["']|(\d{3,}))''')

def value_kinds(text):
    return {kind for kind, pattern in PATTERNS if pattern.search(text)}

def sanitize(data, suffix='.py'):
    text = data.decode('utf-8-sig')
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    spans = {}
    def add(start, end, kind):
        spans.setdefault((start, end), set()).add(kind)
    def token_span(tok):
        return offsets[tok.start[0]-1] + tok.start[1], offsets[tok.end[0]-1] + tok.end[1]
    if suffix == '.py':
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        string_tokens = [t for t in tokens if t.type == tokenize.STRING]
        for tok in string_tokens:
            for kind in value_kinds(tok.string):
                add(*token_span(tok), kind)
        tree = ast.parse(text)
        def redact_value(node, name):
            if not SENSITIVE.search(name):
                return
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)) and node.value not in ('', None, MARKER, 0):
                # AST columns are UTF-8 byte offsets; tokenize offsets are characters.
                def char_col(line, column):
                    return len(text.splitlines()[line-1].encode('utf-8')[:column].decode('utf-8'))
                start = offsets[node.lineno-1] + char_col(node.lineno, node.col_offset)
                end = offsets[node.end_lineno-1] + char_col(node.end_lineno, node.end_col_offset)
                add(start, end, 'credential/account/user-id literal (conservative)')
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    redact_value(node.value, ast.unparse(target))
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        redact_value(value, key.value)
            elif isinstance(node, ast.keyword):
                redact_value(node.value, node.arg or '')
        # Comments also undergo the strict token/hex/base64/context screen.
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                start, end = token_span(tok)
                kinds = value_kinds(tok.string)
                if DIRECT.search(tok.string):
                    kinds.add('credential/id in comment')
                for kind in kinds:
                    add(start, end, kind)
    else:
        # Non-Python: replace only the matched values, not code structure.
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(text):
                add(match.start(), match.end(), kind)
        for match in DIRECT.finditer(text):
            group = 1 if match.group(1) is not None else 2
            if match.group(group) != MARKER:
                add(match.start(group), match.end(group), 'credential/account/user-id literal (conservative)')
    # Merge nested ranges; overlapping independent ranges are deliberately doubtful.
    selected = []
    for (start, end), kinds in sorted(spans.items(), key=lambda item: (item[0][0], -item[0][1])):
        if selected and start < selected[-1][1]:
            if end <= selected[-1][1]:
                selected[-1][2].update(kinds)
                continue
            raise ValueError('Doubtful overlapping redaction')
        selected.append([start, end, kinds])
    chunks, records, position = [], [], 0
    output_line = 1
    for start, end, kinds in selected:
        prefix = text[position:start]
        chunks.append(prefix)
        output_line += prefix.count('\n')
        original_line = text.count('\n', 0, start) + 1
        replacement = '# REDACTED_VALUE' if text[start:end].startswith('#') else (repr(MARKER) if suffix == '.py' else MARKER)
        records.append({'line': original_line, 'snapshot_line': output_line, 'kind': '; '.join(sorted(kinds))})
        chunks.append(replacement)
        position = end
    chunks.append(text[position:])
    if not records:
        return data, []
    output = ''.join(chunks).encode('utf-8')
    if suffix == '.py':
        ast.parse(output)
    return output, records

def remaining_hits(data, suffix):
    """Scan values, comments and direct literal assignments; never return values."""
    text = data.decode('utf-8-sig')
    hits = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            hits.append({'line': text.count('\n', 0, match.start()) + 1, 'kind': kind})
    for match in DIRECT.finditer(text):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if value != MARKER:
            hits.append({'line': text.count('\n', 0, match.start()) + 1, 'kind': 'credential/account/user-id assignment'})
    return hits
