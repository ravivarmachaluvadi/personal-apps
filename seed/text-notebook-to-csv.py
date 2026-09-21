"""Turn a typed password notebook (plain text, blank line between entries) into the vault import CSV.

Usage: python seed/text-notebook-to-csv.py <notebook.txt> <out.csv>
Reuses the block parser from ocr-notebook-to-csv.py by laying the lines out as one synthetic page,
then cleans values, resolves placeholder passwords, merges duplicate entries and prints a masked summary.
"""
import sys, os, re, csv, collections, importlib.util, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('ocr', os.path.join(HERE, 'ocr-notebook-to-csv.py'))
ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


def clean_line(line):
    line = line.replace(' ', ' ').replace('’', "'").replace('\t', ' ')
    return re.sub(r'\s+', ' ', line).strip()


def main(src, out_csv):
    raw = open(src, 'rb').read()
    text = raw.decode('utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8', errors='replace')
    lines = [clean_line(l) for l in text.replace('\r\n', '\n').split('\n')]
    rows, y = [], 100
    for l in lines:
        if not l:
            y += 120
            continue
        rows.append((80, y, l))
        y += 100
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, 'page01.txt'), 'w', encoding='utf-8') as f:
        for x, yy, t in rows:
            f.write('%d\t%d\t%s\n' % (x, yy, t))
    pages = ocr.load_pages(tmp)
    entries = [e for e in ocr.build_entries(pages) if not ocr.empty(e)]
    for e in entries:
        ocr.finish(e)

    fixes = collections.Counter()
    counts = collections.Counter([e.password for e in entries if e.password] + [n for e in entries for n in e.notes])
    placeholders = {v for v, n in counts.items() if len(v) <= 4 and n >= 4 and not v.isdigit()}
    for e in entries:
        if e.password in placeholders:
            e.notes.append('Notebook shows "%s" in place of a password' % e.password)
            e.password = ''
            e.flags.discard('check-password')
            fixes['placeholder password removed'] += 1
        for i, n in enumerate(e.notes):
            if n in placeholders:
                e.notes[i] = 'Notebook shows "%s" in place of a password' % n
                fixes['placeholder mark explained'] += 1
        if e.password.endswith(':'):
            e.password = e.password.rstrip(':')
            fixes['trailing colon removed'] += 1
        e.fields = [(l, v.rstrip(':').strip(), s) for l, v, s in e.fields]
        if '..' in e.username and '@' in e.username:
            e.username = re.sub(r'\.{2,}', '.', e.username)
            e.flags.discard('check-username')
            fixes['double dot in email fixed'] += 1
        if ' ' in e.username and '@' in e.username:
            e.username = e.username.replace(' ', '')
            e.flags.discard('check-username')
            fixes['space in email removed'] += 1

    merged, seen = [], {}
    for e in entries:
        k = (e.title.strip().lower(), (e.username or '').strip().lower())
        if k in seen:
            m = seen[k]
            if not m.password and e.password:
                m.password = e.password
            if not m.url and e.url:
                m.url = e.url
            have = {l.lower() for l, _, _ in m.fields}
            for f in e.fields:
                if f[0].lower() not in have:
                    m.fields.append(f)
            m.notes.extend(n for n in e.notes if n not in m.notes)
            m.flags |= e.flags
            fixes['duplicate entries merged'] += 1
            continue
        seen[k] = e
        merged.append(e)
    entries = merged

    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['name', 'url', 'username', 'password', 'notes', 'type', 'tags', 'fields'])
        for e in entries:
            tags = ['notebook'] + (['check'] if e.flags else []) + (['no-password'] if not e.password and e.category() in ('login', 'bank') else [])
            notes = '\n'.join(e.notes)
            if e.flags:
                notes = (notes + '\n' if notes else '') + 'Check the ' + ' and '.join(sorted(x.split('-')[1] for x in e.flags)) + ' against the notebook.'
            fields = '\n'.join('%s%s: %s' % (l, ' [hidden]' if s else '', v) for l, v, s in e.fields)
            w.writerow([e.title, e.url, e.username, e.password, notes, e.category(), '; '.join(tags), fields])

    def shape(s):
        return re.sub(r'[A-Z]', 'A', re.sub(r'[a-z]', 'a', re.sub(r'\d', '9', s)))

    def ukind(u):
        return 'email' if ocr.RE_EMAIL.match(u) else 'phone' if ocr.RE_PHONE.match(u) else 'id(%d)' % len(u) if u else '-'

    print('%d entries -> %s' % (len(entries), out_csv))
    print('fixes:', dict(fixes) or 'none')
    print('types:', dict(collections.Counter(e.category() for e in entries)))
    print('with password:', sum(1 for e in entries if e.password), '| with username:', sum(1 for e in entries if e.username), '| flagged:', sum(1 for e in entries if e.flags))
    for e in entries:
        print('  %-30s %-5s user=%-8s pass=%2d url=%s fields=%d notes=%d %s' % (shape(e.title), e.category(), ukind(e.username), len(e.password), 'y' if e.url else '-', len(e.fields), len(e.notes), ' '.join(sorted(e.flags))))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
