"""Explicit automation CLI. Does not launch/restart X4 or choose fallback modes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from ._liveautomation import AutomationClient, _id
from ._liveactions import ActionRefused, ActionUncertain, _read_json
from ._livesession import SessionUnavailable
from ._livepipe import LiveQueryUnavailable, LiveQueryDegraded

RESERVED_VERBS=frozenset({"bound","session_connect","ui_state",
                         "action_prepare","action_execute","action_status","read_capture","read_page"})


def main(argv=None, *, open_pipe=None, connect=None):
    parser=argparse.ArgumentParser(prog="x4live automation",
        description="Experimental agent automation; use a disposable save. No launch or implicit retries.")
    parser.add_argument("--journal",required=True,type=Path,help="existing dedicated local journal root")
    parser.add_argument("--image",required=True,type=Path,help="expected exact X4 executable")
    parser.add_argument("--profile-id",required=True,help="expected explicit process personalfolderid")
    parser.add_argument("--build",required=True,help="expected loaded automation bundle SHA256")
    parser.add_argument("--revision",required=True,help="expected public MD session revision")
    parser.add_argument("--mode",required=True,choices=("fresh","resume"),help="explicit load handshake mode")
    parser.add_argument("--pipe")
    parser.add_argument("--timeout",type=float,default=15,
        help="seconds to wait for each reply (0-120, default: %(default)s); the wait for the "
             "game to connect is the larger of this and 15 s")
    sub=parser.add_subparsers(dest="operation",required=True)
    sub.add_parser("identity",help="qualify UI and world identity only")
    p=sub.add_parser("query",help="session-bound read, never an action")
    p.add_argument("verb");p.add_argument("arguments",nargs="*")
    p=sub.add_parser("prepare",help="reserve exact request durably, then prepare once")
    p.add_argument("request",type=Path,help="JSON: id,provider,action,arguments,binding; session is measured")
    for verb in ("execute","status"):
        p=sub.add_parser(verb,help="execute once" if verb=="execute" else "reconcile without resending")
        p.add_argument("request_id")
    args=parser.parse_args(argv)
    if not 0<args.timeout<=120:
        parser.error("timeout must be between zero and 120 seconds")
    # Refuse invalid/hidden write routes BEFORE opening the pipe or allocating metadata.
    if args.operation=="query" and (args.verb in RESERVED_VERBS or args.verb in ("pause","unpause")):
        print("error: dedicated action/session operation required",file=sys.stderr)
        return 2
    if open_pipe is None:
        from ._livecli import _live_open
        open_pipe=_live_open
    connect=connect or AutomationClient.connect
    print("EXPERIMENTAL x4live automation: use a disposable save. Session metadata/journals may be written.",
          file=sys.stderr)
    from ._livetrees import protected_roots, unchecked_roots
    checked=sorted(protected_roots(image=args.image))
    missing=unchecked_roots(image=args.image)
    print("journal check: the journal may not overlap "+(", ".join(checked) or "nothing determinable")
          +("; not determined, so not checked: "+", ".join(missing)+" (configure them in x4-paths.env)" if missing else ""),
          file=sys.stderr)
    if args.operation=="execute":
        print("WRITE: one previously prepared action; no automatic retry on lost reply.",file=sys.stderr)
    receipt=None
    dispatch_returned=False
    def failed(status,exc):
        report=dict(status=status,reason=str(exc))
        if args.operation=="execute":
            report.update(request_id=args.request_id,journal=str(args.journal),
                          returned_receipt=receipt,may_have_acted=(status=="UNKNOWN"))
        print(json.dumps(report,sort_keys=True,allow_nan=False))
        return 3 if status=="UNKNOWN" else 2
    try:
        spec=None
        if args.operation=="prepare":
            spec=_read_json(args.request)
            if not isinstance(spec,dict) or set(spec)!={"id","provider","action","arguments","binding"}:
                raise ActionRefused("request spec requires exactly id/provider/action/arguments/binding")
            AutomationClient._provider_access(spec,None)
        elif args.operation in ("execute","status"):
            pending=_read_json(args.journal/"requests"/(_id(args.request_id)+".json"))
            AutomationClient._provider_access(pending,None)
        with open_pipe(args.pipe,args.timeout) as pipe:
            client=connect(pipe,args.journal,image=args.image,profile_id=args.profile_id,
                           build=args.build,revision=args.revision,mode=args.mode)
            if args.operation=="identity":
                result=client.output(operation="identity")
                code=0
            elif args.operation=="query":
                reply=client.query(args.verb,*args.arguments)
                result=client.output(operation="query",status=reply.status,payload=reply.payload)
                code=0 if reply.status=="OK" else 2
            else:
                if args.operation=="prepare":
                    request=dict(spec,version=1,session=client.identity()["session"])
                    receipt=client.prepare(request)
                else:
                    receipt=getattr(client,args.operation)(args.request_id)
                    dispatch_returned=args.operation=="execute"
                result=client.output(operation=args.operation,receipt=receipt)
                code=0 if receipt["status"] in ("prepared","completed") else (
                    4 if receipt["status"] in ("preparing","running") else (
                    2 if receipt["status"]=="refused" else 3))
            print(json.dumps(result,sort_keys=True,allow_nan=False))
            return code
    except (ActionRefused,SessionUnavailable,LiveQueryUnavailable) as exc:
        return failed("UNKNOWN" if dispatch_returned else "UNAVAILABLE",exc)
    except (ActionUncertain,LiveQueryDegraded) as exc:
        return failed("UNKNOWN",exc)
    except (OSError,ValueError,TypeError) as exc:
        return failed("UNKNOWN" if dispatch_returned else "UNAVAILABLE",exc)

