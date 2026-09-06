"""Turn OCR text of a handwritten/printed password notebook into a Strongroom import CSV.

Input: a folder of pageNN.txt files, each line "x<TAB>y<TAB>text" (from ocr.ps1).
Output: a CSV with columns name,url,username,password,notes,type,tags,fields that the
vault's Settings -> Import a file understands. Prints a masked summary only.

Usage: python ocr-notebook-to-csv.py <ocr-folder> <out.csv>
"""
import sys, os, re, glob, csv

LINE_GAP = 160          # vertical gap (px) that starts a new block
SAME_ROW = 45           # lines closer than this are one row (label left, value right)

RE_EMAIL = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
RE_EMAIL_SPACED = re.compile(r'^\S+ \S*@\S+\.\S+$')
RE_URL = re.compile(r'^(https?://|www\.)', re.I)
RE_URL_FRAG = re.compile(r'^[^\s@]+$')
RE_PHONE = re.compile(r'^\+?\d{10}$')
RE_DIGITS = re.compile(r'^[\d ]+,?$')
RE_CARD = re.compile(r'^\d{4} ?\d{4} ?\d{4} ?\d{4}$')
RE_EXPIRY = re.compile(r'^\d{2}/\d{2}$')
RE_DOMAIN = re.compile(r'^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[a-z]{2,6}$')
RE_LABEL = re.compile(r'^([A-Za-z][A-Za-z0-9 ./]{0,30}?)(?:\s*:\s*|\s+-\s+)(.*)$')
RE_LABEL_NOCOLON = re.compile(r'^(username|user ?name|user ?[il]d|userid|pwd|password|pass|mpin|pin|cvv|otp|ifsc|mobile|phone|email|mail ?[il]d)\s+(\S.*)$', re.I)

USER_LABELS = re.compile(r'^(use?r ?name|user ?[il]d|user ?aa|user|userid|login ?[il]d|mail ?[il]d|e-?mail( address| [il]d)?|login)$', re.I)
PASS_LABELS = re.compile(r'^(password|pwd|pass|passwd|passcode)$', re.I)
SECRET_WORDS = re.compile(r'pin|cvv|otp|password|pwd|tpin|mpin|secret|code|answer', re.I)
CARD_WORDS = re.compile(r'\bcard\b', re.I)
VEHICLE = re.compile(r'^(car|bike|scooter|vehicle|two ?wheeler|four ?wheeler)\b', re.I)


def clean(s):
    return re.sub(r'\s+', ' ', s).strip()


def label_of(t):
    m = RE_LABEL.match(t)
    if m and not RE_URL.match(t) and '://' not in t:
        return m.group(1).strip(), m.group(2).strip()
    m = RE_LABEL_NOCOLON.match(t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def is_header(t):
    lab = label_of(t)
    return t.endswith(':') and (lab is None or not lab[1])


def looks_like_title(t):
    if t.endswith(',') or RE_EMAIL.match(t) or RE_URL.match(t) or '://' in t or RE_DIGITS.match(t):
        return False
    if label_of(t) and not is_header(t):
        return False
    if re.match(r'^dob\b', t, re.I):
        return False
    words = t.split()
    return 1 <= len(words) <= 5 and not t[:1].isdigit() and (RE_DOMAIN.match(t) or not looks_like_password(t))


def looks_like_password(t):
    core = t.replace(' ', '')
    if t.count(' ') > 1 or not (5 <= len(core) <= 30):
        return False
    if RE_EMAIL.match(t) or RE_URL.match(t) or RE_DIGITS.match(t) or RE_DOMAIN.match(t) or ',' in t:
        return False
    return bool(re.search(r'[A-Za-z]', core) and re.search(r'\d', core)) and (' ' not in t or re.search(r'[!@#$%^&*_+=\-*.?]', core))


def is_cred_line(t):
    lab = label_of(t)
    if lab:
        return bool(USER_LABELS.match(lab[0].lower()) or PASS_LABELS.match(lab[0].lower()) or ('pass' in lab[0].lower() and looks_like_password(lab[1])))
    return bool(RE_EMAIL.match(t) or looks_like_password(t))


def load_pages(folder):
    pages = []
    for f in sorted(glob.glob(os.path.join(folder, 'page*.txt'))):
        rows = []
        for line in open(f, encoding='utf-8'):
            parts = line.rstrip('\n').split('\t', 2)
            if len(parts) == 3 and parts[2].strip():
                rows.append((int(parts[0]), int(parts[1]), clean(parts[2])))
        rows.sort(key=lambda r: (r[1], r[0]))
        merged = []
        for x, y, t in rows:
            if merged and abs(merged[-1][1] - y) < SAME_ROW:
                px, py, pt = merged[-1]
                merged[-1] = (px, py, clean(pt + ' ' + t)) if x > px else (x, py, clean(t + ' ' + pt))
            else:
                merged.append((x, y, t))
        pages.append((os.path.basename(f), merged))
    return pages


def split_blocks(pages):
    for name, rows in pages:
        block, last_y, first = [], None, True
        for x, y, t in rows:
            if block and last_y is not None and y - last_y > LINE_GAP:
                yield name, first, block
                block, first = [], False
            block.append(t)
            last_y = y
        if block:
            yield name, first, block


def join_url_fragments(lines):
    out = []
    for t in lines:
        if out and ('://' in out[-1] or RE_URL.match(out[-1])) and RE_URL_FRAG.match(t) and not RE_EMAIL.match(t) \
                and (re.search(r'[/=&_%?#-]', t) or out[-1].endswith(('-', '/', '=', '?', '&'))):
            out[-1] = out[-1] + t
        else:
            out.append(t)
    return out


class Entry:
    def __init__(self, title, page):
        self.title = title
        self.page = page
        self.username = ''
        self.password = ''
        self.url = ''
        self.fields = []
        self.notes = []
        self.flags = set()
        self.kind = ''
        self.numbers = 0
        self.pw_labeled = False

    def add_field(self, label, value, secret=None):
        label = clean(label).rstrip(':').strip()
        value = clean(value).rstrip(',').strip()
        if not value:
            return
        if secret is None:
            secret = bool(SECRET_WORDS.search(label))
        n = sum(1 for f in self.fields if f[0].lower() == label.lower() or f[0].lower().startswith(label.lower() + ' ('))
        if n:
            label = f"{label} ({n + 1})"
        self.fields.append((label, value, secret))

    def set_password(self, value, labeled=False):
        if self.password and labeled and not self.pw_labeled:
            if not self.username:
                self.username = self.password
            else:
                self.notes.append(self.password)
            self.password = value
        elif self.password and not labeled and self.pw_labeled:
            self.notes.append(value)
        elif self.password:
            # keep the more password-looking one as the password, demote the other to an ID
            old_sym = bool(re.search(r'[!@#$%^&*_+=\-*.?]', self.password))
            new_sym = bool(re.search(r'[!@#$%^&*_+=\-*.?]', value))
            if not self.username and (new_sym or not old_sym):
                self.username, self.password = self.password, value
            elif new_sym and not old_sym:
                self.add_field('ID', self.password, False)
                self.password = value
            else:
                self.add_field('Password (2)', value, True)
        else:
            self.password = value
        self.pw_labeled = self.pw_labeled or labeled
        if ' ' in self.password:
            self.flags.add('check-password')

    def is_card(self):
        return bool(CARD_WORDS.search(self.title or '')) or any(re.search(r'^(debit |credit )?card (no|number)|cvv|expiry', l, re.I) for l, _, _ in self.fields)

    def category(self):
        if self.kind:
            return self.kind
        labels = ' '.join(l for l, _, _ in self.fields).lower() + ' ' + (self.title or '').lower()
        bankish = re.search(r'account no|ifsc|customer id|mpin|a/c|savings|netbanking', labels)
        if self.is_card() and not bankish:
            return 'card'
        if bankish:
            return 'bank'
        if 'wifi' in labels or 'wi-fi' in labels:
            return 'wifi'
        if re.search(r'aadhaar|passport|\bpan\b|licen|chassis|engine|vehicle|bike|car\b|voter|epic|application no|dob', labels):
            return 'id'
        if not self.username and not self.password:
            return 'note'
        return 'login'


def absorb(entry, t):
    if RE_URL.match(t) or '://' in t:
        if entry.url:
            entry.add_field('Link', t, False)
        else:
            entry.url = t
        return
    lab = label_of(t)
    if lab and lab[1]:
        label, value = lab
        low = label.lower()
        if USER_LABELS.match(low):
            if entry.username:
                entry.add_field(label, value, False)
            else:
                entry.username = value
        elif PASS_LABELS.match(low):
            entry.set_password(value, True)
        elif low == 'wifi password':
            entry.kind = 'wifi'
            entry.set_password(value, True)
        elif 'pass' in low and not entry.password and looks_like_password(value):
            entry.set_password(value, True)
        elif re.match(r'^(phone|mobile|mobile no\.?|phone no\.?|mob|mobile number)$', low):
            entry.add_field('Mobile', value, False)
        elif re.match(r'^(name|account holder name)$', low) or re.match(r'^account \w+ name$', low):
            entry.add_field('Name', value, False)
        elif re.match(r'^[a-z]{2,3}$', low) and re.match(r'^\d{3}$', value):
            entry.add_field('CVV', value, True)
        else:
            entry.add_field(label, value)
        return
    if lab and not lab[1]:
        entry.notes.append(t.rstrip(':').strip())
        return
    if RE_EMAIL.match(t) or RE_EMAIL_SPACED.match(t):
        if RE_EMAIL_SPACED.match(t) and not RE_EMAIL.match(t):
            entry.flags.add('check-username')
        if '..' in t:
            entry.flags.add('check-username')
        if entry.username:
            entry.add_field('Email', t, False)
        else:
            entry.username = t
        return
    if RE_CARD.match(t) or re.match(r'^\d{16}$', t):
        entry.add_field('Card number', t, False)
        return
    if RE_EXPIRY.match(t):
        entry.add_field('Expiry', t, False)
        return
    if RE_PHONE.match(t):
        if not entry.username and not entry.password:
            entry.username = t
        else:
            entry.add_field('Mobile', t, False)
        return
    if RE_DIGITS.match(t):
        entry.numbers += 1
        if entry.numbers == 1 and not entry.username:
            entry.add_field('Number', t.rstrip(','), False)
        else:
            entry.notes.append(t.rstrip(','))
        return
    if re.match(r'^otp( \w+)?$', t, re.I):
        entry.add_field('Login', 'via OTP', False)
        return
    if looks_like_password(t):
        entry.set_password(t)
        return
    entry.notes.append(t)


def display_title(e):
    if e.title:
        return e.title
    host = re.sub(r'^https?://(www\.)?', '', e.url, flags=re.I).split('/')[0] if e.url else ''
    return host or e.username or 'Untitled'


def build_entries(pages):
    entries = []
    for page, first_on_page, lines in split_blocks(pages):
        lines = join_url_fragments(lines)
        head = lines[0]
        prev = entries[-1] if entries else None
        target = None
        if re.match(r'^dob\b', head, re.I):
            if prev and prev.title == 'Dates of birth':
                target = prev
            else:
                target = Entry('Dates of birth', page)
                target.kind = 'note'
                entries.append(target)
            target.notes.extend(lines)
            continue
        if is_header(head):
            if CARD_WORDS.search(head) and prev:
                target = Entry(f"{prev.title} card", page)
                entries.append(target)
            elif first_on_page or not prev:
                target = Entry(head.rstrip(':').strip(), page)
                entries.append(target)
            else:
                prev.notes.append(head.rstrip(':').strip())
                target = prev
            lines = lines[1:]
        elif looks_like_title(head) or not prev:
            target = Entry(head.rstrip(':').strip(), page)
            entries.append(target)
            lines = lines[1:]
        elif RE_URL.match(head) or '://' in head:
            if any(is_cred_line(l) for l in lines[1:]):
                target = Entry('', page)
                entries.append(target)
            else:
                target = prev
        elif RE_EMAIL.match(head) and (prev.username or prev.password):
            target = Entry(head, page)
            entries.append(target)
        else:
            lab = label_of(head)
            if lab and VEHICLE.match(lab[0]):
                target = Entry(re.sub(r'\s*(no|number)\.?$', '', lab[0], flags=re.I).strip().title(), page)
                target.kind = 'id'
                entries.append(target)
            elif prev.password and any(label_of(l) and PASS_LABELS.match(label_of(l)[0].lower()) for l in lines):
                target = Entry(f"{display_title(prev)} (2)", page)
                entries.append(target)
            else:
                target = prev
        for t in lines:
            if is_header(t) and CARD_WORDS.search(t) and t is not head:
                target = Entry(f"{target.title} card", page)
                entries.append(target)
                continue
            absorb(target, t)
    return entries


def finish(e):
    if not e.title:
        host = re.sub(r'^https?://(www\.)?', '', e.url, flags=re.I).split('/')[0] if e.url else ''
        e.title = host or (e.username or 'Untitled')
    cat = e.category()
    if cat == 'bank' and not e.username:
        for i, (l, v, s) in enumerate(e.fields):
            if re.match(r'customer', l, re.I):
                e.username = v
                del e.fields[i]
                break
    if cat == 'card':
        if not e.username:
            for i, (l, v, s) in enumerate(e.fields):
                if re.match(r'^(debit |credit )?card (no|number)', l, re.I):
                    e.username = v
                    del e.fields[i]
                    break
        if not e.password:
            for i, (l, v, s) in enumerate(e.fields):
                if re.match(r'^(atm )?pin$', l, re.I):
                    e.password = v
                    del e.fields[i]
                    break
    if e.password and (' ' in e.password or e.password.endswith(':') or len(e.password) < 6):
        e.flags.add('check-password')
    if '..' in e.username or (' ' in e.username and cat != 'card'):
        e.flags.add('check-username')


def empty(e):
    return not (e.username or e.password or e.url or e.fields or e.notes)


def main(folder, out_csv):
    pages = load_pages(folder)
    entries = [e for e in build_entries(pages) if not empty(e)]
    for e in entries:
        finish(e)
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['name', 'url', 'username', 'password', 'notes', 'type', 'tags', 'fields'])
        for e in entries:
            tags = ['notebook', 'check'] if e.flags else ['notebook']
            notes = '\n'.join(e.notes)
            if e.flags:
                notes = (notes + '\n' if notes else '') + 'OCR import: verify the ' + ' and '.join(sorted(f.split('-')[1] for f in e.flags)) + ' against the notebook (spaces, 0/O, 1/l/I).'
            notes = (notes + '\n' if notes else '') + f'Notebook {e.page.replace(".txt", "")}'
            fields = '\n'.join(f"{l}{' [hidden]' if s else ''}: {v}" for l, v, s in e.fields)
            w.writerow([e.title, e.url, e.username, e.password, notes, e.category(), '; '.join(tags), fields])

    def shape(s):
        return re.sub(r'[A-Z]', 'A', re.sub(r'[a-z]', 'a', re.sub(r'\d', '9', s)))

    def ukind(u):
        return 'email' if RE_EMAIL.match(u) else 'phone' if RE_PHONE.match(u) else f'id({len(u)})' if u else '-'

    print(f'{len(entries)} entries -> {out_csv}')
    for e in entries:
        print(f"  [{e.page[4:6]}] {shape(e.title):<26} {e.category():<5} user={ukind(e.username):<8} pass={len(e.password):>2} url={'y' if e.url else '-'} fields={len(e.fields)} notes={len(e.notes)} {' '.join(sorted(e.flags))}")


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
