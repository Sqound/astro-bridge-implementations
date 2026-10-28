#!/usr/bin/env python3
"""Download and verify Astro Bridge, then flash removable media with Imager CLI.

The graphical installer manages image and drive selection. The official pinned
Raspberry Pi Imager engine performs writing and read-back verification. The OS
requests administrator authorization; credentials never enter this application.
"""
import argparse
import platform
import plistlib
import re
import shlex
import tempfile
import time
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import urllib.request
import urllib.error

# Packaged builds include a CA bundle instead of depending on a Python install.
try:
    import certifi
except ImportError:
    certifi = None

REPO = 'Sqound/astro-bridge-implementations'
IMAGE_PLATFORMS = {
    'alpine-standard': ('alpine-armhf-standard', 'ALPINE-STANDARD-SHA256SUMS'),
    'raspios': ('armhf', 'SHA256SUMS'),
}
CHECKSUM_LIMIT = 65536


def external_options(command, options):
    # macOS GUI processes must spawn native tools without forking Cocoa state
    # from a background thread. Absolute paths + close_fds=False use posix_spawn.
    if sys.platform == 'darwin':
        command = [shutil.which(command[0]) or command[0], *command[1:]]
        options['close_fds'] = False
    return command, options


def command_output(command, **options):
    command, options = external_options(command, options)
    return subprocess.check_output(command, **options)


def run_command(command, **options):
    command, options = external_options(command, options)
    return subprocess.run(command, **options)


def start_command(command, **options):
    command, options = external_options(command, options)
    return subprocess.Popen(command, **options)


def network_context():
    return ssl.create_default_context(cafile=certifi.where() if certifi else None)


def imager():
    candidates = [shutil.which('rpi-imager'),
                  '/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager']
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        if os.environ.get(variable):
            for relative in ('Raspberry Pi Ltd/Imager/rpi-imager.exe',
                             'Raspberry Pi Imager/rpi-imager.exe',
                             'Programs/Raspberry Pi Imager/rpi-imager.exe'):
                candidates.append(str(Path(os.environ[variable]) / relative))
    return next((str(p) for p in candidates if p and Path(p).is_file()), None)


def verified(path, checksum):
    if not isinstance(checksum,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',checksum):
        raise ValueError('A complete 64-digit SHA-256 checksum is required.')
    if not path.is_file() or not (path.name.endswith('.img') or path.name.endswith('.img.xz')):
        raise ValueError('Select a .img or .img.xz image.')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != checksum.lower():
        raise ValueError('Image checksum mismatch. Nothing has been flashed.')
    return path.resolve()


def checksum_for(data, name):
    if isinstance(data,bytes):data=data.decode('utf-8')
    if len(data.encode('utf-8'))>CHECKSUM_LIMIT:raise ValueError('Checksum file is too large.')
    matches=[parts[0] for line in data.splitlines() if len(parts:=line.split())==2 and parts[1].lstrip('*')==name]
    if len(matches)!=1 or not re.fullmatch(r'[0-9a-fA-F]{64}',matches[0]):
        raise ValueError('The image needs one unambiguous valid SHA-256 entry in the selected checksum file.')
    return matches[0].lower()


def checksum_file(path, name):
    with path.open('rb') as stream:return checksum_for(stream.read(CHECKSUM_LIMIT+1),name)


def latest(directory, image_platform='alpine-standard'):
    if image_platform not in IMAGE_PLATFORMS:raise ValueError('Choose Alpine standard or Raspberry Pi OS explicitly.')
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases/latest', headers={'User-Agent': 'Astro-Bridge-Flasher'})
    try:
        with urllib.request.urlopen(request, timeout=30, context=network_context()) as response:
            data=response.read(1024*1024+1)
        if len(data)>1024*1024:raise ValueError('Release metadata is too large.')
        release=json.loads(data)
    except urllib.error.HTTPError as error:
        if error.code in (401,403,404,429):
            error.close()
            raise ValueError('No accessible stable release. The repository may be private, unreleased or rate limited. Choose a reviewed local image and its checksum file; no account token is needed by this launcher.') from None
        raise
    if not isinstance(release,dict) or release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('No stable release is available. Choose a reviewed local image and checksum file.')
    tag=release.get('tag_name','')
    if not isinstance(tag,str) or not re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',tag):raise ValueError('Unsupported stable release version.')
    entries=release.get('assets')
    if not isinstance(entries,list) or len(entries)>1000:raise ValueError('Invalid release assets.')
    assets={}
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('name'),str) or entry['name']in assets:raise ValueError('Ambiguous release assets.')
        assets[entry['name']]=entry.get('browser_download_url')
    suffix,sums=IMAGE_PLATFORMS[image_platform]
    name=f"astro-bridge-{tag.removeprefix('v')}-{suffix}.img.xz"
    prefix=f"https://github.com/{REPO}/releases/download/{tag}/"
    for asset in (name,sums):
        if assets.get(asset)!=prefix+asset:
            raise ValueError('The latest stable release has no complete image for the selected platform. Choose another platform explicitly or a reviewed local image; no fallback was downloaded.')
    with urllib.request.urlopen(assets[sums], timeout=30, context=network_context()) as response:
        checksum=checksum_for(response.read(CHECKSUM_LIMIT+1),name)
    directory.mkdir(parents=True, exist_ok=True)
    image=directory/name
    if not image.exists():
        partial=image.with_suffix('.partial')
        try:
            with urllib.request.urlopen(assets[name], timeout=60, context=network_context()) as response, partial.open('wb') as output:
                shutil.copyfileobj(response,output)
            partial.rename(image)
            verified(image,checksum)
        except Exception:
            partial.unlink(missing_ok=True);image.unlink(missing_ok=True)
            raise
    return verified(image,checksum)


# Pinned official Raspberry Pi Imager assets. Updates require a reviewed hash change.
IMAGER_VERSION = 'v2.0.12'
IMAGER_ASSETS = {
    'darwin': ('rpi-imager-v2.0.12.dmg', '15b1a94c712c0032750855653c2bf129dceb401329fbce0a83d7cf05dc58a421'),
    'win32': ('imager-v2.0.12.exe', '98bff90afe23339d0768915f6f68d35ac254740ec1eac2082ebeaa9ba41bc8e3'),
    'linux-x86_64': ('Raspberry_Pi_Imager-v2.0.12-cli-x86_64.AppImage', '8a2fa9c0fe6c61f57d5826a7419131b18f33226c681c740ebc30a713928d05e3'),
    'linux-aarch64': ('Raspberry_Pi_Imager-v2.0.12-cli-aarch64.AppImage', 'b87c82db68fbe8f4fd7c266af8a5d7b2aad3d0ddc2e4243ebd6ea77bcb65573c'),
    'linux-armv7l': ('Raspberry_Pi_Imager-v2.0.12-cli-armhf.AppImage', 'dcf25bcbd83256e2b568d2fde8f994629937ebe254cfbf8ec7ad718491cff3bc'),
}


def cache_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches/Astro-Bridge'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Astro-Bridge/Cache'
    return Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'astro-bridge'


def checked_download(url, destination, checksum, notify=lambda text: None):
    """Only make a file executable after checking the pinned vendor hash."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    def matches(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == checksum
    if destination.is_file() and matches(destination):
        return destination
    partial = destination.with_suffix(destination.suffix + '.partial')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Astro-Bridge-Flasher'})
        with urllib.request.urlopen(request, timeout=60, context=network_context()) as response, partial.open('wb') as output:
            size = int(response.headers.get('Content-Length', 0))
            done, previous = 0, -1
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                done += len(chunk)
                percent = int(done * 100 / size) if size else 0
                if percent != previous:
                    notify(f'Downloading Raspberry Pi Imager: {percent} %' if size else 'Downloading Raspberry Pi Imager…')
                    previous = percent
        if not matches(partial):
            raise ValueError('Raspberry Pi Imager checksum mismatch. Download discarded.')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def run_json(command):
    return json.loads(command_output(command, text=True, timeout=15))


def disks():
    """Return whole removable drives only; never offer internal/system disks."""
    result = []
    if sys.platform == 'darwin':
        root = plistlib.loads(command_output(['diskutil', 'info', '-plist', '/'], timeout=8))
        protected = {root.get('ParentWholeDisk')}
        protected.update(re.sub(r's[0-9]+$', '', store['APFSPhysicalStore']) for store in root.get('APFSPhysicalStores', []))
        # Query hardware metadata without opening mounted volumes. diskutil info
        # on external volumes can wait for macOS privacy authorization.
        devices = plistlib.loads(command_output(['ioreg', '-a', '-r', '-l', '-c', 'IOBlockStorageDevice'], timeout=8))
        def media_nodes(node):
            yield node
            for child in node.get('IORegistryEntryChildren', []):
                yield from media_nodes(child)
        seen = set()
        for hardware in devices:
            interconnect = hardware.get('Protocol Characteristics', {}).get('Physical Interconnect')
            if not interconnect or interconnect == 'Virtual Interface':
                continue
            for info in media_nodes(hardware):
                name = info.get('BSD Name', '')
                if (name in seen or name in protected or not re.fullmatch(r'disk[0-9]+', name)
                        or not info.get('Whole') or not info.get('Removable')
                        or not info.get('Writable') or int(info.get('Size', 0)) <= 0):
                    continue
                seen.add(name)
                result.append(dict(device='/dev/' + name,
                                   name=info.get('IORegistryEntryName', 'SD card').removesuffix(' Media'),
                                   size=int(info['Size']), identity=str(info['IORegistryEntryID'])))
    elif sys.platform == 'win32':
        script = "$removable = @(Get-CimInstance Win32_DiskDrive | Where-Object { $_.MediaType -like '*Removable*' } | Select-Object -ExpandProperty Index); Get-Disk | Where-Object { ($_.BusType -in @('SD','MMC') -or ($_.BusType -eq 'USB' -and $_.Number -in $removable)) -and -not $_.IsBoot -and -not $_.IsSystem -and -not $_.IsOffline -and -not $_.IsReadOnly } | Select-Object Number,FriendlyName,Size,UniqueId | ConvertTo-Json -Compress"
        data = command_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], text=True).strip()
        rows = json.loads(data) if data else []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            result.append(dict(device='\\\\.\\PhysicalDrive' + str(row['Number']), name=row['FriendlyName'],
                               size=int(row['Size']), identity=row['UniqueId']))
    else:
        listing = run_json(['lsblk', '--json', '--bytes', '--paths', '--output', 'NAME,MODEL,SIZE,TYPE,RM,HOTPLUG,RO,SERIAL,MOUNTPOINTS'])
        def critical(node):
            return any(p == '/' or (p and (p == '/boot' or p.startswith('/boot/'))) for p in node.get('mountpoints', [])) or any(critical(c) for c in node.get('children', []))
        for row in listing['blockdevices']:
            if row['type'] != 'disk' or row.get('ro') or not row.get('rm') or critical(row):
                continue
            result.append(dict(device=row['name'], name=(row.get('model') or 'Removable drive').strip(),
                               size=int(row['size']), identity=row.get('serial') or row['name']))
    return result


def isolated_disks():
    """Keep native drive queries out of the macOS Tk process entirely."""
    command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
    output = command_output([*command, '--list-disks'], text=True, timeout=40,
                            env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
    return json.loads(output)


def validate_target(selected, isolated=False):
    current = next((disk for disk in (isolated_disks() if isolated else disks()) if disk == selected), None)
    if not current:
        raise ValueError('The selected drive changed or disappeared. Refresh and select it again.')
    return current


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def elevated(command):
    """Ask the OS for authorization; never request or store a password ourselves."""
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    if sys.platform == 'darwin':
        shell = 'env PYINSTALLER_RESET_ENVIRONMENT=1 ' + shlex.join(command)
        return start_command(['osascript', '-e', 'do shell script ' + json.dumps(shell, ensure_ascii=False) + ' with administrator privileges'],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
    if sys.platform == 'win32':
        arguments = subprocess.list2cmdline(command[1:])
        script = ("$ErrorActionPreference='Stop'; $env:PYINSTALLER_RESET_ENVIRONMENT='1'; "
                  "$p=Start-Process -FilePath " + ps_literal(command[0]) +
                  ' -ArgumentList ' + ps_literal(arguments) + ' -Verb RunAs -Wait -PassThru; exit $p.ExitCode')
        return start_command(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
    if os.geteuid() == 0:
        return start_command(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
    if not shutil.which('pkexec'):
        raise ValueError('Install PolicyKit (pkexec) and start its desktop authentication agent, then retry.')
    return start_command(['pkexec', 'env', 'PYINSTALLER_RESET_ENVIRONMENT=1', *command],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)


def ensure_imager(notify=lambda text: None):
    """Download the official engine without requiring a manual installation."""
    key = 'linux-' + platform.machine().lower() if sys.platform.startswith('linux') else sys.platform
    if key not in IMAGER_ASSETS:
        raise ValueError('This platform has no bundled Imager download.')
    name, checksum = IMAGER_ASSETS[key]
    directory = cache_directory() / IMAGER_VERSION
    asset = checked_download(f'https://github.com/raspberrypi/rpi-imager/releases/download/{IMAGER_VERSION}/{name}',
                             directory / name, checksum, notify)
    notify('Preparing Raspberry Pi Imager…')
    if sys.platform == 'darwin':
        target = directory / 'Raspberry Pi Imager.app'
        if not target.exists():
            mounted = plistlib.loads(command_output(['hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', str(asset)]))
            mount = next(e['mount-point'] for e in mounted['system-entities'] if 'mount-point' in e)
            try:
                source = next(Path(mount).glob('*.app'))
                run_command(['ditto', str(source), str(target)], check=True)
            finally:
                run_command(['hdiutil', 'detach', mount], check=True)
        run_command(['codesign', '--verify', '--deep', '--strict', str(target)], check=True)
        return str(target / 'Contents/MacOS/rpi-imager')
    if sys.platform == 'win32':
        existing = imager()
        if not existing:
            notify('Installing Raspberry Pi Imager. Approve the Windows authorization prompt…')
            process = elevated([str(asset), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-'])
            output = process.communicate()[0]
            if process.returncode:
                raise ValueError('Imager installation was cancelled or failed: ' + output[-1000:])
            existing = imager()
        if not existing:
            raise ValueError('Imager installation finished, but its executable was not found.')
        return existing
    target = directory / 'squashfs-root/AppRun'
    if not target.exists():
        asset.chmod(0o755)
        run_command([str(asset), '--appimage-extract'], cwd=directory, check=True, stdout=subprocess.DEVNULL)
    return str(target)


def check_source_drive(image, selected):
    """The image must survive erasing the destination drive."""
    if sys.platform == 'win32':
        letter = image.drive.rstrip(':')
        script = "Get-Partition -DriveLetter " + ps_literal(letter) + " -ErrorAction Stop | Select-Object -ExpandProperty DiskNumber"
        number = command_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], text=True).strip()
        if selected['device'] == '\\\\.\\PhysicalDrive' + number:
            raise ValueError('Move the image off the destination card before writing.')
    else:
        source = command_output(['df', '-P', str(image)], text=True).splitlines()[-1].split()[0]
        target = selected['device']
        if sys.platform == 'darwin':
            info = plistlib.loads(command_output(['diskutil', 'info', '-plist', source]))
            sources = [source] + ['/dev/' + store['APFSPhysicalStore'] for store in info.get('APFSPhysicalStores', [])]
        else:
            sources = [source]
        if any(part == target or re.fullmatch(re.escape(target) + r'(?:s|p)?[0-9]+', part) for part in sources):
            raise ValueError('Move the image off the destination card before writing.')


def engine_command(executable, image, device):
    # The image is hashed before invoking Imager. Its own read-back verification
    # remains enabled. --sha256 is omitted: compressed/raw hash semantics vary.
    flags = [] if sys.platform.startswith('linux') else ['--cli']
    return [executable, *flags, str(image), device]


def start_flash_worker(command):
    if sys.platform == 'darwin':
        # Imager's MacFile::authOpen requests the disk right in the user's
        # graphical session. An outer root session can make AuthorizationCreate
        # fail; Imager labels every such failure as "cancelled by user".
        return start_command(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
    return elevated(command)


def flash_worker(job_path):
    """Privileged, non-GUI worker: recheck the target after authorization."""
    job = json.loads(Path(job_path).read_text())
    log = Path(job_path).with_suffix('.log')
    with log.open('a', encoding='utf-8') as output:
        def report(message):
            output.write(json.dumps(message) + '\n')
            output.flush()
        try:
            report('Checking the image and selected drive…')
            image = verified(Path(job['image']), job['sha256'])
            validate_target(job['disk'])
            check_source_drive(image, job['disk'])
            check_source_drive(Path(job['engine']), job['disk'])
            check_source_drive(Path(sys.executable), job['disk'])
            report('Writing the card. Keep the reader connected…')
            process = start_command(engine_command(job['engine'], image, job['disk']['device']),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       errors='replace', env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
            tail = []
            for line in process.stdout:
                clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line).strip()
                if clean:
                    tail = (tail + [clean])[-12:]
                    report(clean)
                    if 'Authorization cancelled by user' in clean or 'Authorization failed' in clean:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise ValueError('macOS did not grant access to the card. This can be an authorization failure, not necessarily a cancellation. No write was started.')
            if process.wait():
                raise ValueError('\n'.join(tail) or 'Imager failed.')
            report({'success': True})
            return 0
        except Exception as error:
            report({'error': str(error)})
            return 1


def stage_image(image, checksum, notify=lambda text: None):
    """Give the privileged engine a verified source outside protected folders.

    macOS administrator authorization does not grant TCC access to Documents or
    Downloads. Copy while still running as the user who selected the image.
    """
    if not checksum or not re.fullmatch(r'[a-fA-F0-9]{64}', checksum):
        raise ValueError('A previously verified image checksum is required.')
    directory = cache_directory() / 'images'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (checksum.lower() + '-' + image.name)
    if destination.is_file():
        try:
            return verified(destination, checksum)
        except ValueError:
            pass  # A damaged cache entry must be replaced, never flashed.
    notify('Preparing a verified local copy for the writing engine…')
    with tempfile.TemporaryDirectory(prefix='prepare-', dir=directory) as temporary:
        staged = Path(temporary) / destination.name
        with image.open('rb') as source, staged.open('wb') as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        verified(staged, checksum)
        staged.replace(destination)
    return destination.resolve()


def write_card(image, selected, notify=lambda text: None, checksum=None):
    """Prepare the engine before the privileged worker revalidates the target."""
    validate_target(selected, isolated=True)
    check_source_drive(image, selected)
    executable = ensure_imager(notify)
    validate_target(selected, isolated=True)
    notify('Checking image integrity before writing…')
    if not checksum:
        raise ValueError('A previously verified image checksum is required.')
    image = stage_image(image, checksum, notify)
    check_source_drive(image, selected)
    with tempfile.TemporaryDirectory(prefix='astro-bridge-flash-') as directory:
        job_path = Path(directory) / 'job.json'
        job_path.write_text(json.dumps(dict(image=str(image), sha256=checksum, disk=selected, engine=executable)))
        command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
        notify('Imager will request permission to access the selected card…' if sys.platform == 'darwin'
               else 'Waiting for system authorization…')
        process = start_flash_worker([*command, '--flash-worker', str(job_path)])
        log_path = job_path.with_suffix('.log')
        offset = 0
        outcome = None
        while True:
            if log_path.exists():
                with log_path.open(encoding='utf-8') as stream:
                    stream.seek(offset)
                    while line := stream.readline():
                        if not line.endswith('\n'):
                            break
                        message = json.loads(line)
                        if isinstance(message, dict):
                            outcome = message
                        else:
                            notify(message)
                        offset = stream.tell()
            if process.poll() is not None:
                # Read the final worker message on the next pass, after process exit.
                if log_path.exists() and log_path.stat().st_size > offset:
                    continue
                break
            time.sleep(0.15)
        detail = process.communicate()[0]
        if process.returncode or not outcome or not outcome.get('success'):
            raise ValueError((outcome or {}).get('error') or 'Authorization cancelled or writing failed. ' + detail[-1000:])
    return image



def gui(initial_image=None, image_platform="alpine-standard"):
    """One installer window; elevated work never runs on the Tk event thread."""
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    window = tk.Tk()
    window.title("Astro Bridge · SD installer")
    window.geometry("820x850")
    window.minsize(740, 850)
    # Shared Astro Sync design tokens; clam keeps controls consistent on macOS.
    colors = dict(background="#0a0d0b", panel="#151916", border="#2c342f",
                  text="#eef0ee", muted="#9ca5a3", gold="#e0a85e", active="#342918")
    window.configure(background=colors["background"])
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure(".", font=("Avenir Next", 12), background=colors["background"],
                    foreground=colors["text"])
    style.configure("TFrame", background=colors["background"])
    style.configure("Panel.TFrame", background=colors["panel"],
                    bordercolor=colors["border"], relief="solid", borderwidth=1)
    style.configure("TLabel", background=colors["background"], foreground=colors["text"])
    style.configure("Muted.TLabel", foreground=colors["muted"])
    style.configure("Panel.TLabel", background=colors["panel"])
    style.configure("Status.TLabel", background=colors["panel"], foreground=colors["gold"])
    style.configure("TButton", background=colors["panel"], foreground=colors["text"],
                    bordercolor=colors["border"], padding=(18, 12), focusthickness=2,
                    focuscolor=colors["gold"])
    style.map("TButton", background=[("active", colors["active"])],
              foreground=[("disabled", colors["muted"])])
    style.configure("Gold.TButton", background=colors["gold"], foreground=colors["background"])
    style.map("Gold.TButton", background=[("disabled", colors["border"]), ("active", "#ffe0ae")],
              foreground=[("disabled", colors["muted"])])
    style.configure("TProgressbar", background=colors["gold"], troughcolor=colors["panel"],
                    bordercolor=colors["border"], lightcolor=colors["gold"], darkcolor=colors["gold"])
    frame = ttk.Frame(window, padding=32)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="✧  Astro Bridge", foreground=colors["gold"],
              font=("Avenir Next", 24, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Prepare your Raspberry Pi", font=("Avenir Next", 18, "bold")).pack(anchor="w", pady=(16, 6))
    ttk.Label(frame, text="Choose an image and a card. Astro Bridge handles the rest.",
              style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(0, 22))
    panel = ttk.Frame(frame, padding=22, style="Panel.TFrame")
    panel.pack(fill="x")
    ttk.Label(panel, text="1   Choose your system image", style="Panel.TLabel",
              font=("Avenir Next", 14, "bold")).pack(anchor="w", pady=(0, 14))
    image_name = tk.StringVar(value=initial_image[0].name if initial_image else "No image selected yet.")
    ttk.Label(panel, textvariable=image_name, style="Panel.TLabel", wraplength=650).pack(anchor="w", pady=(0, 8))
    status = tk.StringVar(value="Choose an image to get started.")
    events = queue.Queue()
    ready = [initial_image]
    drives = []
    busy = [False]

    def show_progress(text):
        events.put(("progress", text))

    def selected_index():
        selection = drive_choice.curselection()
        return selection[0] if selection else -1

    def set_controls(active):
        download.configure(state="normal" if active else "disabled")
        local.configure(state="normal" if active else "disabled")
        platform_choice.configure(state="readonly" if active else "disabled")
        refresh.configure(state="normal" if active else "disabled")
        drive_choice.configure(state="normal" if active else "disabled")
        launch.configure(state="normal" if active and ready[0] and selected_index() >= 0 else "disabled")

    def begin(operation, kind="image"):
        busy[0] = True
        if kind == "image":
            ready[0] = None
            image_name.set("Checking the selected image…")
        set_controls(False)
        progress.configure(mode="indeterminate")
        progress.start(12)
        status.set("Preparing and checking the image…" if kind == "image" else "Preparing…")
        def run():
            try:
                result = operation()
                if kind == "image":
                    with result.open('rb') as stream:
                        result = (result, hashlib.file_digest(stream, 'sha256').hexdigest())
                events.put((kind, result))
            except Exception as error:
                events.put(("error", str(error)))
        threading.Thread(target=run, daemon=True).start()

    def choose():
        selected = filedialog.askopenfilename(filetypes=[("SD image", "*.img *.img.xz")])
        if not selected:
            return
        path = Path(selected)
        checksum=None
        for name in ('ALPINE-STANDARD-SHA256SUMS','SHA256SUMS','ALPINE-SHA256SUMS'):
            try:checksum=checksum_file(path.parent/name,path.name);break
            except (OSError,ValueError):continue
        if checksum is None:
            choice=filedialog.askopenfilename(title="Select the matching SHA256SUMS file")
            if not choice:return
            try:checksum=checksum_file(Path(choice),path.name)
            except (OSError,ValueError) as error:
                messagebox.showerror("Checksum missing or invalid",str(error));return
        begin(lambda: verified(path, checksum))

    def refresh_disks():
        begin(isolated_disks, "disks")

    def flash():
        index = selected_index()
        if not ready[0] or index < 0 or index >= len(drives):
            return
        selected = dict(drives[index])
        label = drive_choice.get(index)
        if not messagebox.askyesno("Erase this card?",
                "All data on this drive will be erased:\n\n" + label +
                "\n\nImage: " + ready[0][0].name +
                "\n\nContinue? Keep the reader connected until verification finishes.", icon="warning", default="no"):
            return
        begin(lambda: write_card(ready[0][0], selected, show_progress, ready[0][1]), "written")

    platform_labels={'Alpine standard (experimental)':'alpine-standard','Raspberry Pi OS (legacy images)':'raspios'}
    platform_value=tk.StringVar(value=next(label for label,value in platform_labels.items() if value==image_platform))
    platform_choice=ttk.Combobox(panel,textvariable=platform_value,values=list(platform_labels),state="readonly")
    platform_choice.pack(fill="x",pady=(0,6))
    def download_selected():
        selected=platform_labels[platform_value.get()]
        begin(lambda:latest(Path.home()/"Downloads/Astro-Bridge",selected))
    download = ttk.Button(panel, text="Download latest stable for selected platform", command=download_selected)
    download.pack(fill="x", pady=4)
    local = ttk.Button(panel, text="Choose a downloaded image…", command=choose)
    local.pack(fill="x", pady=4)
    ttk.Label(panel, textvariable=status, style="Status.TLabel", wraplength=650).pack(anchor="w", pady=(16, 8))
    progress = ttk.Progressbar(panel, mode="indeterminate")
    progress.pack(fill="x")
    ttk.Label(frame, text="2   Choose your SD card", font=("Avenir Next", 14, "bold")).pack(anchor="w", pady=(24, 10))
    drive_row = ttk.Frame(frame)
    drive_row.pack(fill="x")
    drive_choice = tk.Listbox(drive_row, height=2, exportselection=False, selectmode="browse",
                             font=("Avenir Next", 11), background=colors["panel"],
                             foreground=colors["text"], selectbackground=colors["active"],
                             selectforeground="#ffe0ae", highlightbackground=colors["border"],
                             highlightcolor=colors["gold"], relief="flat", borderwidth=0)
    drive_choice.pack(side="left", fill="x", expand=True, ipady=8)
    drive_choice.bind("<<ListboxSelect>>", lambda event: set_controls(not busy[0]))
    refresh = ttk.Button(drive_row, text="Refresh", command=refresh_disks)
    refresh.pack(side="right", padx=(10, 0))
    launch = ttk.Button(frame, text="Write and verify card →", style="Gold.TButton", command=flash, state="disabled")
    launch.pack(fill="x", pady=(14, 0))
    ttk.Label(frame, text="Writing erases the selected card. You will confirm its name and capacity before any write.\nThe official writing engine downloads automatically; the system may request authorization.",
              style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(12, 0))
    def poll():
        try:
            while True:
                kind, result = events.get_nowait()
                if kind == "progress":
                    status.set(result[-500:])
                    match = re.search(r'(Writing|Verifying).*?(\d+)\s*%', result)
                    if match:
                        progress.stop()
                        progress.configure(mode="determinate", value=int(match[2]))
                    continue
                progress.stop()
                progress.configure(mode="determinate", value=0)
                busy[0] = False
                if kind == "image":
                    ready[0] = result
                    image_name.set(result[0].name)
                    status.set("Verified: " + result[0].name)
                elif kind == "disks":
                    drives[:] = result
                    drive_choice.configure(state="normal")
                    drive_choice.delete(0, "end")
                    for d in drives:
                        drive_choice.insert("end", f"{d['name']} · {d['size'] / 1e9:.1f} GB · {d['device']}")
                    status.set("Select a removable drive." if drives else "No removable card found. Connect your reader, then refresh.")
                elif kind == "written":
                    progress.configure(value=100)
                    status.set("Card written and verified. You can now close the installer.")
                    messagebox.showinfo("Card ready", "Writing and read-back verification completed successfully. Eject the card before removing it.")
                else:
                    status.set("Operation failed or cancelled. Check the details below.")
                    messagebox.showerror("Unable to complete operation", result)
                set_controls(True)
        except queue.Empty:
            pass
        window.after(150, poll)

    def close():
        if busy[0]:
            messagebox.showinfo("Operation in progress", "Keep this window open until the operation finishes. Removing power while writing can leave the card unusable.")
        else:
            window.destroy()
    window.protocol("WM_DELETE_WINDOW", close)
    poll()
    refresh_disks()
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-disks', action='store_true', help='List eligible removable drives without writing')
    parser.add_argument('--flash-worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--gui', action='store_true', help='Open the native preparation window')
    parser.add_argument('--self-test', action='store_true', help='Check the bundled GUI and TLS runtime without accessing disks')
    parser.add_argument('--image', type=Path, help='Local .img/.img.xz (offline mode)')
    sums=parser.add_mutually_exclusive_group()
    sums.add_argument('--sha256', help='Expected SHA-256 for a local image')
    sums.add_argument('--checksum-file',type=Path,help='Matching local SHA256SUMS file')
    parser.add_argument('--platform',choices=tuple(IMAGE_PLATFORMS),default='alpine-standard',help='Latest stable image platform; never falls back across OS families')
    parser.add_argument('--cache', type=Path, default=Path.home() / 'Downloads/Astro-Bridge')
    parser.add_argument('--verify-only', action='store_true', help='Verify without launching or writing a disk')
    args = parser.parse_args()
    if args.self_test:
        import tkinter
        window = tkinter.Tk(); window.withdraw(); window.update_idletasks(); window.destroy()
        network_context()
        print('PASS GUI and TLS runtime; no disk access')
        return
    if args.list_disks:
        print(json.dumps(disks(), indent=2))
        return
    if args.flash_worker:
        raise SystemExit(flash_worker(args.flash_worker))
    if not args.image and (args.gui or len(sys.argv) == 1):
        gui(image_platform=args.platform)
        return
    try:
        if args.image and not (args.sha256 or args.checksum_file):
            parser.error('--image requires --sha256 or --checksum-file from the matching candidate')
        if args.checksum_file and not args.image:parser.error('--checksum-file requires --image')
        checksum=checksum_file(args.checksum_file,args.image.name) if args.checksum_file else args.sha256
        image = verified(args.image,checksum) if args.image else latest(args.cache,args.platform)
        print('Verified image:', image)
        if args.verify_only:
            return
        with image.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        gui((image, checksum),args.platform)
    except Exception as error:
        print('Unable to prepare image:', type(error).__name__, str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
