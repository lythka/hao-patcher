# annotate.py BINARY DISASM OUT: label rip-relative loads of __objc_selrefs / __objc_classrefs / __objc_superrefs
import re,struct,subprocess,sys
binp,dis,out=sys.argv[1:4]; f=open(binp,'rb').read()
lc=subprocess.run(['otool','-l',binp],capture_output=True,text=True).stdout.splitlines()
segs=[];sects={};cur=None;sec=None
for l in lc:
  t=l.split()
  if not t: continue
  if t[0]=='cmd' and t[1]=='LC_SEGMENT_64': cur={};segs.append(cur)
  elif t[0]=='sectname': sec=t[1]
  elif t[0] in('vmaddr','fileoff','filesize') and cur is not None and sec is None: cur[t[0]]=int(t[1],0)
  elif t[0]=='addr' and sec: sa=int(t[1],16)
  elif t[0]=='size' and sec: sects[sec]=(sa,int(t[1],16)); 
  elif t[0]=='offset' and sec: sec=None
def rd(va,n):
  va&=0xffffffffffff
  for s in segs:
    if 'filesize' in s and s['vmaddr']<=va<s['vmaddr']+s['filesize']: o=s['fileoff']+va-s['vmaddr']; return f[o:o+n]
def cstr(va):
  b=rd(va,200); return b.split(b'\0')[0].decode('latin1') if b else None
names={}
syms={}
for l in subprocess.run(['nm',binp],capture_output=True,text=True).stdout.splitlines():
  p=l.split(' ',2)
  if len(p)==3 and p[0].strip(): syms[int(p[0],16)]=p[2]
def ptr(va):
  b=rd(va,8); return struct.unpack('<Q',b)[0] if b else None
for name,kind in (('__objc_selrefs','sel'),('__objc_classrefs','cls'),('__objc_superrefs','super')):
  if name not in sects: continue
  a,n=sects[name]
  for i in range(0,n,8):
    p=ptr(a+i)
    if p is None: continue
    p&=0xfffffffffff  # strip chained-fixup high bits
    if kind=='sel': names[a+i]='sel '+(cstr(p) or hex(p))
    else:
      s=syms.get(p)
      if not s:
        # class_t -> data -> name
        d=ptr(p+0x20); nm=None
        if d: d&=0xfffffffffff; d&=~7; np=ptr(d+0x18); nm=cstr(np&0xfffffffffff) if np else None
        s=nm or hex(p)
      names[a+i]=kind+' '+s
lines=open(dis).readlines(); o=open(out,'w')
for i,l in enumerate(lines):
  m=re.search(r'(-?0x[0-9a-f]+)\(%rip\)',l)
  if m and i+1<len(lines):
    try:
      nxt=int(lines[i+1].split()[0],16); t=nxt+int(m.group(1),16)
      if t in names: l=l.rstrip('\n')+'    ;; '+names[t]+'\n'
    except ValueError: pass
  o.write(l)
print(len(names),'refs')
