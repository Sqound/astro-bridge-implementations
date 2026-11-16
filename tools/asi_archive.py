"""Bounded, pinned vendor download and exact archive-member copying."""
import hashlib
import re
import tarfile
import urllib.parse
import urllib.request
import zipfile

MAX_DOWNLOAD = 160 * 1024 * 1024
HOSTS = frozenset(('dl.zwoastro.com', 'api.zwoastro.com', 'e.zwoastro.com'))


def check_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('unapproved_download_host')


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination, expected):
    check_url(url)
    if not re.fullmatch('[0-9a-f]{64}', expected):
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
    if actual != expected:
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
