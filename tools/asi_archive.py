"""Bounded vendor download, known SDK builds and exact archive-member copying."""
import hashlib
import re
import tarfile
import urllib.parse
import urllib.request
import zipfile

MAX_DOWNLOAD = 160 * 1024 * 1024
HOSTS = frozenset(('dl.zwoastro.com', 'api.zwoastro.com', 'e.zwoastro.com'))
NESTED = re.compile(r'ASI_Camera_SDK/ASI_linux_mac_SDK_V([0-9]{1,3}\.[0-9]{1,4})\.tar\.bz2')
# The vendor replaces the archive behind one URL with each SDK release. A build is
# accepted by the exact library and licence it contains, never by the outer
# archive alone. Add the next release here after checking its files.
KNOWN_BUILDS = {
    '1.41': {'archive_sha256': 'c6d59bf2de5b807f13d60d10b035e10dff31aeaa43391c6cd4782242126775ec'},
    '1.42': {
        'license_sha256': '98ad1c18048bfdabc8463740ac36a8d8cd710bdc3102ac6c22978ec50056e5a2',
        'libraries': {
            'armv6': 'e06c4b92461202cfe5e74b9c9c18a12cf3810dd057b7642fa2ac8ae1a6f5ee36',
            'armv8': 'a8300d27135a7510cc90558daa02ae3935a3b6ee5e3ce7b073b9386f44b499a8',
            'x86': '85731ca1724c93375b6ea67fe304d70df925970a28dcf8468d6f677ac7a9bbb5',
            'x64': '6fcf45bd3a2eab6cfcdae6e740f85a6ec102d5047ade6de188a43b56ff316959',
        },
    },
}


def known_build(version, abi, library_sha256, license_sha256, archive_sha256=None):
    build = KNOWN_BUILDS.get(version)
    if not build:
        return False
    if archive_sha256 is not None and build.get('archive_sha256') == archive_sha256:
        return True
    return (build.get('libraries', {}).get(abi) == library_sha256
            and build.get('license_sha256') == license_sha256)


def nested_version(archive):
    """Name and version of the single Linux SDK archive inside the vendor ZIP."""
    with zipfile.ZipFile(archive) as outer:
        found = [(m.filename, NESTED.fullmatch(m.filename)[1]) for m in outer.infolist() if NESTED.fullmatch(m.filename)]
    if len(found) != 1:
        raise ValueError('invalid_nested_archive')
    return found[0]


def check_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('unapproved_download_host')


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination, expected=None):
    """Bounded HTTPS download from the vendor hosts; an optional pin checks the bytes."""
    check_url(url)
    if expected is not None and not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('missing_digest_pin')
    digest, size = hashlib.sha256(), 0
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirects())
    with opener.open(url, timeout=30) as response, destination.open('xb') as out:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise ValueError('download_too_large')
            digest.update(chunk)
            out.write(chunk)
    actual = digest.hexdigest()
    if expected is not None and actual != expected:
        raise ValueError('digest_mismatch')
    return {'sha256': actual, 'bytes': size}


def select_members(archive, nested_name, wanted, nested):
    """Copy only the exact requested regular members; never extract paths/links or run installers."""
    with zipfile.ZipFile(archive) as outer:
        members = [x for x in outer.infolist() if x.filename == nested_name]
        if (len(members) != 1 or members[0].file_size > MAX_DOWNLOAD or members[0].is_dir()
                or (members[0].external_attr >> 28) not in (0, 8)):
            raise ValueError('invalid_nested_archive')
        with outer.open(members[0]) as source, nested.open('xb') as dest:
            copy_bounded(source, dest, MAX_DOWNLOAD)
    with tarfile.open(nested) as inner:
        found, count, expanded = set(), 0, 0
        for member in inner:
            count += 1
            expanded += member.size
            if count > 20000 or expanded > 1024 * 1024 * 1024:
                raise ValueError('archive_limit')
            if member.name not in wanted:
                continue
            target, limit = wanted[member.name]
            if member.name in found or not member.isfile() or member.size > limit:
                raise ValueError('invalid_library_member')
            found.add(member.name)
            with inner.extractfile(member) as source, target.open('xb') as dest:
                copy_bounded(source, dest, limit)
        if found != wanted.keys():
            raise ValueError('library_member_absent')


def copy_bounded(source, dest, limit):
    size = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise ValueError('member_too_large')
        dest.write(chunk)
