"""Advisory stage costs, with unknown usage/bytes kept unknown. No game actions.

This is a planner, not an authorization or resource reservation. Missing native
usage remains unknown; retained samples do not exclude concurrent activity.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from ._livecapacity import resolve
from ._livecapturedispatch import CaptureDispatcher, CAMERA_SAMPLE_BYTES, CAMERA_TOTAL_BYTES
from ._liveclip import WindowClipRecorder, MAX_BYTES
from ._liveevidence import ReadEvidence, replay
from ._livebound import _decode

COUNTS=('captures','orbit_starts','orbit_stops','orbit_observations','room_discoveries',
        'room_entries','capture_observations','room_observations','native_requests','other_actions',
        'read_calls','orbit_calls','capture_calls','room_calls','clips')
BYTES=('image_bytes_each','camera_sample_bytes_each','clip_bytes_each')


def _count(v,name,maximum=2**53-1):
    if type(v)is not int or not 0<=v<=maximum:raise ValueError('invalid bounded count: '+name)
    return v


def _receipts(values,profile):
    out={}
    if values is None:return out
    if type(values)is not dict or set(values)-{'capture','orbit','roomentry'}:raise ValueError('fixed receipt names required')
    for kind,binding in values.items():
        if type(binding)is not dict or set(binding)!={'path','sha256'}:raise ValueError('exact frozen receipt binding')
        p=Path(binding['path'])
        if not p.is_absolute() or p.is_symlink():raise ValueError('absolute nonlink receipt required')
        with p.open('rb') as f:raw=f.read(4*1024**2+1)
        if len(raw)>4*1024**2 or hashlib.sha256(raw).hexdigest()!=binding['sha256']:raise ValueError('receipt hash mismatch')
        r=json.loads(raw)
        if r.get('capacity_profile')!=profile:raise ValueError('receipt capacity profile mismatch')
        if not isinstance(r.get('source_sha256'),dict) or not r['source_sha256']:raise ValueError('receipt source pins missing')
        for path,digest in r['source_sha256'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:raise ValueError('receipt source changed')
        expected={'capture':('capture_history','capture_calls'),'orbit':('orbit_history','orbit_calls'),
                  'roomentry':('roomentry_records','roomentry_calls')}[kind]
        if r.get('provider_limits')!={'records':profile[expected[0]],'calls':profile[expected[1]]}:
            raise ValueError('provider limit differs')
        md=r.get('native') if kind=='capture' else r.get('md_contract')
        n=md.get('contract',{}).get('max_records') if kind=='capture' and type(md)is dict else (
            md.get('limits',{}).get('records') if type(md)is dict else None)
        expected_md=profile['capture_native_records'] if kind=='capture' else profile[expected[0]]
        if n!=expected_md:raise ValueError('MD limit differs')
        out[kind]=dict(binding)
    return out


def _native(binding,profile,build,revision,synthetic):
    if binding is None:return {},None
    if type(binding)is not tuple or len(binding)!=2 or type(binding[0])is not ReadEvidence:
        raise ValueError('actual local ReadEvidence handle required')
    e,h=binding
    if type(synthetic)is not bool or (e.synthetic and not synthetic):raise ValueError('synthetic evidence not production')
    r=e.receipt(h);parsed=replay(e.transcript(h))
    if (r['request']!={'verb':'automation_capacity','args':[]} or r['status']!='OK'
        or r['build']!=build or e.client.bound.build!=build or r['identity']!=e.client.identity()
        or r['capture']!=parsed['capture']):raise ValueError('capacity source/identity/request mismatch')
    value=_decode(r['capture']['payload'])
    if (type(value)is not dict or set(value)!={'sdk','router','native','scope','reserved','future_fit_verified'}
        or value['scope']!='CURRENT_HELPER_INSTANCE_COUNTERS_NOT_FUTURE_RESERVATIONS'
        or value['reserved'] is not False or value['future_fit_verified'] is not False):
        raise ValueError('capacity scope/fields mismatch')
    for name,key in [('sdk','sdk_history'),('router','router_history'),('native','native_history')]:
        row=value[name]
        if (type(row)is not dict or row.get('profile')!=profile['name'] or row.get('revision')!=profile['revision']
            or row.get('requests_limit')!=profile[key] or row.get('eviction') is not False):raise ValueError('capacity profile/limit mismatch')
        _count(row.get('requests_used'),name,profile[key])
        if name!='native':
            if row.get('source_sha256')!=profile['source_sha256'] or row.get('scope')!='RETAINED_VALUE_CHARGES_NOT_RSS':
                raise ValueError('capacity source differs')
            expected=profile['sdk_retained_bytes' if name=='sdk' else 'router_retained_bytes']
            if row.get('retained_charge_limit_bytes')!=expected:raise ValueError('capacity byte limit differs')
            _count(row.get('retained_charge_bytes'),name,expected)
    if value['native'].get('provider_revision')!=revision or type(revision)is not str or len(revision)!=64:
        raise ValueError('native provider revision differs')
    if (value['native'].get('scope')!='RETAINED_NATIVE_PROVIDER_REQUEST_COUNT'
        or value['native'].get('future_fit_verified') is not False):raise ValueError('native counter scope differs')
    if value['router'].get('reads_limit')!=profile['read_history']:raise ValueError('read capacity limit differs')
    _count(value['router'].get('reads_used'),'reads',profile['read_history'])
    return value,dict(transcript_sha256=r['transcript_sha256'],identity=r['identity'],build=build,
        evidence_tier=r['evidence_tier'],first_native_age=r['first_envelope_age'],last_native_age=r['last_envelope_age'],
        atomic=False,future_reservation=False)


def evaluate(plan,*,capacity_profile='campaign_v1',capture=None,clip=None,frozen_receipts=None,
             native_evidence=None,expected_build=None,expected_native_revision=None,synthetic=False,provider_samples=None):
    """Read same-owner host usage if supplied; no zero defaults for missing owners.

    plan has all COUNTS and BYTES keys; a byte bound may be None (UNKNOWN).
    Counts/byte bounds are declarations, not measured future costs. Captures
    includes room/other dynamic allowances chosen by the campaign planner.
    """
    profile=resolve(capacity_profile)
    if type(plan)is not dict or set(plan)!=set(COUNTS+BYTES):raise ValueError('exact stage plan fields required')
    p={k:_count(plan[k],k,1_000_000)for k in COUNTS}
    p.update({k:None if plan[k] is None else _count(plan[k],k)for k in BYTES})
    for k,cap in [('image_bytes_each',256*1024**2),('camera_sample_bytes_each',CAMERA_SAMPLE_BYTES),('clip_bytes_each',MAX_BYTES)]:
        if p[k] is not None and p[k]>cap:raise ValueError('per-item byte limit exceeded: '+k)
    # These minima are source-backed operation costs, not estimates of polling.
    minima={'capture_calls':4*p['captures']+p['capture_observations'],
            'orbit_calls':2*(p['orbit_starts']+p['orbit_stops'])+p['orbit_observations'],
            'room_calls':p['room_discoveries']+2*p['room_entries']+p['room_observations'],'read_calls':2*p['captures']}
    if any(p[k]<v for k,v in minima.items()):raise ValueError('declared transport calls below fixed operation minimum')
    actions=(2*p['captures']+p['orbit_starts']+p['orbit_stops']+p['orbit_observations']+p['room_discoveries']+
             p['room_entries']+p['capture_observations']+p['room_observations']+p['other_actions'])
    if p['native_requests']>p['other_actions']:raise ValueError('native requests must be included in other actions')
    sources=_receipts(frozen_receipts,profile)
    samples={};collector=None;handles=None;ordered=False
    if provider_samples is not None:
        from ._liveusagesamples import CapacitySamples
        if type(provider_samples)is not tuple or len(provider_samples)!=2 or type(provider_samples[0])is not CapacitySamples:
            raise ValueError('actual CapacitySamples and opaque handles required')
        collector,handles=provider_samples
        if type(handles)is not dict or set(handles)-{'capture','orbit','roomentry'}:raise ValueError('fixed provider sample names')
        if collector.synthetic and not synthetic:raise ValueError('synthetic provider samples not production')
        for kind,h in handles.items():
            r=collector.receipt(h)
            if (r['status']!='completed' or r['kind']!=kind or r['capacity_profile']!=profile
                or r['build']!=expected_build or collector.latest.get(kind)is not h or kind not in sources
                or collector.receipts[kind]!=sources[kind]):raise ValueError('provider sample source/owner/selection mismatch')
            samples[kind]=r
        if native_evidence is None and collector.checkpoint is not None:
            native_evidence=collector.global_evidence(handles);ordered=True
        if native_evidence is not None and (type(native_evidence)is not tuple or len(native_evidence)!=2
            or type(native_evidence[0])is not ReadEvidence or native_evidence[0].client is not collector.client):
            raise ValueError('same-client global/provider evidence required')
    native,proof=_native(native_evidence,profile,expected_build,expected_native_revision,synthetic)
    if samples and proof and any(r['identity']!=proof['identity']for r in samples.values()):
        raise ValueError('global/provider sample identities differ')
    host=None;clip_used=None;clip_bytes=None
    if capture is not None:
        if type(capture)is not CaptureDispatcher:raise ValueError('actual capture dispatcher required')
        host=capture.capacity_usage()
        if host['capacity_profile']!=profile:raise ValueError('capture owner profile differs')
    if clip is not None:
        if type(clip)is not WindowClipRecorder:raise ValueError('actual clip recorder required')
        clip._check();clip_used=len(clip.records);clip_bytes=clip.retained_bytes
    items=[]
    def item(name,new,limit,used=None,source=True,tier='DECLARED_BOUND',usage_tier='MEASURED_SAME_OWNER'):
        # Even without current usage, a plan exceeding the entire quota is known infeasible.
        status='EXCEEDS_LIMIT' if new is not None and new>limit else (
            'UNKNOWN' if new is None or used is None or not source else (
            'DECLARED_BOUNDS_FIT' if used+new<=limit else 'EXCEEDS_LIMIT'))
        items.append(dict(name=name,planned=new,used=used,limit=limit,
            headroom=None if used is None else limit-used,status=status,
            planned_tier=tier,usage_tier=usage_tier if used is not None else 'UNKNOWN',source_bound=source))
    def current(section,key):return native.get(section,{}).get(key)
    tier=proof['evidence_tier'] if proof else 'UNKNOWN'
    item('sdk_requests',actions,profile['sdk_history'],current('sdk','requests_used'),usage_tier=tier)
    item('sdk_worst_case_retained_charge',actions*(profile['sdk_request_bytes']+profile['sdk_result_bytes']+profile['sdk_record_overhead']),profile['sdk_retained_bytes'],current('sdk','retained_charge_bytes'),usage_tier=tier)
    item('router_requests',actions,profile['router_history'],current('router','requests_used'),usage_tier=tier)
    item('router_reads',p['read_calls'],profile['read_history'],current('router','reads_used'),usage_tier=tier)
    item('router_worst_case_retained_charge',actions*(profile['packet_bytes']+64)+p['read_calls']*64,profile['router_retained_bytes'],current('router','retained_charge_bytes'),usage_tier=tier)
    item('native_requests',p['native_requests'],profile['native_history'],current('native','requests_used'),usage_tier=tier)
    for name,kind,new,key in [
        ('capture_provider_requests','capture',2*p['captures']+p['capture_observations'],'capture_history'),
        ('capture_md_records','capture',p['captures'],'capture_native_records'),
        ('capture_transport_calls','capture',p['capture_calls'],'capture_calls'),
        ('orbit_requests','orbit',p['orbit_starts']+p['orbit_stops']+p['orbit_observations'],'orbit_history'),
        ('orbit_transport_calls','orbit',p['orbit_calls'],'orbit_calls'),
        ('room_provider_requests','roomentry',p['room_entries']+p['room_discoveries']+p['room_observations'],'roomentry_records'),
        ('room_md_selected_records','roomentry',p['room_entries'],'roomentry_records'),
        ('room_transport_calls','roomentry',p['room_calls'],'roomentry_calls')]:
        r=samples.get(kind);used=None
        if r:
            used=r['md']['records_used']if name in ('capture_md_records','room_md_selected_records')else (
                r['lua']['calls_used']if name.endswith('calls')else r['lua']['history_used'])
        item(name,new,profile[key],used,source=kind in sources,usage_tier=r['evidence_tier']if r else'UNKNOWN')
    item('orbit_md_records',p['orbit_starts']+p['orbit_stops'],profile['orbit_history'],
         samples.get('orbit',{}).get('md',{}).get('records_used'),source='orbit'in sources,
         usage_tier=samples.get('orbit',{}).get('evidence_tier','UNKNOWN'))
    o=host['observer'] if host else None
    item('host_capture_records',p['captures'],profile['host_capture_records'],host['records_used'] if host else None)
    item('directory_members',p['captures'],profile['host_directory_members'],o['directory_members'] if o else None)
    image=None if p['image_bytes_each'] is None else p['captures']*p['image_bytes_each']
    camera=None if p['camera_sample_bytes_each'] is None else 2*p['captures']*p['camera_sample_bytes_each']
    item('retained_image_bytes',image,profile['capture_total_bytes'],o['image_bytes'] if o else None)
    item('retained_camera_bytes',camera,CAMERA_TOTAL_BYTES,host['camera_bytes'] if host else None)
    item('clip_records',p['clips'],128,clip_used)
    clip_charge=None if p['clip_bytes_each'] is None else p['clips']*p['clip_bytes_each']
    item('retained_clip_bytes',clip_charge,2*1024**3,clip_bytes)
    peak=None if p['clip_bytes_each'] is None else (max(0,p['clips']-1)*p['clip_bytes_each']+MAX_BYTES if p['clips'] else 0)
    item('clip_pre_effect_reservation',peak,2*1024**3,clip_bytes)
    if capture is not None:capture._current()
    if clip is not None:clip._check()
    if proof:
        if capture is not None and capture.identity!=proof['identity']:raise ValueError('capture/native owner differs')
        if clip is not None and clip.initial!=proof['identity']:raise ValueError('clip/native owner differs')
        _native(native_evidence,profile,expected_build,expected_native_revision,synthetic)
    if collector:
        if capture is not None and capture.identity!=collector.identity:raise ValueError('capture/provider owner differs')
        if clip is not None and clip.initial!=collector.identity:raise ValueError('clip/provider owner differs')
        for kind,h in handles.items():
            current_sample=collector.receipt(h)
            if current_sample.get('journal')!=samples[kind].get('journal'):raise ValueError('provider sample changed during planning')
        if ordered:collector.global_evidence(handles)
    if _receipts(frozen_receipts,profile)!=sources:raise ValueError('receipt changed during planning')
    # Native current counters are not inferred from limits or a fresh generation.
    return dict(status='NOT_READY',scope='ADVISORY_STAGE_COST_NOT_RESERVATION_OR_RUNTIME_ACCEPTANCE',
        capacity_profile=profile,plan=p,items=items,frozen_receipts=sources,
        native_current_usage='SAMPLED_BOUND_COUNTERS' if proof else 'UNKNOWN_NO_BOUND_COUNTER_READ',
        native_evidence=proof,provider_samples=samples,global_sample_after_own_provider_samples=ordered,
        runtime_accepted=False,
        blockers=[x['name'] for x in items if x['status']!='DECLARED_BOUNDS_FIT']+
                 [kind+'_md_busy'for kind,r in samples.items()if r['md']['busy']]+
                 ['PRIVATE_PROVIDER_DEMANDS_AND_FOREIGN_CONCURRENCY_NOT_QUALIFIED'],
        limits=['Declared byte ceilings are not measurements or reservations',
                'Two camera reads per capture are a proven minimum; native_save_path reads and polling require additional declared allowance',
                'Room discoveries consume Lua/SDK history independently of selected MD records',
                '128 clips is a record ceiling, not a guarantee under the two-GiB byte quota',
                'Released full frame journals remain on disk; their disk growth is not included in image-byte quota'])
