"""Actual adapter counters, native assembly and wire; synthetic native fixtures only."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent
UI = HERE.parents[2] / "mods/x4_toolkit_helper/ui"


def fixture(name):
    spec = importlib.util.spec_from_file_location("counter_fixture_" + name, HERE / ("test_live" + name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CASES = [
    ("menu", "build", "api", {"tickets": 128}),
    ("options", "build", "api", {"tickets": 128}),
    ("orders", "build", "api", {"submissions": 128, "cancellations": 128}),
    ("timecontrol", "make", "api", {"tickets": 128}),
    ("playernavigation", "make", "api", {"tickets": 256}),
    ("menupages", "runtime", "pager", {"attempts": 128}),
    ("playerstate", "build", "api", {"attempts": 256}),
    ("buildtasks", "build", "api", {"attempts": 128}),
    ("people", "build", "api", {"attempts": 128}),
    ("equipmentmods", "build", "api", {"reads": 4096}),
]


@pytest.mark.parametrize("name,builder,api,limits", CASES)
def test_actual_getters_are_copied_local_zeroes_without_callbacks(name, builder, api, limits):
    r = getattr(fixture(name), builder)()
    # Every dependency call reachable from Lua would raise. Getter lexical reads
    # still work, without even sampling a clock or public-current callback.
    r.execute("local getter=" + api + ".capacity;debug.sethook(function(e) local i=debug.getinfo(2,'f');"
              "if e=='call' and i.func~=getter and i.func~=debug.sethook then error('getter called another function')end end,'c');"
              "local out=getter();debug.sethook();capacity_result=out")
    out = r.globals().capacity_result
    assert out.available is True
    assert set(out.budgets.keys()) == set(limits)
    for key, limit in limits.items():
        assert out.budgets[key].used == 0 and out.budgets[key].limit == limit
        out.budgets[key].used = 99999
    again = r.globals()[api].capacity()
    assert all(again.budgets[key].used == 0 for key in limits)


@pytest.mark.parametrize("name,expression", [
    ("menu", "api.prepare('ship','ship_test_macro',expected)"),
    ("options", "prep()"),
    ("timecontrol", "api.prepare('start','ship')"),
    ("playernavigation", "api.prepare('target','ship','target')"),
])
def test_successful_action_preparation_charges_only_ticket(name, expression):
    f = fixture(name)
    r = (f.make if name in ("timecontrol", "playernavigation") else f.build)()
    assert r.eval(expression).status == "PREPARED"
    assert r.eval("api.capacity().budgets.tickets.used") == 1
    r.execute("for i=1,3 do api.capacity() end")
    assert r.eval("api.capacity().budgets.tickets.used") == 1


def test_order_submission_and_cancellation_are_separate_actual_histories():
    f = fixture("orders")
    r, p, out = f.submitted()
    c = r.globals().api.prepare_cancel(p.ticket, out.order_id)
    assert c.status == "PREPARED"
    cap = r.globals().api.capacity()
    assert cap.budgets.submissions.used == cap.budgets.cancellations.used == 1
    assert r.globals().api.prepare_cancel("foreign", out.order_id).status == "REFUSED"
    assert r.globals().api.capacity().budgets.cancellations.used == 1


@pytest.mark.parametrize("name", ["playerstate", "buildtasks", "people"])
def test_capture_cached_nonce_and_pages_do_not_charge_again(name):
    f = fixture(name)
    r = f.build()
    first = f.capture(r)
    assert first.status == "OBSERVED"
    assert r.eval("api.capacity().budgets.attempts.used") == 1
    assert f.capture(r).status == "OBSERVED"
    if name == "people":
        assert r.globals().api.page(first.capture_id, 0).status == "OBSERVED"
    else:
        assert f.page(r, first).status in ("PAGE", "OBSERVED")
    c = r.globals().api.capacity()
    assert c.budgets.attempts.used == 1 and c.cache_present is True
    assert c.cached_context == "LAST_SUCCESSFUL_CAPTURE_NOT_CURRENT_READBACK"
    c.cached_stamp.session = "mutated"
    assert r.globals().api.capacity().cached_stamp.session != "mutated"


def test_menu_capture_failure_consumes_attempt_and_page_does_not():
    f = fixture("menupages")
    r = f.runtime()
    f.capture(r)
    assert r.eval("pager.capacity().budgets.attempts.used") == 1
    r.globals().pager.capture(f.ID)
    assert r.eval("pager.capacity().budgets.attempts.used") == 1
    r.execute("pager=pagerfactory({observe=function()error('synthetic failed model')end})")
    assert r.globals().pager.capture("b" * 32).status == "REFUSED"
    assert r.eval("pager.capacity().budgets.attempts.used") == 1


@pytest.mark.parametrize("name", ["playerstate", "buildtasks", "people"])
def test_eligible_capture_failure_still_consumes_actual_attempt(name):
    f = fixture(name)
    r = f.build()
    r.execute("stamp=nil")
    assert f.capture(r).status != "OBSERVED"
    assert r.eval("api.capacity().budgets.attempts.used") == 1


def test_equipment_read_invalid_kind_consumes_attempt_before_validation():
    f = fixture("equipmentmods")
    r = f.build()
    assert f.read(r, "invalid").status == "REFUSED"
    assert r.eval("api.capacity().budgets.reads.used") == 1


@pytest.mark.parametrize("name", ["playerstate", "buildtasks", "people", "equipmentmods"])
def test_dispose_sentinel_is_unknown_not_fabricated_usage(name):
    r = fixture(name).build()
    r.globals().api.dispose()
    c = r.globals().api.capacity()
    key = "reads" if name == "equipmentmods" else "attempts"
    assert c.available is False and c.budgets[key].used == "UNKNOWN"


def assembly(actual=True):
    f = fixture("nativeassembly")
    r = f.runtime.__wrapped__()
    if actual:
        for name, _, _, _ in CASES:
            r.globals().modules[name] = r.execute(f.source(name))
    r.globals().wire = r.execute(f.source("actionwire"))()
    r.execute("deps.encode=wire.encode")
    f.construct(r)
    return r


def test_ten_actual_constructors_and_getters_through_encoded_assembly_route():
    r = assembly()
    assert r.eval("calls") == 0
    status, payload = r.execute("return assembly.read('native_adapter_capacity',{})")
    assert status == "OK", payload
    out = json.loads(payload)
    assert out["adapter_count"] == len(out["adapters"]) == 10
    assert out["budget_count"] == sum(len(a["budgets"]) for a in out["adapters"].values()) == 11
    assert not out["atomic"] and not out["reserved"] and not out["future_fit_verified"]
    assert len(payload.encode()) < 32000
    assert r.eval("calls") == 0
    assert r.execute("return assembly.read('native_adapter_capacity',{})") == (status, payload)
    r.execute("assembly.dispose()")
    assert r.execute("return assembly.read('native_adapter_capacity',{})")[0] == "ERR"


@pytest.mark.parametrize("args", ["{'extra'}", "{[2]='sparse'}", "{false}"])
def test_capacity_route_exact_zero_arguments(args):
    r = assembly()
    assert r.execute("return assembly.read('native_adapter_capacity'," + args + ")")[0] == "ERR"
    assert r.eval("calls") == 0


@pytest.mark.parametrize("mutation", [
    "v.budgets={}", "v.budgets.extra={used=0,limit=1}",
    "v.budgets.tickets.used=-1", "v.budgets.tickets.used=129",
    "v.budgets.tickets.used=0/0", "v.budgets.tickets.used='UNKNOWN'",
    "v.budgets.tickets.limit=math.huge", "v.budgets.tickets.limit=true",
])
def test_malformed_or_missing_counter_never_becomes_partial_success(mutation):
    f = fixture("nativeassembly")
    r = f.runtime.__wrapped__()
    r.execute("modules.menu.new=function()local t=fake('menu');local old=t.capacity;t.capacity=function()local v=old();"
              + mutation + ";return v end;return t end")
    f.construct(r)
    assert r.execute("return assembly.read('native_adapter_capacity',{})")[0] == "ERR"


def test_identity_loss_during_counter_read_refuses_encoded_evidence():
    f = fixture("nativeassembly")
    r = f.runtime.__wrapped__()
    r.execute("modules.menu.new=function()local t=fake('menu');local old=t.capacity;t.capacity=function()current=nil;return old()end;return t end")
    f.construct(r)
    assert r.execute("return assembly.read('native_adapter_capacity',{})")[0] == "ERR"


def test_actual_router_boundpipe_readcapture_costs_one_read_and_no_adapter_or_sdk_record(tmp_path):
    from test_livebound import Pipe, Link
    from x4validate._livebound import BoundPipe
    from x4validate._liveautomation import AutomationClient
    r = assembly()
    r.globals().corefactory = r.execute((UI / 'live_actions.lua').read_text(encoding="utf-8"))
    r.globals().routerfactory = r.execute((UI / 'live_automation.lua').read_text(encoding="utf-8"))
    r.execute("""
      current={provider='x4-live-session-v1',revision=string.rep('c',64),
        session=string.rep('a',32),generation=string.rep('b',32),world_token=string.rep('d',32)}
      core=corefactory()
      router=routerfactory{session={current=function()return current end},registry=core,wire=wire,
        read=assembly.read,read_verbs=assembly.read_verbs,build='12345678',
        age=function()return 17 end,ui_authorize=function()return true end}
    """)
    pipe = Pipe(r)
    client = AutomationClient(BoundPipe(pipe, Link(), '12345678'), tmp_path)
    reply = client.query('native_adapter_capacity')
    assert reply.status == 'OK'
    out = json.loads(reply.payload)
    assert out['adapter_count'] == 10 and out['budget_count'] == 11
    assert all(b['used'] == 0 for a in out['adapters'].values() for b in a['budgets'].values())
    assert r.eval('core.capacity().requests_used') == 0
    assert r.eval('router.capacity().requests_used') == 0
    assert r.eval('router.capacity().reads_used') == 1
    assert r.eval('router.capacity().retained_charge_bytes') == 32064
    assert len(pipe.calls) > 2  # Actual generic transport pages, no extra capture allocation.
    assert client.last_read['transport_complete'] and not client.last_read['atomic']
    assert r.eval('calls') == 0
    assert client.query('native_adapter_capacity').payload == reply.payload
    assert r.eval('router.capacity().reads_used') == 2
    assert r.eval('core.capacity().requests_used') == 0


def test_dense_used_limits_and_historical_stamps_cross_actual_wire_encoder():
    f = fixture('nativeassembly')
    r = f.runtime.__wrapped__()
    r.execute("""original_fake=fake;function fake(name)
      local t=original_fake(name);local old=t.capacity
      t.capacity=function()local v=old();for _,b in pairs(v.budgets)do b.used=b.limit end
        v.cached_stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
          generation=string.rep('c',32),world_token=string.rep('d',128)}
        v.cached_context='LAST_SUCCESSFUL_CAPTURE_NOT_CURRENT_READBACK';return v end
      return t end""")
    r.globals().wire = r.execute(f.source('actionwire'))()
    r.execute('deps.encode=wire.encode')
    f.construct(r)
    status, payload = r.execute("return assembly.read('native_adapter_capacity',{})")
    assert status == 'OK', payload
    out = json.loads(payload)
    assert len(payload.encode()) < 32000 and not out['future_fit_verified']
    assert all(b['used'] == b['limit'] for a in out['adapters'].values() for b in a['budgets'].values())

