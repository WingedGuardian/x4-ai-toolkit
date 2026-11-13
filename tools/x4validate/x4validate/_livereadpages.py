"""Strict immutable transport pages for large session-bound legacy reads."""
from __future__ import annotations
import hashlib
import math
import re
from ._livebound import _decode
from ._livesession import SessionUnavailable
from ._livepipe import Reply

BASE={"version","kind","capture","source_verb","source_status","bytes","pages","chunk_bytes",
      "started_age","finished_age","atomic"}


def query_capture(bound,nonce,verb,args):
    """Shared actual/replay paging path; no journals, nonce generation or retries."""
    first=bound.ask("read_capture",nonce,verb,*args)
    if first.status!="OK":
        raise SessionUnavailable("read capture unavailable")
    capture=ReadCapture(first.payload,capture=nonce,verb=verb)
    if capture.header["finished_age"]>bound.last_envelope["age"]:
        raise SessionUnavailable("source capture interval exceeds its qualified reply age")
    reply=first
    for index in range(capture.header["pages"]):
        reply=bound.ask("read_page",nonce,str(index))
        if reply.status!="OK":
            raise SessionUnavailable("read page unavailable; capture incomplete")
        capture.add(reply.payload)
    result=capture.finish()
    return Reply(reply.seq,result["source_status"],result["payload"]),result


def _header(value,kind):
    keys=BASE if kind=="READ_CAPTURE" else BASE|{"index","offset","length","hex"}
    if (not isinstance(value,dict) or set(value)!=keys
            or type(value["version"]) is not int or value["version"]!=1 or value["kind"]!=kind
            or not isinstance(value["capture"],str) or not re.fullmatch("[0-9a-f]{32}",value["capture"])
            or not isinstance(value["source_verb"],str) or not 0<len(value["source_verb"])<=64
            or value["source_status"] not in ("OK","ABSENT","ERR")
            or type(value["bytes"]) is not int or not 0<=value["bytes"]<=32000
            or type(value["pages"]) is not int or value["pages"]!=max(1,(value["bytes"]+999)//1000)
            or type(value["chunk_bytes"]) is not int or value["chunk_bytes"]!=1000
            or value["atomic"] is not False):
        raise SessionUnavailable("invalid read capture header")
    for key in ("started_age","finished_age"):
        age=value[key]
        if type(age) not in (int,float) or not 0<=age<=2**53-1 or not math.isfinite(age):
            raise SessionUnavailable("invalid source read age")
    if value["finished_age"]<value["started_age"]:
        raise SessionUnavailable("source clock rolled back")
    return {k:value[k] for k in BASE if k!="kind"}


class ReadCapture:
    def __init__(self,payload,*,capture,verb):
        self.header=_header(_decode(payload),"READ_CAPTURE")
        if self.header["capture"]!=capture or self.header["source_verb"]!=verb:
            raise SessionUnavailable("read capture does not match request")
        self.parts=[];self.failed=False;self.finished=False

    def add(self,payload):
        if self.failed or self.finished:
            raise SessionUnavailable("read capture already failed or finished")
        try:
            page=_decode(payload)
            if _header(page,"READ_PAGE")!=self.header:
                raise SessionUnavailable("mixed read captures")
            index=len(self.parts)
            length=min(1000,self.header["bytes"]-index*1000)
            if (type(page["index"]) is not int or page["index"]!=index or index>=self.header["pages"]
                    or type(page["offset"]) is not int or page["offset"]!=index*1000
                    or type(page["length"]) is not int or page["length"]!=length
                    or not isinstance(page["hex"],str) or len(page["hex"])!=length*2
                    or not re.fullmatch("[0-9a-f]*",page["hex"])):
                raise SessionUnavailable("omitted, duplicate or malformed read page")
            self.parts.append(bytes.fromhex(page["hex"]))
        except Exception as exc:
            self.failed=True
            if isinstance(exc,SessionUnavailable):raise
            raise SessionUnavailable("read page unavailable") from exc

    def finish(self):
        if self.failed or self.finished:
            raise SessionUnavailable("read capture already failed or finished")
        self.finished=True
        try:
            if len(self.parts)!=self.header["pages"]:
                raise SessionUnavailable("read capture incomplete")
            raw=b"".join(self.parts)
            if len(raw)!=self.header["bytes"]:
                raise SessionUnavailable("read capture byte mismatch")
            return dict(self.header,payload=raw.decode("utf-8"),transport_complete=True,
                        sha256=hashlib.sha256(raw).hexdigest(),digest_scope="HOST_REASSEMBLED_BYTES")
        except Exception as exc:
            self.failed=True
            if isinstance(exc,SessionUnavailable):raise
            raise SessionUnavailable("invalid source read encoding") from exc

