"""4.1.1: a `tee` onto the profile mod list follows the same exemption as a redirect or `cp`.

The profile `content.xml` (the mod enable/disable list) is written without confirmation by a
redirect, `cp`, or `tee < file`. A PIPELINE into `tee` (`echo x | tee <profile>/content.xml`)
is two segments, and the exemption required exactly one, so it asked. The guard only DECIDES
here; nothing runs the commands. Fixture roots only (test_x4guard_check.sandbox)."""
from __future__ import annotations

import pytest
from test_x4guard_check import check, sandbox  # noqa: F401  (pytest fixture)


def _decide(env, command):
    rc, v, err = check(env, "--kind", "shell", "--shell", "bash", "--command", command)
    assert rc == 0 and not v["inert"], (v, err)
    return v


def _files(tmp):
    prof = tmp / "profile"
    for f in (prof / "content.xml", prof / "config.xml", tmp / "src.xml"):
        f.write_text("<content/>\n", encoding="utf-8")
    return prof, (tmp / "src.xml").as_posix()


@pytest.mark.parametrize("shape", [
    "echo x | tee '{p}'",
    "printf '<content/>' | tee '{p}' > /dev/null",
    "cat '{src}' | tee '{p}'",
    "cat '{src}' | tee -a '{p}'",
])
def test_a_pipeline_into_tee_onto_the_mod_list_is_not_asked(sandbox, shape):
    tmp, _tk, env = sandbox
    prof, src = _files(tmp)
    v = _decide(env, shape.format(p=(prof / "content.xml").as_posix(), src=src))
    assert v["decision"] in ("allow", "advise"), v


@pytest.mark.parametrize("shape", ["echo x > '{p}'", "cp '{src}' '{p}'", "tee '{p}' < '{src}'"])
def test_TWIN_the_single_command_forms_were_already_exempt(sandbox, shape):
    tmp, _tk, env = sandbox
    prof, src = _files(tmp)
    v = _decide(env, shape.format(p=(prof / "content.xml").as_posix(), src=src))
    assert v["decision"] in ("allow", "advise"), v


@pytest.mark.parametrize("shape", [
    "echo x | tee '{p}'",                                  # another profile file keeps its ask
    "echo x | tee '{m}' '{p}'",                            # the mod list AND another file
    "echo x | tee '{m}' | tee '{p}'",                      # a second tee onto another file
    "rm -f '{s}' | tee '{m}'",                             # the producer itself writes or deletes
    "echo $(rm '{p}') | tee '{m}'",                        # a substitution in the producer
])
def test_TWIN_a_pipeline_that_touches_anything_else_keeps_its_verdict(sandbox, shape):
    tmp, _tk, env = sandbox
    prof, src = _files(tmp)
    (prof / "save").mkdir(exist_ok=True)
    cmd = shape.format(p=(prof / "config.xml").as_posix(), m=(prof / "content.xml").as_posix(),
                       s=(prof / "save" / "a.xml.gz").as_posix())
    v = _decide(env, cmd)
    assert v["decision"] in ("ask", "deny"), (cmd, v)


@pytest.mark.parametrize("shape", [
    "echo '{s}' | xargs tee '{m}'",          # 4.1.1 review: xargs appends the save to tee's files -> truncated
    "xargs tee '{m}' < '{s}'",               # the single-segment form
    "echo x | sudo tee '{m}'",
    "echo x | env X=1 tee '{m}'",
    "echo x | timeout 5 tee '{m}'",
])
def test_TWIN_a_wrapped_tee_onto_the_mod_list_keeps_its_confirmation(sandbox, shape):
    tmp, _tk, env = sandbox
    prof, _src = _files(tmp)
    save = prof / "save" / "a.xml.gz"
    save.parent.mkdir(exist_ok=True)
    save.write_bytes(b"opaque save fixture")
    v = _decide(env, shape.format(m=(prof / "content.xml").as_posix(), s=save.as_posix()))
    assert v["decision"] in ("ask", "deny"), (shape, v)


def test_a_tee_that_reads_a_save_and_writes_only_the_mod_list_is_advised(sandbox):
    tmp, _tk, env = sandbox
    prof, _src = _files(tmp)
    save = prof / "save" / "a.xml.gz"
    save.parent.mkdir(exist_ok=True)
    save.write_bytes(b"opaque save fixture")
    v = _decide(env, "cat '{}' | tee '{}'".format(save.as_posix(), (prof / "content.xml").as_posix()))
    assert v["decision"] in ("allow", "advise"), v
    assert save.read_bytes() == b"opaque save fixture"      # decided only; nothing ran
