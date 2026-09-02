import re
import unicodedata

SPACES = re.compile('[\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]')
EMOJI = re.compile(
    '[\U0001F000-\U0001FAFF\u2190-\u2BFF\u3030\u303D\u3297\u3299'
    '\uFE0E\uFE0F\u200D\u20E3\u00A9\u00AE]')

NICK_PATTERNS = [
    re.compile(r'"([^"]*)"'),
    re.compile('[\u201c\u201d]([^\u201c\u201d]*)[\u201c\u201d]'),
    re.compile(r"(?:(?<=\s)|(?<=^))'(.*)'(?=\s|$)"),
    re.compile(r'(?:(?<=\s)|(?<=^))\u2018(.*)\u2019(?=\s|$)'),
    re.compile(r'\(([^)]*)\)'),
    re.compile(r'\[([^\]]*)\]'),
]

def strip_emoji(s):
    return re.sub(r'\s+', ' ', EMOJI.sub('', SPACES.sub(' ', s or ''))).strip()

def split_nicknames(s):

    nicks, out = [], s
    for pat in NICK_PATTERNS:
        found = pat.findall(out)
        if found:
            nicks.extend(found)
            out = pat.sub(' ', out)
    return re.sub(r'\s+', ' ', out).strip(' ,-'), [strip_emoji(n) for n in nicks]

JUNK_ENDS = re.compile(r'^[\W\d_]+|[\W\d_]+$')

def canon(s):
    base, _ = split_nicknames(strip_emoji(s))
    base = JUNK_ENDS.sub('', base)
    return re.sub(r'\s+', ' ', base).strip().casefold()

def clean_display(s):

    return JUNK_ENDS.sub('', split_nicknames(strip_emoji(s))[0]).strip()

def nicknames(s):
    return [n.casefold() for n in split_nicknames(strip_emoji(s))[1] if n]

def signature(c):

    d = unicodedata.normalize('NFKD', c)
    d = ''.join(ch for ch in d if not unicodedata.combining(ch))
    return re.sub(r'[^0-9a-z]+', '', d.casefold())

PARTICLES = {'mc', 'mac', 'o', 'd', 'de', 'del', 'della', 'der', 'den', 'di',
             'du', 'da', 'dos', 'das', 'van', 'von', 'ter', 'ten', 'la', 'le',
             'st', 'saint', 'bin', 'ibn', 'abu', 'al', 'el'}

def name_tokens(c):

    toks, out = [signature(t) for t in c.split()], []
    for t in toks:
        if not t:
            continue
        if out and out[-1] in PARTICLES:
            out[-1] += t
        else:
            out.append(t)
    return out
