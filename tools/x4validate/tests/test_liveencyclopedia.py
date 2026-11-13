"""Actual Lua descriptor reads with synthetic native widgets; no game calls."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/'mods/x4_toolkit_helper/ui/live_encyclopedia.lua'


def make(section='description',setup=''):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute('''
        age=10;reads=0;cellreads=0;callbacks=0;menu_reads=0;encodes=0
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        function text(id,s)return {type='text',id=id,colspan=1,properties={text=s}}end
        cell=text(30,'Arawn');row={cell,index=1,rowdata=true}
        tbl={type='table',id=20,numcolumns=1,rows={row}}
        frame={id=10,properties={layer=1},content={tbl}}
        m={name='EncyclopediaMenu',shown=true,minimized=false,encyclopediaMode='encyclopedia',
            mode='Ships',library='ships_xl',id='arawn_macro',object=nil,frame=frame,frames={[1]=10},
            table_index=20,table_description=20,table_detail=20}
        menus={m}
        d={menus=function()menu_reads=menu_reads+1;if on_menu then on_menu(menu_reads)end;return menus end,
            current=function()return stamp end,now=function()return age end,
            valid_widget=function(id)reads=reads+1;return id~=invalid end,
            cell_id=function(t,ri,ci)
                cellreads=cellreads+1;if on_cell then on_cell(cellreads)end
                assert(t==tbl.id);return wrong_cell or tbl.rows[ri][ci].id
            end,
            encode=function(v)encodes=encodes+1;if encode_error then error('encoder refused')end;return string.rep('x',encoded_length or 10)end}
    ''')
    r.globals().section=section
    r.execute("tbl.numcolumns=section=='index' and 6 or section=='detail' and 5 or 1;for i=2,tbl.numcolumns do row[i]={type='icon',colspan=1}end")
    if setup:r.execute(setup)
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute('api=factory(d)')
    return r


@pytest.mark.parametrize('section',['index','description','detail'])
def test_lazy_constructor_and_native_section_text(section):
    r=make(section);assert r.eval('reads+menu_reads+cellreads')==0
    out=r.eval('api.read(section,1)')
    assert out.status=='OBSERVED' and out.cells[1].text=='Arawn'
    assert out.section==section and out.context.id.value=='arawn_macro'
    assert out.static_cells==1 and out.unknown_cells==0 and out.covers_selected_table is True
    assert out.full_encyclopedia_coverage is False and out.rendered_pixels_verified is False
    assert r.eval('menu_reads')==2 and r.eval('cellreads')==2


def test_dynamic_text_is_unknown_and_never_invoked():
    r=make(setup="cell.properties.text=function()callbacks=callbacks+1;return 'dangerous' end")
    out=r.eval('api.read(section,1)')
    assert out.status=='OBSERVED' and out.cells[1].status=='UNKNOWN'
    assert out.cells[1].reason=='DYNAMIC_CALLBACK_NOT_INVOKED' and out.cells[1].text is None
    assert out.all_text_descriptors_known is False and r.eval('callbacks')==0


@pytest.mark.parametrize('value',['nil','false','42','{}'])
def test_nonstring_text_is_unknown_not_empty(value):
    r=make(setup='cell.properties.text='+value)
    out=r.eval('api.read(section,1)')
    assert out.status=='OBSERVED' and out.cells[1].status=='UNKNOWN' and out.cells[1].text is None


def test_empty_text_is_observed_empty():
    r=make(setup="cell.properties.text=''")
    out=r.eval('api.read(section,1)')
    assert out.cells[1].status=='OBSERVED' and out.cells[1].text==''


def test_page_denominator_includes_all_built_text_not_only_returned_cells():
    r=make(setup="for i=2,6 do tbl.rows[i]={text(29+i,'row'..i),index=i}end")
    out=r.eval('api.read(section,1)')
    assert out.text_cells==6 and out.returned_count==4 and out.next_index==5 and out.covers_selected_table is False
    out=r.eval('api.read(section,5)')
    assert out.text_cells==6 and out.returned_count==2 and out.cells[1].row==5 and out.next_index==0


def test_no_text_is_measured_over_built_cell_slots():
    r=make(setup="cell.type='icon'")
    out=r.eval('api.read(section,1)')
    assert out.status=='OBSERVED' and out.text_cells==0 and out.total_cell_slots==1 and out.nontext_cells==1
    assert out.returned_count==0 and out.covers_selected_table is True


def test_spanned_placeholder_is_counted_separately():
    r=make('detail',"row[2].colspan=0")
    out=r.eval('api.read(section,1)')
    assert out.total_cell_slots==5 and out.spanned_cells==1 and out.nontext_cells==3


@pytest.mark.parametrize('change',[
    "m.shown=false","m.minimized=true","m.encyclopediaMode='shipcomparison'","m.displayRunning=0","m.refresh=true",
    "menus[2]=m","menus[2]={name='Other',shown=true}","m.frames[1]=11","frame.id=0","invalid=10","invalid=20",
    "m.table_description=21","tbl.numcolumns=5","frame.content[2]=tbl","row.index=2","tbl.rows[3]=row",
    "row[1]=nil","row[2]={}","cell.id=0","invalid=30","wrong_cell=31","cell.properties=nil",
    "cell.type=nil","cell.colspan=nil",
    "cell.properties.text=string.rep('x',4097)","cell.properties.text='a'..string.char(0)..'b'",
    "m.id=string.rep('x',4097)","stamp=nil","age=0/0","encoded_length=30001","encode_error=true",
])
def test_unavailable_native_shape_or_bound_does_not_produce_observed_text(change):
    r=make(setup=change);out=r.eval('api.read(section,1)')
    assert out.status=='UNAVAILABLE' and out.rendered_pixels_verified is False


@pytest.mark.parametrize('change',[
    "cell.properties.text='changed'","cell.properties={text='Arawn'}",
    "tbl.rows[1]={text(30,'Arawn'),index=1}","tbl.id=21;m.table_description=21",
    "frame.id=11;m.frames[1]=11","m.id='another_macro'","m.object=42",
    "stamp.generation=string.rep('d',32)","age=9","api.dispose()",
])
def test_between_read_changes_refuse_even_if_projected_text_stays_equal(change):
    r=make();r.execute('on_menu=function(n)if n==2 then '+change+' end end')
    assert r.eval('api.read(section,1).status')=='UNAVAILABLE'


def test_changed_dynamic_callback_identity_refuses_without_invoking_either():
    r=make(setup='cell.properties.text=function()callbacks=callbacks+1 end')
    r.execute('on_menu=function(n)if n==2 then cell.properties.text=function()callbacks=callbacks+1 end end end')
    assert r.eval('api.read(section,1).status')=='UNAVAILABLE' and r.eval('callbacks')==0


def test_unknown_object_context_stays_unknown():
    r=make(setup='m.object={untrusted=true}')
    out=r.eval('api.read(section,1)')
    assert out.status=='OBSERVED' and out.context.object.kind=='UNKNOWN' and out.context.object.value is None


def test_unknown_object_replacement_does_not_hide_context_drift():
    r=make(setup='m.object={untrusted=true}')
    r.execute('on_menu=function(n)if n==2 then m.object={untrusted=true}end end')
    assert r.eval('api.read(section,1).status')=='UNAVAILABLE'


def test_duplicate_widget_handles_refuse():
    r=make(setup="tbl.rows[2]={text(30,'another'),index=2}")
    assert r.eval('api.read(section,1).status')=='UNAVAILABLE'


@pytest.mark.parametrize('start',[0,3,1.5,float('inf')])
def test_invalid_window_refuses(start):
    r=make();r.globals().start=start
    assert r.eval('api.read(section,start).status')=='UNAVAILABLE'


def test_closed_section_vocabulary_and_retirement():
    r=make();assert r.eval("api.read('property',1).status")=='UNAVAILABLE'
    r.execute('api.dispose()');assert r.eval('api.read(section,1).status')=='UNAVAILABLE'
