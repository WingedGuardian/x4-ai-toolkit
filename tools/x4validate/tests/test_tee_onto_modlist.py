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
