#!/usr/bin/env python3
"""Install checksum-verified workshop tools under .local/bin, without global changes."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
BIN = LOCAL / 'bin'
DOWNLOADS = LOCAL / 'downloads'
VERSIONS = {'kind': '0.33.0', 'kubectl': '1.36.4', 'helm': '4.3.0', 'node': '24.21.0'}

def fetch(url, target):
    subprocess.run(['curl', '-fsSL', '--retry', '3', '--connect-timeout', '15', '-o', str(target), url], check=True)

def install():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64', 'amd64': 'amd64'}.get(machine)
    if system not in ('darwin', 'linux') or not arch:
        raise SystemExit('Bootstrap supports macOS/Linux arm64/amd64. Install pinned versions manually on other systems.')
    BIN.mkdir(parents=True, exist_ok=True)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    for tool in ('kind', 'kubectl', 'helm', 'node'):
        if (BIN / tool).exists():
            print(f'{tool}: already installed locally')
            continue
        version = VERSIONS[tool]
        if tool == 'kind':
            name = f'kind-{system}-{arch}'
            base = f'https://github.com/kubernetes-sigs/kind/releases/download/v{version}'
            url, checksum_url = f'{base}/{name}', f'{base}/{name}.sha256sum'
        elif tool == 'kubectl':
            name = f'kubectl-{system}-{arch}'
            url = f'https://dl.k8s.io/release/v{version}/bin/{system}/{arch}/kubectl'
            checksum_url = url + '.sha256'
        elif tool == 'helm':
            name = f'helm-v{version}-{system}-{arch}.tar.gz'
            url = f'https://get.helm.sh/{name}'
            checksum_url = url + '.sha256sum'
        else:
            node_os = 'darwin' if system == 'darwin' else 'linux'
            node_arch = 'arm64' if arch == 'arm64' else 'x64'
            name = f'node-v{version}-{node_os}-{node_arch}.tar.gz'
            url = f'https://nodejs.org/dist/v{version}/{name}'
            checksum_url = f'https://nodejs.org/dist/v{version}/SHASUMS256.txt'
        archive = DOWNLOADS / name
        sums = DOWNLOADS / f'{name}.sha256'
        fetch(checksum_url, sums)
        if tool == 'node':
            expected = next(line.split()[0] for line in sums.read_text().splitlines() if line.split()[-1] == name)
        else:
            expected = sums.read_text().split()[0]
        if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            print(f'Downloading {tool} v{version} ({system}/{arch})', flush=True)
            fetch(url, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            archive.unlink()
            raise SystemExit(f'Checksum failed for {tool}')
        if tool in ('kind', 'kubectl'):
            shutil.copy2(archive, BIN / tool)
            (BIN / tool).chmod(0o755)
        elif tool == 'helm':
            with tarfile.open(archive) as tar:
                member = tar.extractfile(f'{system}-{arch}/helm')
                (BIN / 'helm').write_bytes(member.read())
                (BIN / 'helm').chmod(0o755)
        else:
            node_dir = LOCAL / 'node'
            node_dir.mkdir(exist_ok=True)
            with tarfile.open(archive) as tar:
                tar.extractall(path=DOWNLOADS, filter='data')
            extracted = DOWNLOADS / name.removesuffix('.tar.gz')
            if node_dir.exists():
                shutil.rmtree(node_dir)
            shutil.move(extracted, node_dir)
            for executable in ('node', 'npm', 'npx'):
                destination = BIN / executable
                if destination.is_symlink() or destination.exists():
                    destination.unlink()
                destination.symlink_to(node_dir / 'bin' / executable)
        print(f'{tool}: v{version} checksum verified', flush=True)

if __name__ == '__main__':
    install()
