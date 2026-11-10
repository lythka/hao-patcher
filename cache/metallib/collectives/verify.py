"""Read-only validation of native GPU outputs; dispatch commands remain explicit."""
from pathlib import Path
import argparse,array,json,re,struct,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'));import collective_runtime
parser=argparse.ArgumentParser(description='Compare native collective readback against a CPU oracle')
parser.add_argument('--data',type=Path,required=True)
parser.add_argument('--outputs',type=Path,required=True)
parser.add_argument('--log',type=Path,required=True)
parser.add_argument('--report',type=Path,required=True)
a=parser.parse_args()
m=array.array('I');m.frombytes((a.data/'masks.bin').read_bytes())
data=array.array('I');data.frombytes((a.data/'input.bin').read_bytes());results=[]
log=a.log.read_text()
for symbol,body in collective_runtime.sources().items():
 entry=log.split(symbol+'.metallib\n',1)[1];device=json.loads(re.search(r'\{"device"[^\n]+',entry)[0]);width=device['width'];b=(a.outputs/(symbol+'.out')).read_bytes();expected=bytearray(b'\xcd'*len(b));count=int(re.search(r'define <(\d)',body)[1]) if 'define <' in body else 1;checked=0
 for g,mask in enumerate(m):
  active=[l for l in range(width) if mask>>l&1]
  for lane in active:
   lanes=[s for s in active if ('_quad_' not in symbol or s//4==lane//4) and ('_prefix_exclusive_' not in symbol or s<lane) and ('_prefix_inclusive_' not in symbol or s<=lane)]
   vals=[]
   if symbol=='air_simd_ballot_i64':
    value=sum(1<<s for s in active if data[(g*width+s)*4]&1)
    struct.pack_into('<4I',expected,(g*width+lane)*16,value & 0xffffffff,value>>32,0,0)
    checked+=1
    continue
   for c in range(count):
    v=[data[(g*width+s)*4+c] for s in lanes]
    if symbol=='air_simd_is_first':value=int(lane==active[0])
    elif '_sum_' in symbol:value=sum(v) if v else -0.0
    elif '_min_' in symbol:value=min(v)
    elif '_max_' in symbol:value=max(v)
    elif '_or_' in symbol:
     value=0
     for q in v:value|=q
    elif symbol.endswith('_all'):value=int(all(v))
    elif symbol.endswith('_any'):value=int(any(v))
    else:raise ValueError(symbol)
    vals.append(struct.unpack('<I',struct.pack('<f',float(value)))[0]);checked+=1
   vals += [0]*(4-count);struct.pack_into('<4I',expected,(g*width+lane)*16,*vals)
 diff=[i//4 for i in range(0,len(b),4) if b[i:i+4]!=expected[i:i+4]]
 row=dict(symbol=symbol,**device,active_scalar_comparisons=checked,different_words=len(diff),examples=[dict(index=i,actual=struct.unpack_from('<I',b,i*4)[0],expected=struct.unpack_from('<I',expected,i*4)[0]) for i in diff[:6]])
 print(row);results.append(row)
a.report.write_text(json.dumps(results,indent=2))
assert not any(r['different_words'] for r in results)
