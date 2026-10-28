#!/usr/bin/python3
"""Run by the repository Ansible controller, using its existing signing key."""
from pathlib import Path
import argparse, datetime, email.utils, fcntl, gzip, hashlib, json, lzma, os, shutil, subprocess
parser=argparse.ArgumentParser()
parser.add_argument('--check', action='store_true')
args=parser.parse_args()
repo=Path('/var/www/webdata/tunnelbiz.com/pelandukos/repository')
work=Path(__file__).resolve().parent
suite=repo/'dists/melaka'
custom=Path('/root/pelandukos-repo-refresh/custom-Packages')
locks=[]
for p in ('/root/pelandukos-repo-sync/sync.lock','/root/pelandukos-repo-refresh/.package-update.lock'):
 f=open(p,'a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
manifest=json.loads((work/'manifest.json').read_text())
artifacts=manifest['artifacts'];entries=manifest['packages']
for name,h in artifacts.items():
 assert Path(name).name==name
 assert hashlib.sha256((work/name).read_bytes()).hexdigest()==h,name
for e in entries:
 name=e['package'];version=e['version'];stanza=e['binary'];source=e['source']
 assert 'Package: '+name in stanza.splitlines()
 assert 'Version: '+version in stanza.splitlines()
 assert 'Architecture: amd64' in stanza.splitlines()
 deb=f'{name}_{version}_amd64.deb'
 assert f'Filename: pool/pelandukos/{name}/{deb}' in stanza.splitlines()
 assert 'SHA256: '+artifacts[deb] in stanza.splitlines()
 assert f'Directory: pool/pelandukos/{name}' in source.splitlines()
 assert 'Package: '+e.get('source_package',name) in source.splitlines()
 for filename in e['files']:
  assert filename in artifacts
  dst=repo/'pool/pelandukos'/name/filename
  if dst.exists():assert hashlib.sha256(dst.read_bytes()).hexdigest()==artifacts[filename],'Immutable artifact conflict: '+filename

def merge(text, entry):
 name=entry['package'];new=entry['stanza']
 blocks=[b for b in text.strip().split('\n\n') if b.strip() and 'Package: '+name not in b.splitlines()]
 return '\n\n'.join(blocks+[new.strip()])+'\n\n'

changes={}
for comp in ('main','pelandukos'):
 for rel,kind in ((f'{comp}/binary-amd64/Packages','binary'),(f'{comp}/source/Sources','source')):
  raw=(suite/rel).read_text()
  for e in entries:raw=merge(raw,{'package':e.get('source_package',e['package']) if kind=='source' else e['package'],'stanza':e[kind]})
  data=raw.encode()
  for suffix,b in (('',data),('.gz',gzip.compress(data,mtime=0)),('.xz',lzma.compress(data,preset=1))):changes[rel+suffix]=b
old=(suite/'Release').read_text();lines=[];algo=None;accounted=set()
for line in old.splitlines():
 if line in ('MD5Sum:','SHA1:','SHA256:','SHA512:'):algo={'MD5Sum:':'md5','SHA1:':'sha1','SHA256:':'sha256','SHA512:':'sha512'}[line]
 if line.startswith('Date:'):line='Date: '+email.utils.formatdate(usegmt=True)
 elif line.startswith(' ') and algo:
  parts=line.split()
  if len(parts)==3 and parts[2] in changes:
   data=changes[parts[2]];accounted.add((algo,parts[2]))
   line=f' {hashlib.new(algo,data).hexdigest()} {len(data)} {parts[2]}'
 lines.append(line)
assert {r for a,r in accounted if a=='sha256'}==set(changes),'Release missing index checksums'
if args.check:
 print('Validated artifacts, immutable filenames, index merge and Release coverage; no repository changes.')
 raise SystemExit(0)
marker=work/'published.json'
if marker.exists() and json.loads(marker.read_text())==manifest:
 assert all(e['binary'].strip() in (suite/'pelandukos/binary-amd64/Packages').read_text() for e in entries)
 assert all(e['source'].strip() in (suite/'pelandukos/source/Sources').read_text() for e in entries)
 print('Already published and verified');raise SystemExit(0)
stage=work/'signed';stage.mkdir(exist_ok=True)
(stage/'Release').write_text('\n'.join(lines)+'\n')
key='21419FC6FF91FE8F0C57F401DF9B1E213E5C6CC3'
for flags,name in ((['--clearsign'],'InRelease'),(['--armor','--detach-sign'],'Release.gpg')):
 subprocess.run(['gpg','--homedir','/root/pelandukos-repo-signing','--batch','--yes','--pinentry-mode','loopback','--local-user',key,'--digest-algo','SHA256',*flags,'--output',str(stage/name),str(stage/'Release')],check=True)
subprocess.run(['gpgv','--keyring',str(repo/'keyrings/pelandukos-repo-keyring.gpg'),str(stage/'InRelease')],check=True)
backup=Path('/root/pelanduk-repo-backups')/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-iso-builder')
backup.mkdir(parents=True)
shutil.copy2(custom,backup/'custom-Packages')
for rel in [*changes,'Release','Release.gpg','InRelease']:
 dst=backup/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(suite/rel,dst)
def write(dst,data):
 dst.parent.mkdir(parents=True,exist_ok=True)
 tmp=dst.with_name(dst.name+'.builder-next');tmp.write_bytes(data);tmp.chmod(0o644);os.replace(tmp,dst)
for e in entries:
 for name in e['files']:write(repo/'pool/pelandukos'/e['package']/name,(work/name).read_bytes())
for rel,data in changes.items():
 for alg in ('sha256','sha512'):write((suite/rel).parent/'by-hash'/alg.upper()/hashlib.new(alg,data).hexdigest(),data)
for rel,data in changes.items():write(suite/rel,data)
c=custom.read_text()
for e in entries:c=merge(c,{'package':e['package'],'stanza':e['binary']})
write(custom,c.encode())
for name in ('Release','Release.gpg','InRelease'):write(suite/name,(stage/name).read_bytes())
marker.write_text(json.dumps(manifest,sort_keys=True))
print('Published PelandukOS release; backup:',backup)
