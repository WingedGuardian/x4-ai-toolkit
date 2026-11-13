"""Restricted, agent-reviewed conversation shortcut prototype. Runtime UNQUALIFIED.

No focus/capture/launch operation. Windows SendInput has no target HWND and cannot
close the sampled foreground race. Inserted events never imply game acceptance.
Production uses local ClipPort/ReadEvidence/AutomationClient handles, not exported
caller receipts. Review transcripts remain AGENT_REVIEWED judgment, not pixel truth.
"""
from __future__ import annotations
import copy
import ctypes
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from ._liveactions import ActionJournal, _write_new, _read_json, request_digest
from ._liveautomation import AutomationClient
from ._livebound import _decode
from ._liveclip import window_for_peer, run_bounded
from ._liveclipport import ClipPort
from ._livecapture import decode_image
from ._liveevidence import ReadEvidence
from ._livepersistence import _route, _file


class InputUnavailable(ValueError):
    pass


def need(value, why):
    if not value:
        raise InputUnavailable(why)


def finite(v):
    try:
        return type(v) in (int, float) and math.isfinite(v) and v >= 0
    except OverflowError:
        return False  # silent-ok: False drives explicit malformed-clock refusal.


def hexid(v, n):
    return type(v) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}', v) is not None


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def actor(v):
    need(type(v) is dict, 'typed native actor required')
    if v.get('kind') == 'COMPONENT':
        need(set(v) == {'kind', 'component'}, 'component actor fields')
        keys = ('component',)
    else:
        need(v.get('kind') == 'TEMPLATE' and set(v) == {'kind', 'context', 'seed'}, 'template actor fields')
        keys = ('context', 'seed')
    out = {'kind': v['kind']}
    for k in keys:
        s = v[k]
        need(type(s) is str and re.fullmatch(r'[1-9]\d{0,19}(?:ULL)?', s), 'exact positive actor identity')
        n = int(s.removesuffix('ULL')); need(n < 2**64, 'actor identity exceeds uint64')
        out[k] = str(n)
    return out


class WindowsDigits:
    """Win32 declarations only at construction; no input until send_pair().

    Sources: Microsoft SendInput/KEYBDINPUT/GUITHREADINFO/MapVirtualKeyExW docs.
    Zero GetAsyncKeyState cannot distinguish released keys from access failure.
    Returned keyboard scope explicitly preserves that uncertainty.
    """
    def __init__(self):
        need(os.name == 'nt', 'Windows input only')
        from ctypes import wintypes as W
        self.W = W
        class KI(ctypes.Structure):
            _fields_ = [('vk', W.WORD), ('scan', W.WORD), ('flags', W.DWORD),
                        ('time', W.DWORD), ('extra', ctypes.c_size_t)]
        class MI(ctypes.Structure):
            _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('data', W.DWORD),
                        ('flags', W.DWORD), ('time', W.DWORD), ('extra', ctypes.c_size_t)]
        class HI(ctypes.Structure):
            _fields_ = [('message', W.DWORD), ('low', W.WORD), ('high', W.WORD)]
        class U(ctypes.Union):
            _fields_ = [('ki', KI), ('mi', MI), ('hi', HI)]
        class INPUT(ctypes.Structure):
            _fields_ = [('type', W.DWORD), ('value', U)]
        class GUI(ctypes.Structure):
            _fields_ = [('size', W.DWORD), ('flags', W.DWORD),
                        *[(k, W.HWND) for k in ('active', 'focus', 'capture', 'menu', 'move', 'caret')],
                        ('rect', W.RECT)]
        need(ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28), 'INPUT ABI')
        self.INPUT, self.GUI = INPUT, GUI
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        signatures = {
            'GetForegroundWindow': ([], W.HWND), 'GetWindowThreadProcessId': ([W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            'IsWindowEnabled': ([W.HWND], W.BOOL), 'GetGUIThreadInfo': ([W.DWORD, ctypes.POINTER(GUI)], W.BOOL),
            'GetKeyboardLayout': ([W.DWORD], W.HANDLE), 'GetAsyncKeyState': ([ctypes.c_int], ctypes.c_short),
            'MapVirtualKeyExW': ([W.UINT, W.UINT, W.HANDLE], W.UINT),
            'SendInput': ([W.UINT, ctypes.POINTER(INPUT), ctypes.c_int], W.UINT)}
        for name, (args, ret) in signatures.items():
            fn = getattr(self.u, name); fn.argtypes = args; fn.restype = ret

    def snapshot(self, peer, expected=None, *, owned_scan=None):
        win = window_for_peer(peer, expected)
        hwnd = int(win['hwnd']); u = self.u; pid = self.W.DWORD()
        tid = u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        need(tid and pid.value == peer['pid'] and int(u.GetForegroundWindow() or 0) == hwnd,
             'exact X4 window is not foreground')
        need(u.IsWindowEnabled(hwnd), 'window disabled')
        info = self.GUI(); info.size = ctypes.sizeof(info)
        need(u.GetGUIThreadInfo(tid, ctypes.byref(info)), 'GUI thread state unavailable')
        need(int(info.active or 0) == hwnd and int(info.focus or 0) == hwnd and
             not info.flags and not info.capture and not info.menu and not info.move and not info.caret,
             'native modal/caret/focus state not eligible')
        layout = int(u.GetKeyboardLayout(tid) or 0); need(layout, 'keyboard layout unavailable')
        held = [key for key in range(1, 255) if u.GetAsyncKeyState(key) & 0x8000]
        if owned_scan is None:
            need(not held, 'pressed keyboard/mouse state observed; no automatic releases')
        else:
            need(type(owned_scan) is int and 2 <= owned_scan <= 7 and held in ([], [0x30+owned_scan-1]),
                 'keys other than recorded owned digit observed')
        need(int(u.GetForegroundWindow() or 0) == hwnd, 'foreground changed during preflight')
        return {'window': win, 'thread': int(tid), 'layout': str(layout), 'down_bits': held,
                'keyboard_scope': 'NO_DOWN_BITS_OBSERVED_ACCESS_FAILURE_NOT_EXCLUDED'}

    def translate(self, scan, state):
        need(type(scan) is int and 2 <= scan <= 7, 'only unmodified top-row digit scan2–7')
        layout = int(state['layout']); vk = self.u.MapVirtualKeyExW(scan, 3, layout)
        need(vk == 0x30 + scan - 1 and self.u.MapVirtualKeyExW(vk, 4, layout) == scan,
             'current-layout digit scan/VK roundtrip unavailable')
        return scan

    def send_pair(self, scan):
        need(type(scan) is int and 2 <= scan <= 7, 'digit scan only')
        events = (self.INPUT * 2)()
        for i, flags in enumerate((0x0008, 0x0008 | 0x0002)):
            events[i].type = 1; events[i].value.ki.scan = scan; events[i].value.ki.flags = flags
        ctypes.set_last_error(0)
        sent = self.u.SendInput(2, events, ctypes.sizeof(self.INPUT))
        return {'inserted': int(sent), 'winerror': ctypes.get_last_error()}

    def send_release(self, scan):
        need(type(scan) is int and 2 <= scan <= 7, 'owned digit scan only')
        events = (self.INPUT * 1)(); events[0].type = 1
        events[0].value.ki.scan = scan; events[0].value.ki.flags = 0x0008 | 0x0002
        ctypes.set_last_error(0)
        sent = self.u.SendInput(1, events, ctypes.sizeof(self.INPUT))
        return {'inserted': int(sent), 'winerror': ctypes.get_last_error()}


class DialogInput:
    """Local owner; no exported receipt can recreate actor/frame/review tickets.

    Actor snapshots are fixed observational requests. The caller explicitly drives
    begin_actor/context polling and existing clip capture; no capture/input is hidden.
    reviews contain literal visual transcription, not a caller 'qualified' boolean.
    The reviewing agent is trusted judgment, not a security boundary or OCR engine.
    """
    def __init__(self, client, clips, journal, *, observer_revision, expected_actor,
                 expected_actor_label, clock=time.monotonic, backend=None, synthetic=False):
        need(isinstance(client, AutomationClient) and isinstance(clips, ClipPort) and clips.client is client,
             'same actual client and ClipPort required')
        need(type(synthetic) is bool and (synthetic or (client._native_connection is True and not clips.synthetic)),
             'production connection/capture owner required')
        need(synthetic or (backend is None and clock is time.monotonic),
             'production requires actual Windows backend and host monotonic clock')
        need(hexid(observer_revision, 64), 'source-bound observer revision required')
        need(type(expected_actor_label) is str and 0 < len(expected_actor_label) <= 256, 'expected visible actor label')
        self.client, self.clips, self.peer = client, clips, client.bound.link.peer
        self.initial = copy.deepcopy(client.identity()); self.build = client.bound.build
        self.expected_actor = actor(expected_actor); self.label = expected_actor_label
        self.revision, self.clock = observer_revision, clock
        self.backend = backend if backend is not None else WindowsDigits()
        self.root = _route(journal, directory=True); self.root_id = self.root.stat().st_dev, self.root.stat().st_ino
        need(not self.root.is_relative_to(clips.profile.parent), 'input journal cannot be in personal game tree')
        self.synthetic = synthetic; self.busy = threading.Lock(); self.last = None; self.input_fault = None
        self.actors, self.contexts, self.frames, self.reviews, self.tickets = {}, {}, {}, {}, {}
        self.releases = {}
        self.source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.owner = {'version': 1, 'identity': self.initial, 'build': self.build, 'source': self.source,
                      'profile': self.peer.profile_id, 'policy': 'NO_REOPEN_NO_REPLAY_GLOBAL_INPUT_NONATOMIC'}
        self._current(require_owner=False); _write_new(self.root/'dialog-input-owner.json', self.owner)

    def _now(self):
        t = self.clock(); need(finite(t) and (self.last is None or t >= self.last), 'host monotonic clock')
        self.last = t; return t

    def _current(self, *, require_owner=True):
        need(_route(self.root, directory=True) == self.root and
             (self.root.stat().st_dev, self.root.stat().st_ino) == self.root_id, 'journal replaced')
        need(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == self.source, 'input source changed')
        if require_owner:
            need((self.root/'dialog-input-owner.json').is_file(), 'input owner record missing')
            need(_read_json(self.root/'dialog-input-owner.json') == self.owner, 'input owner record changed')
        need(self.client.identity() == self.initial and self.client.bound.build == self.build and
             self.peer() == self.initial['peer'], 'connected session/profile/process changed')
        self._now()

    def _stamp(self):
        return {k: self.initial[k] for k in ('provider', 'revision', 'session', 'world_token')} | {
            'generation': self.initial['ui_generation']}

    def begin_actor(self):
        self._current(); need(len(self.actors) < 128, 'actor snapshot limit')
        request = {'version': 1, 'id': secrets.token_hex(16), 'session': self.initial['session'],
                   'provider': 'x4-conversation-observer-v1', 'action': 'snapshot', 'arguments': {}, 'binding': self.revision}
        handle = object(); row = {'request': request, 'started': self._now(), 'sent': False}
        self.actors[handle] = row
        prepared = self.client.prepare(request)
        need(prepared['status'] == 'prepared', 'snapshot not ready; no automatic prepare retry')
        row['sent'] = True; self.client.execute(request['id'])
        return handle

    def context(self, actor_handle):
        self._current(); need(actor_handle in self.actors, 'local actor ticket required')
        row = self.actors[actor_handle]; receipt = self.client.status(row['request']['id'])
        if receipt['status'] != 'completed':
            return None
        o = receipt.get('observations', {}); s = o.get('snapshot', {})
        need(o.get('proof_scope') == 'CONVERSATION_SNAPSHOT_ONLY' and o.get('read_only') is True and
             o.get('gameplay_action_attempted') is False and s.get('request') == row['request']['id'] and
             s.get('stamp') == self._stamp() and s.get('active') is True and s.get('stable') is True and
             s.get('history_matches') is True and s.get('status') == 'ACTOR_AND_HISTORICAL_SECTION_OBSERVED',
             'current actor/instance snapshot unqualified')
        need(actor(s['actor']) == self.expected_actor and type(s.get('instance')) is int and s['instance'] > 0 and
             type(s.get('sequence')) is int and s['sequence'] >= s['instance'] and
             type(s.get('conversation')) is str and s['conversation'] and type(s.get('section')) is str and s['section'],
             'wrong or incomplete native actor/instance')
        collector = ReadEvidence(self.client, synthetic=self.synthetic)
        rh = collector.collect('dialog_probe'); proof = collector.receipt(rh)
        need(proof['status'] == 'OK' and proof['identity'] == self.initial and proof['build'] == self.build,
             'dialog read binding')
        model = _decode(proof['capture']['payload'])
        need(model.get('status') == 'UNBOUND_MODEL_OBSERVED' and model.get('stamp') == self._stamp() and
             model.get('dialog_active') is True and model.get('dialog_active_after') is True and
             model.get('conversation_active') is True and model.get('conversation_cancelling') is False and
             model.get('rows_read') == 6 and len(model.get('options', [])) == 6 and not model.get('issues'),
             'six readable active dialog rows required')
        b = model.get('current_bindings', {})
        need(b.get('status') == 'TWO_EQUAL_PROJECTED_READS' and len(b.get('rows', [])) == 6, 'current bindings unavailable')
        for i, binding in enumerate(b['rows'], 1):
            need(type(binding) is dict and binding.get('choice') == i and binding.get('action') == 96+i and
                 binding.get('status') == 'OBSERVED' and type(binding.get('count')) is int and
                 0 <= binding['count'] <= 4 and type(binding.get('inputs')) is list and
                 len(binding['inputs']) == binding['count'], 'all six binding rows must be accounted')
            for v in binding['inputs']:
                need(type(v) is list and len(v) == 4 and all(type(n) is int for n in v[:3]) and
                     0 <= v[0] <= 65535 and 0 <= v[1] <= 2147483647 and v[2] in (-1,0,1) and
                     (type(v[3]) is bool or v[3] == 'NOT_PRESENT'), 'native binding tuple malformed')
        offered = []
        for i, option in enumerate(model['options'], 1):
            need(option.get('index') == i and option.get('readable') is True and
                 type(option.get('text')) is str and type(option.get('selectable')) is bool and
                 type(option.get('shortcut')) is str and type(option.get('active')) is bool,
                 'malformed offered option')
            offered.append({k: option[k] for k in ('index', 'text', 'selectable', 'shortcut', 'active')})
        projection = {'actor': self.expected_actor, 'instance': s['instance'], 'sequence': s['sequence'],
                      'conversation': s['conversation'], 'section': s['section'], 'offered': offered, 'bindings': b['rows']}
        self._current(); handle = object()
        self.contexts[handle] = {'projection': projection, 'started': row['started'], 'finished': self._now(),
                                 'actor_request': row['request']['id'], 'native_receipt': receipt, 'read_proof': proof}
        need(len(self.contexts) <= 128, 'context limit')
        return handle

    def frame(self, clip_handle, context_handle):
        """Decode retained clip's last frame; never records or captures new pixels."""
        self._current(); need(context_handle in self.contexts and len(self.frames) < 16, 'local context/frame capacity')
        receipt = self.clips.receipt(clip_handle); clip = receipt['clip']
        need(receipt['identity'] == self.initial and receipt['build'] == self.build and
             receipt['tier'] == ('SYNTHETIC' if self.synthetic else 'LOCAL_TRUSTED_CAPTURE'), 'local clip identity/tier')
        need(clip['window']['pid'] == self.initial['peer']['pid'] and clip['identity'] == self.initial,
             'clip window identity')
        interval = clip['host_interval']; context = self.contexts[context_handle]
        need(len(interval) == 2 and all(finite(x) for x in interval) and
             context['started'] <= interval[0] <= interval[1] <= self._now() and
             self._now()-interval[1] <= 30, 'clip not after native context or stale')
        binary = self.clips.recorder.binary; binary_row = self.clips.recorder.binary_row
        need(_file(binary, 256*1024**2) == binary_row, 'decoder changed')
        path = Path(clip['path']); need(_file(path, 256*1024**2) == clip['file'], 'clip changed')
        n = clip['evidence']['decoded_frames']; need(type(n) is int and 2 <= n <= 1800, 'decoded frame bound')
        raw = self.clips.recorder.runner([str(binary), '-nostdin', '-hide_banner', '-loglevel', 'error', '-xerror',
             '-i', str(path), '-vf', 'select=eq(n\\,'+str(n-1)+')', '-frames:v', '1', '-f', 'image2pipe',
             '-c:v', 'png', 'pipe:1'], cap=32*1024**2, timeout=15, check=self._current)
        info = decode_image(raw)
        need((info['width'], info['height']) == (clip['window']['width'], clip['window']['height']), 'frame dimensions')
        need(_file(path, 256*1024**2) == clip['file'] and _file(binary, 256*1024**2) == binary_row, 'clip/decoder changed')
        self.clips.receipt(clip_handle); self._current()
        handle = object(); self.frames[handle] = {'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
            'info': info, 'window': clip['window'], 'end': interval[1], 'context': context_handle,
            'clip_handle': clip_handle, 'clip': receipt}
        return handle

    def image(self, frame_handle):
        need(frame_handle in self.frames, 'local decoded frame required')
        return bytes(self.frames[frame_handle]['raw'])

    def review(self, frame_handle, *, reviewer, transcript):
        """Record literal agent visual judgment, never assert automated rendering proof."""
        self._current(); need(frame_handle in self.frames and len(self.reviews) < 64, 'local frame/review capacity')
        f = self.frames[frame_handle]; c = self.contexts[f['context']]
        need(type(reviewer) is str and 0 < len(reviewer) <= 128, 'named reviewing agent required')
        need(type(transcript) is dict and set(transcript) == {'frame_sha256', 'actor_label', 'actor_region',
             'choices', 'overlays', 'choice'}, 'structured rendered transcription required; no readiness boolean')
        need(transcript['frame_sha256'] == f['sha256'] and transcript['actor_label'] == self.label,
             'wrong reviewed frame or expected actor label')
        need(transcript['overlays'] == [], 'reviewed modal/overlay present')
        def region(box):
            need(type(box) is list and len(box) == 4 and all(type(v) is int for v in box) and
                 0 <= box[0] < box[2] <= f['info']['width'] and 0 <= box[1] < box[3] <= f['info']['height'],
                 'bounded visible region required')
        region(transcript['actor_region'])
        choice = transcript['choice']; need(type(choice) is int and 1 <= choice <= 6, 'one exact choice')
        rows = transcript['choices']; need(type(rows) is list and len(rows) == 6, 'all six offered positions transcribed')
        for expected, actual in zip(c['projection']['offered'], rows):
            need(type(actual) is dict and set(actual) == {'index', 'text', 'appearance', 'region', 'shortcut'} and
                 actual['index'] == expected['index'] and actual['text'] == expected['text'], 'visible choice differs')
            need(type(actual['shortcut']) is str and len(actual['shortcut']) <= 128, 'visible shortcut transcription required')
            appearance = 'ABSENT' if not expected['active'] else 'ENABLED' if expected['selectable'] else 'DISABLED'
            need(actual['appearance'] == appearance, 'choice eligibility appearance differs')
            if appearance == 'ABSENT': need(actual['region'] == [] and actual['shortcut'] == '', 'absent choice has visible region/shortcut')
            else: region(actual['region'])
        selected = c['projection']['offered'][choice-1]
        need(selected['active'] and selected['selectable'], 'selected choice not offered/selectable')
        need(self._now()-f['end'] <= 30, 'reviewed frame stale')
        record = {'version': 1, 'tier': 'SYNTHETIC' if self.synthetic else 'AGENT_REVIEWED',
            'reviewer': reviewer, 'transcript': copy.deepcopy(transcript), 'context': c['projection'],
            'identity': self.initial, 'build': self.build, 'frame_sha256': f['sha256'],
            'clip_path': f['clip']['clip']['path'], 'clip_file': f['clip']['clip']['file'],
            'clip_interval': f['clip']['clip']['host_interval'], 'window': f['window'],
            'source_sha256': self.source, 'judgment_verified': False}
        path = self.root/('review-'+secrets.token_hex(16)+'.json'); _write_new(path, record)
        handle = object(); self.reviews[handle] = {'frame': frame_handle, 'reviewer': reviewer,
            'transcript': copy.deepcopy(transcript), 'created': self._now(), 'tier': 'AGENT_REVIEWED',
            'judgment_verified': False, 'record': record, 'path': path}
        return handle

    def _preflight(self, review_handle, context_handle):
        self._current(); need(self.input_fault is None, 'dependent input quarantined: '+str(self.input_fault))
        need(review_handle in self.reviews and context_handle in self.contexts, 'local opaque evidence required')
        r = self.reviews[review_handle]; f = self.frames[r['frame']]; c = self.contexts[context_handle]
        need(_read_json(r['path']) == r['record'], 'rendered review record changed')
        need(c['projection'] == self.contexts[f['context']]['projection'], 'choice/actor/instance/binding changed')
        need(c['started'] >= r['created'] and c['started'] >= f['end'], 'native context must follow rendered review')
        need(self._now()-c['started'] <= 3 and self._now()-f['end'] <= 30 and self._now()-r['created'] <= 15,
             'native context/frame/review expired')
        self.clips.receipt(f['clip_handle'])
        choice = r['transcript']['choice']; p = c['projection']; row = p['bindings'][choice-1]
        need(row.get('choice') == choice and row.get('action') == 96+choice and row.get('status') == 'OBSERVED'
             and row.get('count') == len(row.get('inputs', [])), 'selected native binding unavailable')
        eligible = [v for v in row['inputs'] if type(v) is list and len(v) == 4 and v[0] == 1 and
                    type(v[1]) is int and 2 <= v[1] <= 7 and v[2] == 0 and (v[3] is False or v[3] == 'NOT_PRESENT')]
        need(len(eligible) == 1, 'one source-backed unmodified digit binding required')
        scan = eligible[0][1]
        need(r['transcript']['choices'][choice-1]['shortcut'] == str(scan-1), 'reviewed shortcut is not the mapped digit')
        for other in p['bindings']:
            if other.get('choice') != choice:
                need(eligible[0] not in other.get('inputs', []), 'digit also bound to another offered choice')
        state = self.backend.snapshot(self.initial['peer'], f['window'])
        need(self.backend.translate(scan, state) == scan, 'native digit translation changed')
        self._current(); need(self._now()-c['started'] <= 3, 'preflight exceeded native evidence budget')
        return scan, state

    def prepare(self, review_handle, context_handle):
        need(self.busy.acquire(blocking=False), 'input owner busy')
        try:
            need(len(self.tickets) < 64, 'input ticket capacity')
            scan, state = self._preflight(review_handle, context_handle)
            request = {'version': 1, 'id': secrets.token_hex(16), 'session': self.initial['session'],
                'provider': 'x4-host-dialog-input-v1', 'action': 'digit_choice', 'binding': self.source,
                'arguments': {'scan': scan, 'review': digest(self.reviews[review_handle]['transcript'])}}
            receipt = {'version': 1, 'id': request['id'], 'session': request['session'], 'digest': request_digest(request),
                'status': 'prepared', 'reason': 'agent-reviewed pixels plus sampled native/host gates',
                'observations': {'rendered_tier': 'AGENT_REVIEWED',
                    'evidence_tier': 'SYNTHETIC' if self.synthetic else 'LOCAL_AGENT_REVIEWED', 'game_result_verified': False}}
            journal = ActionJournal.create(self.root, request, receipt)
            handle = object(); self.tickets[handle] = {'journal': journal, 'review': review_handle,
                'context': context_handle, 'scan': scan, 'state': state, 'consumed': False, 'result': None}
            return handle
        finally: self.busy.release()

    def execute(self, ticket, fresh_context):
        need(self.busy.acquire(blocking=False), 'input owner busy')
        try:
            need(ticket in self.tickets, 'local input ticket required'); row = self.tickets[ticket]
            need(not row['consumed'] and fresh_context in self.contexts and
                 self.contexts[fresh_context]['actor_request'] != self.contexts[row['context']]['actor_request'],
                 'consumed ticket or fresh independent actor snapshot missing')
            scan, state = self._preflight(row['review'], fresh_context)
            need(scan == row['scan'] and state == row['state'], 'prepared Windows binding changed')
            row['consumed'] = True
            outer = self
            class Transport:
                def execute(self, request):
                    inserted = None; attempted = False; reason = 'global input insertion unverified'; post = None; clean = False
                    try:
                        outer._current()
                        need(outer.backend.snapshot(outer.initial['peer'], state['window']) == state, 'final foreground changed')
                        need(outer._now()-outer.contexts[fresh_context]['started'] <= 3, 'final native deadline')
                        attempted = True; row['attempt_age'] = outer._now(); inserted = outer.backend.send_pair(scan)
                        need(type(inserted) is dict and set(inserted) == {'inserted', 'winerror'} and
                             type(inserted['inserted']) is int and 0 <= inserted['inserted'] <= 2 and
                             type(inserted['winerror']) is int, 'malformed insertion result')
                        post = outer.backend.snapshot(outer.initial['peer'], state['window'])
                        need(post == state, 'post-input foreground/layout changed')
                        outer._current()
                        clean = inserted['inserted'] == 2
                    except Exception as exc: reason = str(exc)[:512]
                    if attempted and not clean:
                        outer.input_fault = 'partial/unknown insertion or post-dispatch state; owned-key reconciliation required'
                    result = {'version': 1, 'id': request['id'], 'session': request['session'], 'digest': request_digest(request),
                        'status': 'running' if clean else 'unknown',
                        'reason': reason, 'observations': {'attempted': attempted, 'insertion': inserted,
                        'rendered_tier': 'AGENT_REVIEWED', 'focus_scope': 'SAMPLED_NOT_ATOMIC_GLOBAL_STREAM',
                        'evidence_tier': 'SYNTHETIC' if outer.synthetic else 'LOCAL_AGENT_REVIEWED',
                        'game_result_verified': False, 'runtime_qualified': False, 'automatic_replay': False,
                        'dependent_input_blocked': outer.input_fault is not None,
                        'key_cleanup': 'NOT_REQUIRED_BY_REPORTED_PAIR' if clean else 'UNRESOLVED_NO_AUTOMATIC_COMPENSATION'}}
                    row['result'] = copy.deepcopy(result); return result
            return row['journal'].execute(Transport())
        finally: self.busy.release()

    def receipt(self, ticket):
        need(ticket in self.tickets, 'local input ticket required')
        return copy.deepcopy(self.tickets[ticket]['result'])

    def outcome(self, ticket, actor_handle):
        """Independent later native observations; not a causal or rendered verdict."""
        self._current(); need(ticket in self.tickets and actor_handle in self.actors, 'local outcome handles required')
        t=self.tickets[ticket];r=self.actors[actor_handle]
        need(t['result'] and t['result']['observations']['attempted'] is True and
             r['started']>=t['attempt_age'] and
             r['request']['id']!=self.contexts[t['context']]['actor_request'] and
             self._now()-r['started']<=3, 'fresh post-input actor snapshot required')
        receipt=self.client.status(r['request']['id'])
        if receipt['status']!='completed':return {'status':'PENDING','selection_verified':False}
        o=receipt.get('observations',{});s=o.get('snapshot',{})
        need(o.get('proof_scope')=='CONVERSATION_SNAPSHOT_ONLY' and o.get('read_only') is True and
             o.get('gameplay_action_attempted') is False and s.get('request')==r['request']['id'] and
             s.get('stamp')==self._stamp() and s.get('stable') is True and
             s.get('status') in ('INACTIVE_OBSERVED','ACTOR_OBSERVED_SECTION_UNKNOWN','ACTOR_AND_HISTORICAL_SECTION_OBSERVED'),
             'post-input native snapshot unqualified')
        collector=ReadEvidence(self.client,synthetic=self.synthetic);h=collector.collect('dialog_probe');proof=collector.receipt(h)
        need(proof['status']=='OK' and proof['identity']==self.initial and proof['build']==self.build,'outcome read binding')
        model=_decode(proof['capture']['payload']);need(model.get('stamp')==self._stamp(),'outcome stamp')
        self._current();need(self._now()-r['started']<=3,'post-input sample deadline')
        value={'status':'POST_INPUT_NATIVE_OBSERVATIONS','scope':'SAMPLED_NOT_CAUSAL_NOT_RENDERED',
            'tier':'SYNTHETIC' if self.synthetic else 'MACHINE_OBSERVED','input_id':t['journal'].request['id'],
            'actor_receipt':receipt,'dialog_read':proof,'selection_verified':False,'gameplay_effect_verified':False}
        _write_new(t['journal'].path/('outcome-'+r['request']['id']+'.json'),value)
        return value

    def _release_state(self, original):
        self._current(); need(self.input_fault is not None and original['consumed'] and original['result'],
                             'no local quarantined input attempt')
        need(original['journal']._history()[-1] == original['result'], 'original dispatch evidence changed')
        o = original['result']['observations']; insertion = o.get('insertion')
        need(o.get('attempted') is True and type(insertion) is dict and type(insertion.get('inserted')) is int and
             insertion['inserted'] == 1, 'exactly one owned down event must be reported; unknown counts cannot confer ownership')
        need(self._now()-original['attempt_age'] <= 5, 'owned partial-event release window expired')
        state = self.backend.snapshot(self.initial['peer'], original['state']['window'], owned_scan=original['scan'])
        # Only this one recorded digit may be down. No modifier/foreign key cleanup.
        need(state.get('down_bits') in ([], [0x30+original['scan']-1]), 'foreign key state')
        need({k:v for k,v in state.items() if k!='down_bits'} ==
             {k:v for k,v in original['state'].items() if k!='down_bits'}, 'original foreground/layout changed')
        need(self.backend.translate(original['scan'],state) == original['scan'], 'owned scan translation changed')
        return state

    def prepare_release(self, original_ticket):
        """Explicit separate request, never an automatic compensating action."""
        need(self.busy.acquire(blocking=False), 'input owner busy')
        try:
            need(original_ticket in self.tickets, 'foreign original input ticket')
            original = self.tickets[original_ticket]
            need(not original.get('release_reserved'), 'release request already reserved; never repeat')
            state = self._release_state(original)
            request = {'version':1,'id':secrets.token_hex(16),'session':self.initial['session'],
                'provider':'x4-host-dialog-input-v1','action':'owned_key_release','binding':self.source,
                'arguments':{'original':original['journal'].request['id'],'scan':original['scan']}}
            receipt = {'version':1,'id':request['id'],'session':request['session'],'digest':request_digest(request),
                'status':'prepared','reason':'explicit release of one reported owned down event only',
                'observations':{'game_result_verified':False,'cleanup_verified':False}}
            original['release_reserved'] = True
            journal = ActionJournal.create(self.root,request,receipt)
            handle = object(); self.releases[handle] = {'original':original_ticket,'journal':journal,'state':state,
                                                      'consumed':False,'result':None}
            return handle
        finally:self.busy.release()

    def execute_release(self, release_ticket):
        need(self.busy.acquire(blocking=False), 'input owner busy')
        try:
            need(release_ticket in self.releases, 'foreign release ticket')
            row=self.releases[release_ticket];need(not row['consumed'], 'release consumed; no repeat')
            original=self.tickets[row['original']]
            need(self._release_state(original)==row['state'], 'prepared release state changed')
            row['consumed']=True;outer=self
            class ReleaseTransport:
                def execute(self, request):
                    attempted=False;inserted=None;post=None;clean=False;reason='owned keyup not yet observed'
                    try:
                        need(outer._release_state(original)==row['state'],'final release preflight changed')
                        attempted=True;inserted=outer.backend.send_release(original['scan'])
                        need(type(inserted)is dict and set(inserted)=={'inserted','winerror'} and
                             type(inserted['inserted'])is int and inserted['inserted']==1 and
                             type(inserted['winerror'])is int,'owned keyup insertion uncertain')
                        post=outer.backend.snapshot(outer.initial['peer'],original['state']['window'])
                        need(post.get('down_bits')==[] and post==original['state'],'keyup post-state not observed')
                        outer._current();clean=True;outer.input_fault=None
                        reason='one keyup inserted; no down bits observed; sampled cleanup only'
                    except Exception as exc:reason=str(exc)[:512]
                    result={'version':1,'id':request['id'],'session':request['session'],'digest':request_digest(request),
                        'status':'running' if clean else 'unknown','reason':reason,'observations':{
                        'attempted':attempted,'insertion':inserted,'original':original['journal'].request['id'],
                        'scan':original['scan'],'cleanup_scope':'ONE_OWNED_KEYUP_SAMPLED_NO_DOWN_BITS',
                        'cleanup_verified':False,'dependent_input_blocked':outer.input_fault is not None,
                        'game_result_verified':False,'focus_scope':'SAMPLED_NOT_ATOMIC_GLOBAL_STREAM',
                        'evidence_tier':'SYNTHETIC' if outer.synthetic else 'LOCAL_AGENT_REVIEWED'}}
                    row['result']=copy.deepcopy(result);return result
            return row['journal'].execute(ReleaseTransport())
        finally:self.busy.release()
