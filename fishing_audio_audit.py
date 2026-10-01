"""Offline labeled short-time loudness comparison; never enables a controller."""
import argparse,json,math,wave
from pathlib import Path
import numpy as np

def measure(folder,at,start=.1,end=.5):
    rows=[json.loads(l) for l in (folder/'audio-timeline.jsonl').read_text().splitlines()]
    with wave.open(str(folder/'audio.wav'),'rb') as wav:pcm=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(float)/32768
    chunks=[]
    for r in rows:
        if r['end']<at+start or r['at']>at+end:continue
        lo=max(0,round((at+start-r['at'])*16000));hi=min(r['samples'],round((at+end-r['at'])*16000))
        if hi>lo:chunks.append(pcm[r['start_sample']+lo:r['start_sample']+hi])
    if not chunks:return None
    audio=np.concatenate(chunks);values=[float(np.sqrt(np.mean(audio[i:i+800]**2))) for i in range(0,len(audio)-799,160)]
    if not values:return None
    peak=max(values)
    return dict(rms50_max=peak,dbfs=20*math.log10(max(peak,1e-12)),rms=float(np.sqrt(np.mean(audio**2))),samples=len(audio))

def audit(folders):
    own=[];neighbors=[];background=[]
    for folder in folders:
        if not (folder/'audio-summary.json').exists():continue
        if json.loads((folder/'audio-summary.json').read_text()).get('error'):continue
        for cast in sorted(folder.glob('cast-*')):
            if not (cast/'events.jsonl').exists():continue
            events=[json.loads(l) for l in (cast/'events.jsonl').read_text().splitlines()]
            reels=[e for e in events if e['event']=='reel_sent'];signals=[e.get('signal_at',e['at']-e['signal_to_ack_ms']/1000) for e in reels]
            result_path=cast/'corrected-verification.json' if (cast/'corrected-verification.json').exists() else cast/'result.json'
            result=json.loads(result_path.read_text()) if result_path.exists() else {}
            for reel,signal in zip(reels,signals):
                if not result.get('confirmed'):continue
                value=measure(folder,signal,end=min(.5,reel['at']-signal-.05))
                if value:own.append(dict(run=str(folder),cast=cast.name,at=signal,**value))
            for e in events:
                if e['event']=='neighbor_visual_candidate' and e['metrics']['splash']>=100 and all(abs(e['at']-s)>1.5 for s in signals):
                    value=measure(folder,e['at'])
                    if value:neighbors.append(dict(run=str(folder),cast=cast.name,at=e['at'],candidate=e['candidate'],**value))
                if e['event']=='watch' and e.get('audio',{}).get('available') and all(abs(e['at']-s)>1 for s in signals):
                    background.append(e['audio']['rms50_max'])
    pmin=min((r['rms50_max'] for r in own),default=None);nmax=max((r['rms50_max'] for r in neighbors),default=None)
    gap=pmin is not None and nmax is not None and pmin>nmax
    return dict(record_only=True,own=own,neighbor_visual_candidates=neighbors,own_count=len(own),neighbor_count=len(neighbors),own_min=pmin,neighbor_max=nmax,background_max=max(background,default=None),separable_in_sample=gap,provisional_midpoint=(pmin+nmax)/2 if gap else None,note='Labeled own catches; neighbor splash candidates require review. Event window +100..500ms clipped before reel. No threshold enabled; background_max includes unrelated sounds.')

def include_independent_others(report,folders):
    negatives=[]
    for folder in folders:
        review=json.loads((folder/'review.json').read_text())
        accepted=set(review['accepted_patches'])
        for line in (folder/'events.jsonl').read_text().splitlines():
            e=json.loads(line)
            if e['patch'] in accepted:
                value=measure(folder,e['at'])
                if value:negatives.append(dict(folder=str(folder),patch=e['patch'],at=e['at'],**value))
    own=[x['rms50_max'] for x in report['own']];other=[x['rms50_max'] for x in negatives]
    if not own or not other:raise ValueError('Need both labeled classes')
    overlap=min(own)<=max(other)
    summary=dict(own_count=len(own),other_count=len(other),own_range=[min(own),max(own)],own_median=float(np.median(own)),other_range=[min(other),max(other)],other_median=float(np.median(other)),overlap=overlap,threshold_enabled=False,threshold_trials=[dict(threshold=t,own_detected=sum(x>=t for x in own),other_above=sum(x>=t for x in other)) for t in [.0029,.003,.0035,.004,.005]],record_only=True)
    report['independent_other_observation']=summary;report['independent_other_samples']=negatives
    report['separable_in_sample']=not overlap
    report['provisional_midpoint']=None if overlap else (min(own)+max(other))/2
    report['note']='Independent other-only observation included. No threshold enabled; sample separation is not operational validation.'
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folders',nargs='+',type=Path);p.add_argument('--other',nargs='*',type=Path,default=[]);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=audit(a.folders)
    if a.other:r=include_independent_others(r,a.other)
    a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in r.items() if k not in ('own','neighbor_visual_candidates','independent_other_samples')},ensure_ascii=False))
