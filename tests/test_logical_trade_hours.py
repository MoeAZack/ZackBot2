import gzip
import hashlib
import json

import pytest

from tools.research import logical_trade_hours as H


def minutes(hour):
    return [(t,100.,102.,98.,101.,1.,t+59999,10.,1,1.,1.,'0')
            for t in range(hour,hour+H.HOUR_MS,H.MINUTE_MS)]


def test_exact_sixty_and_no_minute_order_exposure():
    rows=minutes(H.SEAM_MS-H.HOUR_MS)
    result=H.exact_hours(rows)
    assert result == ((H.SEAM_MS-H.HOUR_MS,100.,102.,98.,101.,60.,H.SEAM_MS-1,600.,60,60.,60.,'0'),)


def test_missing_whole_sixty_minute_internal_hour_is_not_silently_compressed():
    rows=minutes(0)+minutes(2*H.HOUR_MS)
    assert len(rows)==120
    with pytest.raises(ValueError,match='missing entire internal'):
        H.exact_hours(rows)


def test_empty_and_observed_edges_do_not_invent_prelisting_or_tail_history():
    assert H.exact_hours([])==()
    assert [row[0] for row in H.exact_hours(minutes(7*H.HOUR_MS))]==[7*H.HOUR_MS]
    assert [row[0] for row in H.exact_hours(minutes(7*H.HOUR_MS)+minutes(8*H.HOUR_MS))]==[7*H.HOUR_MS,8*H.HOUR_MS]


def test_decimal_price_cells_are_not_rounded_by_shared_derivation():
    from decimal import Decimal
    rows=[list(row) for row in minutes(H.SEAM_MS-H.HOUR_MS)]
    for row in rows:
        row[1:5]=['100.00000000000000001','102.00000000000000002','98.00000000000000003','101.00000000000000004']
    result=H.exact_hours(rows)[0]
    assert Decimal(result[1])==Decimal('100.00000000000000001')
    assert Decimal(result[2])==Decimal('102.00000000000000002')


@pytest.mark.parametrize('attack',['gap','duplicate','partial','close','offgrid','nonfinite','ohlc'])
def test_hostile_minute_groups_fail_closed(attack):
    rows=minutes(H.SEAM_MS-H.HOUR_MS)
    if attack=='gap':rows.pop(17)
    elif attack=='duplicate':rows.insert(17,rows[17])
    elif attack=='partial':rows=rows[1:]
    else:
        row=list(rows[0])
        row[{'close':6,'offgrid':0,'nonfinite':7,'ohlc':2}[attack]]= {'close':0,'offgrid':1,'nonfinite':'nan','ohlc':1}[attack]
        rows[0]=row
    with pytest.raises(ValueError):H.exact_hours(rows)


def test_fixed_seam_has_no_native_base_or_minute_extension_fallback():
    for new,interval,expected in [(False,'1m',(H.SEAM_MS-H.HOUR_MS,H.SEAM_MS)),
                                  (True,'1h',(H.SEAM_MS,H.SEAM_MS+H.HOUR_MS)),
                                  (False,'1h',None),(True,'1m',None)]:
        assert H.trade_window({'series':'klines','interval':interval},new=new,
            start_ms=H.SEAM_MS-H.HOUR_MS,end_ms=H.SEAM_MS+H.HOUR_MS)==expected


def test_tracked_clarification_exact_bytes():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    H.validate_clarification(root)
    assert hashlib.sha256((root/H.CLARIFICATION_PATH).read_bytes()).hexdigest()==H.CLARIFICATION_SHA256


def test_actual_frozen_v5_metadata_selects_minutes_before_any_raw_parser(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from tools.research import c258_archive_source as S
    root=Path(__file__).resolve().parents[1]
    raw=(root/'research_evidence/manifests/binance-um-archive-precutoff-monthly-rest-v5.json.gz').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='3de00d9a9961d04938e71cd58fb042c28bfeea48519d91d8fbac2d12da99987a'
    manifest=json.loads(gzip.decompress(raw))
    monkeypatch.setattr(S.C,'validate_authority_pins',lambda *a,**k:{'manifest_v6':{
        'active_base_files_v5':manifest['files'],'files_v6':[]},'pit_v9':{},'filter_v4':{}})
    archive=S.C258ArchiveSource(SimpleNamespace(protected_head='a'*40),root/'DO_NOT_OPEN')
    class Selected(Exception):pass
    def intercept(store,record,**kw):
        assert record['interval']=='1m' and record['symbol']=='BTCUSDT' and not kw['new']
        raise Selected
    monkeypatch.setattr(S,'_read_window_rows',intercept)
    lo,_=S.frozen_window('development')
    with pytest.raises(Selected):
        archive.load(symbol='BTCUSDT',stream='klines',fold='development',start_ms=lo,end_ms=lo+H.HOUR_MS)


def test_c257_actual_metadata_selector_intercept_before_byte_parse(monkeypatch):
    from pathlib import Path
    from decimal import Decimal
    from tools.research import c257_archive_loader as L
    from tools.research import authority_v6 as V
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads(gzip.decompress((root/'research_evidence/manifests/binance-um-archive-precutoff-monthly-rest-v5.json.gz').read_bytes()))
    pit=json.loads((root/'research_evidence/universe/pit-top40-qv30d-precutoff-monthly-rest-v8.json').read_bytes())
    filters=json.loads((root/'contracts/strategy_causal_pack_01/exchange_filter_authority.v3.json').read_bytes())
    snap=filters['snapshots'][0];raw=(root/snap['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==snap['sha256']
    eligible=V._snapshot_eligible(json.loads(raw))
    start=L.S._iso_ms('2024-12-14T00:00:00Z');end=L.S.frozen_splits()['development'][1]
    cells=V._weekly_membership_cells(pit,eligible,start,end)
    loader=object.__new__(L.ProtectedArchiveLoader)
    loader.manifest={'active_base_files_v5':manifest['files'],'files_v6':[]};loader.pit=pit
    loader._development_passed=False
    loader.filters={'weekly_membership':{'development_eligible_segment':cells},
        'weekly_membership_sha256':{'development_eligible_segment':V.sha256(V.canonical(cells))}}
    loader._fold_filters=lambda *a:{symbol:None for symbol in eligible}
    class Selected(Exception):pass
    def intercept(record,*a,**kw):
        meta=V.M.archive_meta(record['path'])
        if meta['series']!='klines':return []
        assert meta['interval']=='1m' and not kw['new']
        raise Selected
    loader._source_rows=intercept
    with pytest.raises(Selected):loader.load_fold('development',Decimal(2000))


def test_c259_actual_metadata_selector_intercept_before_byte_parse(monkeypatch,tmp_path):
    from pathlib import Path
    from tools.research import btc_residual_leadlag_continuation_c259_stagea as E
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads(gzip.decompress((root/'research_evidence/manifests/binance-um-archive-precutoff-monthly-rest-v5.json.gz').read_bytes()))
    lo,hi=E.FOLD_RANGES['development']
    records=[r for r in manifest['files'] if r['symbol']=='BTCUSDT' and r['series']=='klines']
    for stream,interval in [('markPriceKlines','1h'),('fundingRate',None)]:
        records.append(dict(path=f"um/monthly/{stream}/BTCUSDT/"+('1h/' if interval else '')+'outside.zip',
            series=stream,interval=interval,symbol='BTCUSDT',first_open_ms=hi+H.HOUR_MS,last_open_ms=hi+2*H.HOUR_MS,sha256='a'*64))
    authority=E.ProtectedAuthority((('manifest_v6',json.dumps({'active_base_files_v5':records,'files_v6':[]}).encode()),),
                                    'a'*64,E._SOURCE_SEAL_CAPABILITY)
    monkeypatch.setitem(E._VALIDATED_AUTHORITIES,id(authority),authority)
    archive=authority.open_source(tmp_path)
    class Selected(Exception):pass
    def intercept(path):
        assert '/klines/BTCUSDT/1m/' in path
        raise Selected
    monkeypatch.setattr(archive,'_safe_path',intercept)
    with pytest.raises(Selected):archive.read_fold('BTCUSDT','development')


def test_c258_actual_zip_seam_and_partial_gap_no_native_fallback(tmp_path,monkeypatch):
    import io,zipfile
    from types import SimpleNamespace
    from tools.research import c258_archive_source as S
    def write(interval,month,rows,new):
        rel=f'um/monthly/klines/BTCUSDT/{interval}/BTCUSDT-{interval}-{month}.zip'
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr(rel.rsplit('/',1)[1][:-4]+'.csv','\n'.join(','.join(map(str,row)) for row in rows)+'\n')
        raw=data.getvalue();path=tmp_path/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        digest=hashlib.sha256(raw).hexdigest();path.with_name(path.name+'.ok').write_text(digest)
        record=dict(path=rel,symbol='BTCUSDT',series='klines',interval=interval,
            source={'kind':'binance-vision-archive','archive_checksum':digest})
        record.update({'raw_sha256' if new else 'sha256':digest,
                       'observed_first_ms' if new else 'first_open_ms':int(rows[0][0]),
                       'observed_last_ms' if new else 'last_open_ms':int(rows[-1][0])})
        return record
    before=H.SEAM_MS-H.HOUR_MS
    native=lambda t,price:(t,price,price,price,price,1,t+H.HOUR_MS-1,1,1,1,1,0)
    base=[write('1m','2026-08',minutes(before),False),write('1h','2026-08',[native(before,999)],False)]
    extension=[write('1h','2026-09',[native(H.SEAM_MS,200)],True)]
    monkeypatch.setattr(S.C,'validate_authority_pins',lambda *a,**k:{'manifest_v6':{
        'active_base_files_v5':base,'files_v6':extension},'pit_v9':{},'filter_v4':{}})
    archive=S.C258ArchiveSource(SimpleNamespace(protected_head='a'*40),tmp_path)
    bars=archive.candles(symbol='BTCUSDT',fold='wf3',start_ms=before,end_ms=H.SEAM_MS+H.HOUR_MS)
    assert [bar.open for bar in bars]==[100.,200.]
    base[0]=write('1m','2026-08',minutes(before)[:-1],False)
    archive=S.C258ArchiveSource(SimpleNamespace(protected_head='a'*40),tmp_path)
    with pytest.raises(S.C.IncompleteAuthority,match='partial'):
        archive.candles(symbol='BTCUSDT',fold='wf3',start_ms=before,end_ms=H.SEAM_MS+H.HOUR_MS)
