"""On-foot null handles never reach object getters; synthetic Lua boundary."""
import pytest
from test_modlua_rearm import live,ask,g

def onfoot(value='"0ULL"'):
    r=live()
    r.execute("""
        fake_C.GetPlayerOccupiedShipID=function() return VALUE end
        zero_reads=0;all_reads=0
        local old=GetComponentData
        GetComponentData=function(id,...)
            all_reads=all_reads+1
            if tostring(id)=="0" or tostring(id)=="0ULL" then
                zero_reads=zero_reads+1;error("invalid ship zero")
            end
            return old(id,...)
        end
        fake_C.GetObjectPositionInSector=function(id)
            all_reads=all_reads+1
            if tostring(id)=="0" or tostring(id)=="0ULL" then
                zero_reads=zero_reads+1;error("invalid position zero")
            end
            return {x=1,y=2,z=3,yaw=0,pitch=0,roll=0}
        end
        fake_C.GetComponentClass=function(id)
            all_reads=all_reads+1
            if tostring(id)=="0" or tostring(id)=="0ULL" then
                zero_reads=zero_reads+1;error("invalid class zero")
            end
            return "ship_l"
        end
    """.replace("VALUE",value))
    return r

@pytest.mark.parametrize("value",['"0ULL"',"0"])
def test_onfoot_player_reports_absent_ship_without_object_getters(value):
    r=onfoot(value);reply=ask(r,1,"player")
    assert reply.status=="OK"
    assert "occupiedship=0ULL" in reply.fields
    assert "occupiedship_present=false" in reply.fields
    assert "playerid=77ULL" in reply.fields
    assert not any(f.startswith(("pos=","rot=","class=","sectorid="))for f in reply.fields)
    assert g(r,"zero_reads")==0 and g(r,"all_reads")==0
    # Reporting absence must not issue an inspectable zero handle.
    component=ask(r,2,"component","0ULL")
    assert component.status=="ERR" and "not issued by this session" in component.payload
    assert g(r,"zero_reads")==0

@pytest.mark.parametrize("verb",["probe","containerprobe"])
def test_onfoot_probes_report_unavailable_without_zero_reads(verb):
    r=onfoot()
    reply=ask(r,1,verb)
    assert g(r,"zero_reads")==0 and g(r,"all_reads")==0
    assert "no occupied ship" in reply.payload.lower()
    assert reply.status==("OK" if verb=="probe" else "ABSENT")

def test_occupied_ship_still_issues_and_reads_exact_object():
    r=onfoot('"1046331ULL"')
    reply=ask(r,1,"player")
    assert reply.status=="OK" and "occupiedship_present=true" in reply.fields
    assert "occupiedship=1046331ULL" in reply.fields
    assert any(f.startswith("pos=")for f in reply.fields)
    assert g(r,"zero_reads")==0 and g(r,"all_reads")>0
    assert ask(r,2,"component","1046331ULL").status=="OK"

def test_nil_native_enumeration_is_not_misreported_as_no_ship():
    r=onfoot('"1046331ULL"')
    r.execute('GetContainedObjectsByOwner=function() return nil end')
    reply=ask(r,1,'probe')
    assert reply.status=='OK'
    assert 'contained_in_my_sector=nil' in reply.fields
    assert 'no occupied ship' not in reply.payload.lower()
    assert g(r,'zero_reads')==0
