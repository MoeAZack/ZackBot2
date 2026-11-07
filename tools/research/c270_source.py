"""OFF C270 native-Decimal development source, never a tape/evidence seal.

Only the immutable C270 issued capability opens archive bytes. Signal floats
are an explicit original-C258 projection; native prices/filters remain Decimal.
"""
from dataclasses import dataclass
from decimal import Decimal
import gzip
import hashlib
import math
import re
from pathlib import Path
from types import MappingProxyType

from tools.research import c270_metadata as C
from tools.research import c270_authority as AUTH
from tools.research import logical_trade_hours as HOURS
from tools.research import c260_archive as CORE
from tools.research import manifest as M
from tools.research import universe as U
from tools.research import authority_v6 as V6

P,H,MINUTE=C.P,C.HOUR_MS,60_000


class SourceIncomplete(P.IncompleteAuthority):pass


def decimal(value):
    try:result=Decimal(value.decode('ascii') if isinstance(value,bytes) else str(value))
    except (ArithmeticError,UnicodeError,ValueError) as exc:raise SourceIncomplete('C270_INVALID_NATIVE_DECIMAL') from exc
    if not result.is_finite():raise SourceIncomplete('C270_NONFINITE_NATIVE_DECIMAL')
    return result


@dataclass(frozen=True)
class SignalBar(P.Bar):
    """Own270 price/ATR projection: observed zero qv is valid, never invented.

    Positive-volume binary64 arithmetic remains original259; its global Bar
    validator is not changed. Decision and aggregate liquidity gates remain.
    """
    def validate(self):
        if (type(self.open_ms)is not int or self.open_ms%H
            or any(not math.isfinite(v) or v<=0 for v in(self.open,self.high,self.low,self.close))
            or not math.isfinite(self.quote_volume) or self.quote_volume<0
            or not self.low<=min(self.open,self.close)<=max(self.open,self.close)<=self.high):
            raise SourceIncomplete('C270_INVALID_SIGNAL_PRICE_OR_NONNEGATIVE_QUOTE')


@dataclass(frozen=True)
class NativeBar:
    open_ms:int
    open:Decimal
    high:Decimal
    low:Decimal
    close:Decimal
    quote_volume:Decimal

    def validate(self):
        if (type(self.open_ms)is not int or self.open_ms%H or any(type(v)is not Decimal or not v.is_finite()
            for v in (self.open,self.high,self.low,self.close,self.quote_volume))
            or min(self.open,self.high,self.low,self.close)<=0 or self.quote_volume<0
            or not self.low<=min(self.open,self.close)<=max(self.open,self.close)<=self.high):
            raise SourceIncomplete('C270_INVALID_EXACT_NATIVE_PRICE_OR_VOLUME')

    def signal_bar(self):
        # Conversion belongs only to the unchanged binary64 signal/ATR/EMA,
        # never to native PRICE_FILTER checks or Decimal LegSpec references.
        self.validate()
        result=SignalBar(self.open_ms,*(float(v) for v in (self.open,self.high,self.low,self.close,self.quote_volume)))
        result.validate();return result


@dataclass(frozen=True)
class NativeFunding:
    time_ms:int
    interval_hours:int|None
    rate:Decimal


@dataclass(frozen=True)
class BoundaryOpen:
    open_ms:int
    open:Decimal


@dataclass(frozen=True)
class PublishedMonth:
    path:str
    sha256:str
    start_ms:int
    end_ms:int
    rows:int


_NATIVE_NUMBER=re.compile(rb'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


def authenticated_funding_month(store,record,start,end):
    """Full official published-month proof; convert rates ONLY in granted scope.

    Interval is native descriptive metadata. No unsupported ENDING/STARTING
    predicate, missing-event imputation, or claim about venue omniscience.
    """
    meta=M.archive_meta(record['path'])
    if (meta['series']!='fundingRate' or not record['path'].startswith('um/monthly/')
        or not record['path'].endswith('.zip')):
        raise SourceIncomplete('C270_COMPLETE_OFFICIAL_MONTHLY_FUNDING_PROOF_REQUIRED')
    try:raw,published,csv=M.read_archive(M.contained(str(store),record['path']),record['path'])
    except (OSError,ValueError,RuntimeError) as exc:raise SourceIncomplete(str(exc)) from exc
    if len(raw)!=record['bytes'] or published!=record['sha256'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
        raise SourceIncomplete('C270_FUNDING_CHECKSUM_BEFORE_RATE_PARSE')
    lines=csv.splitlines()
    if lines and lines[0]==M.FUNDING_HEADER:lines=lines[1:]
    times=[];selected=[]
    for line in lines:
        cells=line.split(b',')
        if (len(cells)!=3 or not cells[0].isdigit() or not cells[1].isdigit()
            or not 1<=int(cells[1])<=24 or not _NATIVE_NUMBER.fullmatch(cells[2])):
            raise SourceIncomplete('C270_PUBLISHED_FUNDING_RECORD_SCHEMA')
        t=int(cells[0]);times.append(t)
        if not meta['month_start_ms']<=t<meta['month_end_ms']:
            raise SourceIncomplete('C270_PUBLISHED_FUNDING_CALENDAR')
        if start<=t<end:selected.append(NativeFunding(t,int(cells[1]),decimal(cells[2])))
    if (len(times)!=record['rows'] or (times[0] if times else None)!=record['first_open_ms']
        or (times[-1] if times else None)!=record['last_open_ms'] or any(b<=a for a,b in zip(times,times[1:]))):
        raise SourceIncomplete('C270_PUBLISHED_FUNDING_ALL_RECORD_BOUNDS')
    return tuple(selected),PublishedMonth(record['path'],record['sha256'],meta['month_start_ms'],meta['month_end_ms'],len(times))


@dataclass(frozen=True)
class NativeFilters:
    captured_ms:int
    server_ms:int
    snapshot_sha256:str
    eligible_symbols:frozenset[str]
    rules:object


@dataclass(frozen=True)
class MetadataView:
    repository_root:Path
    store_root:Path
    start_ms:int
    eligible_start_ms:int
    end_ms:int
    manifest:object
    pit:object
    filters:NativeFilters
    digests:object

    def members(self,t):
        if type(t)is not int or not self.start_ms<=t<self.end_ms:raise SourceIncomplete('C270_DEVELOPMENT_ONLY_ASOF')
        if t<self.eligible_start_ms:return ()
        week=max((w for w in self.pit['weeks'] if w['monday_ms']<=t),key=lambda w:w['monday_ms'],default=None)
        if week is None:raise SourceIncomplete('C270_MISSING_CAUSAL_PIT')
        return tuple(s for s in week['books']['crypto']['members'] if s in self.filters.eligible_symbols)

    def required_symbols(self):
        times=(self.eligible_start_ms,*(w['monday_ms'] for w in self.pit['weeks']
            if self.eligible_start_ms<w['monday_ms']<self.end_ms))
        return frozenset({'BTCUSDT'}|{s for t in times for s in self.members(t)})

    def required_intervals(self,symbol):
        if symbol=='BTCUSDT':return ((self.start_ms,self.end_ms),)
        times=(self.eligible_start_ms,*(w['monday_ms'] for w in self.pit['weeks']
            if self.eligible_start_ms<w['monday_ms']<self.end_ms),self.end_ms)
        return tuple((max(self.start_ms,t-25*H),min(self.end_ms,nxt+4*H))
            for t,nxt in zip(times,times[1:]) if symbol in self.members(t))

    def records(self,symbol,series,*,boundary=False):
        interval=None if series=='fundingRate' else '1m' if series=='klines' else '1h'
        result=[]
        for record in self.manifest['files']:
            # M.validate has already crossbound these native archive fields
            # to the path. Avoid reparsing 102k unrelated paths per lookup.
            if (record['symbol'],record['series'],record['interval'])!=(symbol,series,interval):continue
            meta=M.archive_meta(record['path'])
            if boundary:
                relevant=meta['month_start_ms']<=self.end_ms<meta['month_end_ms']
            else:
                first,last=record.get('first_open_ms'),record.get('last_open_ms')
                relevant=(last is not None and first is not None and last>=self.start_ms and first<self.end_ms)
            if relevant:result.append(record)
        return tuple(result)

    def preflight_grid(self):
        # Presence/stat only. In particular do NOT demand every asset's native
        # first row be Oct14, or equate a declared month with a complete grid.
        for symbol in self.required_symbols():
            for series in ('klines','markPriceKlines','fundingRate'):
                records=self.records(symbol,series)
                if not records:raise C.C270Locked(f'C270_REQUIRED_SOURCE_METADATA_ABSENT:{symbol}:{series}')
                for record in records:
                    path=Path(M.contained(str(self.store_root),record['path']))
                    if not path.is_file() or not Path(str(path)+'.ok').is_file():
                        raise C.C270Locked('C270_SOURCE_STAT_INCOMPLETE_BEFORE_SPEND')


def prepare_metadata(root,store,artifacts):
    root,store=root.resolve(),store.resolve()
    proposal=C.load_proposal_metadata();windows=proposal['splits']
    start,end=map(CORE.milliseconds,windows['development'].split('/'))
    eligible,_=map(CORE.milliseconds,windows['development_filter_eligible'].split('/'))
    docs,digests,pins={},{},{}
    for name,path,digest,raw in artifacts:
        if name in docs or type(raw)is not bytes or hashlib.sha256(raw).hexdigest()!=digest:
            raise SourceIncomplete('C270_ARTIFACT_BYTES_OR_DUPLICATE')
        M.check_rel_path(path);pins[name]={'path':path,'byte_sha256':digest};digests[name]=digest
        docs[name]=CORE.strict_json(gzip.decompress(raw) if path.endswith('.gz') else raw)
    if set(docs)!={'manifest','pit','filters','coverage'}:raise SourceIncomplete('C270_EXACT_ARTIFACT_SET')
    manifest,pit,coverage=docs['manifest'],docs['pit'],docs['coverage'];M.validate(manifest)
    if (manifest['loader_version']!=M.ARCHIVE_LOADER or pit.get('format')!=U.FORMAT
        or pit.get('digest')!=U.digest_of(pit) or pit.get('manifest_digest')!=manifest['digest']
        or coverage.get('digest')!=M.digest_of(coverage) or coverage.get('manifest_digest')!=manifest['digest']
        or coverage.get('pit_universe_digest')!=pit['digest']):raise SourceIncomplete('C270_SOURCE_CROSSBINDING')
    weeks=pit.get('weeks')
    if (not isinstance(weeks,list) or not weeks or any(type(w.get('monday_ms'))is not int for w in weeks)
        or any(b['monday_ms']<=a['monday_ms'] for a,b in zip(weeks,weeks[1:]))):
        raise SourceIncomplete('C270_PIT_WEEK_ORDER')
    for week in weeks:
        members=week.get('books',{}).get('crypto',{}).get('members')
        if not isinstance(members,list) or any(type(s)is not str or not s for s in members) or len(members)!=len(set(members)):
            raise SourceIncomplete('C270_FULL_PIT_MEMBER_SCHEMA')
    authority=docs['filters']
    CORE.A._validate_filter_authority_rebind_v3(authority,root)
    if (authority.get('pit_universe_path')!=pins['pit']['path']
        or authority.get('pit_universe_sha256')!=digests['pit'] or authority.get('pit_universe_digest')!=pit['digest']):
        raise SourceIncomplete('C270_FILTER_PIT_BINDING')
    captures=[];seen=set();payload=None
    for row in authority['snapshots']:
        capture,server=CORE.milliseconds(row['captured_at']),CORE.milliseconds(row['server_time'])
        if capture in seen:raise SourceIncomplete('C270_DUPLICATE_SNAPSHOT_CAPTURE')
        seen.add(capture)
        if capture<eligible and server<eligible:captures.append((capture,server,row))
    if not captures:raise SourceIncomplete('C270_NO_STRICT_PRE_DEC14_SNAPSHOT')
    capture,server,chosen=max(captures,key=lambda x:x[0])
    for row in authority['snapshots']:
        raw=Path(M.contained(str(root),row['path'])).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise SourceIncomplete('C270_SNAPSHOT_BYTE_DRIFT')
        if row is chosen:payload=CORE.strict_json(raw)
    if payload is None or payload.get('serverTime')!=server:raise SourceIncomplete('C270_SNAPSHOT_SERVER_BINDING')
    eligible_symbols=V6._snapshot_eligible(payload);rules={};seen_symbols=set()
    for row in payload['symbols']:
        symbol=row.get('symbol')
        if symbol in seen_symbols:raise SourceIncomplete('C270_DUPLICATE_SNAPSHOT_SYMBOL')
        seen_symbols.add(symbol)
        if symbol not in eligible_symbols:continue
        raw_filters=row['filters'];by_type={f['filterType']:f for f in raw_filters}
        if len(by_type)!=len(raw_filters):raise SourceIncomplete('C270_DUPLICATE_NATIVE_FILTER')
        lot,minimum=by_type['MARKET_LOT_SIZE'],by_type['MIN_NOTIONAL']
        tick=decimal(by_type['PRICE_FILTER']['tickSize'])
        rule=P.Filters(tick,decimal(lot['stepSize']),decimal(lot['minQty']),decimal(lot['maxQty']),
                       decimal(minimum.get('notional',minimum.get('minNotional'))))
        if any(not v.is_finite() or v<=0 for v in (rule.tick,rule.step,rule.min_qty,rule.max_qty,rule.min_notional)):
            raise SourceIncomplete('C270_INVALID_NATIVE_FILTER')
        if tick<=0:raise SourceIncomplete('C270_POSITIVE_NATIVE_TICK_REQUIRED')
        rules[symbol]=(rule,tick)
    filters=NativeFilters(capture,server,chosen['sha256'],eligible_symbols,MappingProxyType(rules))
    return MetadataView(root,store,start,eligible,end,CORE._freeze(manifest),CORE._freeze(pit),filters,CORE._freeze(digests))


class DevelopmentSource:
    def __init__(self,cap):
        AUTH.validate_issued(cap);self._cap=cap
        self.view=prepare_metadata(cap.repository_root,cap.store_root,cap.artifacts)
        self.root,self.store=self.view.repository_root,self.view.store_root
        self.start_ms,self.end_ms=self.view.start_ms,self.view.end_ms
        self.side=cap.side
        self.signal_end_ms=cap.signal_end_ms;self.funding_tail_exclusive_end_ms=cap.funding_tail_exclusive_end_ms
        if ((self.start_ms,self.end_ms,self.signal_end_ms)!=(cap.start_ms,cap.end_ms,cap.end_ms)
            or cap.funding_tail_exclusive_end_ms!=cap.end_ms+C.TAIL_MS+1):
            raise C.C270Locked('C270_OWN_FIXED_SIGNAL_WINDOW_REQUIRED')
        self.manifest,self.pit,self.filters,self.digests=self.view.manifest,self.view.pit,self.view.filters,self.view.digests
        self._cache,self._issued_streams,self.source_reads={},{},[]
        self.known_gaps=[];self.published_funding_proofs=[]
        self._plan_symbols=self.view.required_symbols()
        funding_index={s:[] for s in self._plan_symbols}
        for record in self.manifest['files']:
            if record['series']=='fundingRate' and record['symbol'] in funding_index:funding_index[record['symbol']].append(record)
        self._funding_index=MappingProxyType({s:tuple(rows) for s,rows in funding_index.items()})
        self._proof_cache={};self._issued_proofs={}
        self._binding=(self.view,self.manifest,self.pit,self.filters,self.digests,self._cache,
            self._plan_symbols,self._funding_index,self._proof_cache,self._issued_proofs)

    def _ensure_authority(self):
        AUTH.validate_issued(self._cap);cap=self._cap
        if ((self.start_ms,self.end_ms,self.signal_end_ms,self.funding_tail_exclusive_end_ms)
            !=(cap.start_ms,cap.end_ms,cap.signal_end_ms,cap.funding_tail_exclusive_end_ms) or type(self.side)is not int or self.side!=cap.side
            or self.root!=cap.repository_root or self.store!=cap.store_root
            or any(a is not b for a,b in zip((self.view,self.manifest,self.pit,self.filters,self.digests,self._cache,
                self._plan_symbols,self._funding_index,self._proof_cache,self._issued_proofs),self._binding))):
            raise C.C270Locked('C270_SOURCE_SCOPE_OR_IMMUTABLE_METADATA_CHANGED')

    def membership_and_filters(self,*,decision_ms,fold='development'):
        self._ensure_authority()
        if fold!='development':raise C.C270Locked('C270_FORWARD_AND_HOLDOUT_SOURCE_LOCKED')
        members=self.view.members(decision_ms)
        return members,MappingProxyType({s:self.filters.rules[s] for s in members})

    def _selected_rows(self,record,start,end):
        self._ensure_authority()
        if record not in self.manifest['files'] or not self.start_ms<=start<end<=self.end_ms:
            raise C.C270Locked('C270_FIXED_DEVELOPMENT_RECORD_WINDOW_REQUIRED')
        try:return CORE._Archive._selected_rows(self,record,start,end)
        except (OSError,ValueError,CORE.SourceIncomplete) as exc:raise SourceIncomplete(str(exc)) from exc

    def _stream(self,symbol,series):
        self._ensure_authority();key=symbol,series
        if symbol not in self._plan_symbols:raise C.C270Locked('C270_SYMBOL_NOT_IN_FIXED_CAUSAL_SOURCE_PLAN')
        if key in self._cache:
            if self._cache[key] is not self._issued_streams.get(key):raise C.C270Locked('C270_NATIVE_CACHE_REPLACED')
            return self._cache[key]
        raw={}
        if series=='fundingRate':
            native={}
            for record in self.view.records(symbol,series):
                rows,proof=authenticated_funding_month(self.store,record,self.start_ms,self.end_ms)
                self._proof_cache[record['path']]=self._issued_proofs[record['path']]=proof
                self.published_funding_proofs.append(proof)
                self.source_reads.append({'path':proof.path,'sha256':proof.sha256,'mode':'ORDINARY_NATIVE_FUNDING',
                    'start_ms':self.start_ms,'exclusive_end_ms':self.end_ms})
                for row in rows:
                    if row.time_ms in native:raise SourceIncomplete('C270_DUPLICATE_SOURCE_COORDINATE')
                    native[row.time_ms]=row
            result=MappingProxyType(dict(sorted(native.items())))
            self._cache[key]=result;self._issued_streams[key]=result;return result
        for record in self.view.records(symbol,series):
            rows,zipped=self._selected_rows(record,self.start_ms,self.end_ms)
            for row in rows:
                t=int(row[0]) if zipped or series!='fundingRate' else row['fundingTime']
                if t in raw:raise SourceIncomplete('C270_DUPLICATE_SOURCE_COORDINATE')
                raw[t]=row
        if series=='klines':
            # Exact helper rejects a present partial hour. Split only on whole
            # absent hours; original signal warmup and owned-lifecycle checks
            # decide their causal relevance, never a fabricated price.
            groups={}
            for t,row in sorted(raw.items()):groups.setdefault(t//H*H,[]).append(row)
            logical=[]
            first_hour=min(groups,default=None)
            for hour,rows in sorted(groups.items()):
                try:logical.extend(HOURS.exact_hours(rows))
                except ValueError as exc:
                    # An authenticated asset may start partway through its
                    # first archive hour. C270's preread clarification leaves
                    # that ONE exact suffix unavailable, requiring the full
                    # original720-return warmup from the first complete bar.
                    coordinates=tuple(int(row[0]) for row in rows)
                    initial_suffix=(hour==first_hour and coordinates and coordinates[0]>hour
                        and coordinates==tuple(range(coordinates[0],hour+H,MINUTE)))
                    if initial_suffix and str(exc)=='missing/partial canonical minute hour':
                        self.known_gaps.append({'symbol':symbol,'series':series,'start_ms':hour,'end_ms':hour+H,
                            'reason':'INITIAL_PARTIAL_EXACT_SUFFIX_UNAVAILABLE_REQUIRES_FULL_HISTORY_WARMUP'})
                        continue
                    raise SourceIncomplete(str(exc)) from exc
            native={}
            for row in logical:
                point=NativeBar(int(row[0]),*(decimal(row[i]) for i in (1,2,3,4,7)));point.validate();native[point.open_ms]=point
        elif series=='markPriceKlines':
            native={}
            for t,row in sorted(raw.items()):
                if len(row)!=12 or int(row[6])!=t+H-1:raise SourceIncomplete('C270_NATIVE_MARK_CLOCK')
                point=NativeBar(t,*(decimal(row[i]) for i in (1,2,3,4,7)));point.validate();native[t]=point
        elif series=='fundingRate':
            native={}
            for t,row in sorted(raw.items()):
                if isinstance(row,dict):interval,rate=None,decimal(row['fundingRate'])
                else:interval,rate=int(row[1]),decimal(row[2])
                if interval is not None and not 0<interval<=24:raise SourceIncomplete('C270_NATIVE_FUNDING_SCHEMA')
                native[t]=NativeFunding(t,interval,rate)
        else:raise SourceIncomplete('C270_UNKNOWN_TYPED_STREAM')
        if series!='fundingRate' and native:
            observed=tuple(native)
            for left,right in zip(observed,observed[1:]):
                if right==left+H:continue
                gap={'symbol':symbol,'series':series,'start_ms':left+H,'end_ms':right}
                self.known_gaps.append(gap)
                if any(max(left+H,lo)<min(right,hi) for lo,hi in self.view.required_intervals(symbol)):
                    raise SourceIncomplete('C270_REQUIRED_INTERNAL_NATIVE_HOURLY_GAP')
        result=MappingProxyType(native);self._cache[key]=result;self._issued_streams[key]=result;return result

    def native_candles(self,symbol):return self._stream(symbol,'klines')
    def native_marks(self,symbol):return self._stream(symbol,'markPriceKlines')
    def funding(self,symbol):return self._stream(symbol,'fundingRate')
    def candles(self,symbol):return tuple(b.signal_bar() for b in self.native_candles(symbol).values())

    def exact_native_bar(self,symbol,t):
        row=self.native_candles(symbol).get(t)
        if row is None:raise SourceIncomplete(f'C270_MISSING_REQUIRED_CANONICAL_BAR:{symbol}:{t}')
        return row

    def funding_exhaustivity(self,symbol,lower_ms,upper_ms):
        """Inclusive liability coverage, not first/last-event extrapolation.

        Full official-month validation is structural outside the granted rate
        interval. Stop once relevant liability is covered, not at a later gap.
        """
        self._ensure_authority()
        if (symbol not in self._plan_symbols or type(lower_ms)is not int or type(upper_ms)is not int
            or not self.start_ms<=lower_ms<=upper_ms<self.funding_tail_exclusive_end_ms):
            raise C.C270Locked('C270_FIXED_OLD_LIABILITY_PROOF_SCOPE')
        records=[]
        for record in self._funding_index[symbol]:
            meta=M.archive_meta(record['path'])
            if meta['month_end_ms']>lower_ms and meta['month_start_ms']<=upper_ms:
                records.append((meta['month_start_ms'],record))
        cursor=lower_ms;proofs=[]
        for _,record in sorted(records):
            if cursor>upper_ms:break
            proof=self._proof_cache.get(record['path'])
            if record['path'] in self._proof_cache:
                if proof is not self._issued_proofs.get(record['path']):raise C.C270Locked('C270_AUTHENTICATED_PROOF_CACHE_REPLACED')
            else:
                _,proof=authenticated_funding_month(self.store,record,0,0)
                self._proof_cache[record['path']]=self._issued_proofs[record['path']]=proof
            if proof.start_ms>cursor:raise SourceIncomplete('C270_PUBLISHED_FUNDING_REQUIRED_MONTH_GAP')
            if proof.end_ms<=cursor:continue
            cursor=proof.end_ms;proofs.append(proof)
        if cursor<=upper_ms:raise SourceIncomplete('C270_PUBLISHED_FUNDING_LIABILITY_TAIL_UNPROVED')
        self.published_funding_proofs.extend(proofs)
        return tuple(proofs)

    def funding_tail(self,symbol,*,old_entry_ms):
        """Fixed terminal-only old-liability rates; no future price/mark read.

        Trusted fixed book must supply its actual old leg. This typed source
        method does not certify a caller-created trade or grant execution.
        """
        self._ensure_authority()
        if type(old_entry_ms)is not int or not self.start_ms<=old_entry_ms<self.end_ms:
            raise C.C270Locked('C270_TAIL_ONLY_ACTUAL_OLD_LEG')
        self.funding_exhaustivity(symbol,max(self.start_ms,old_entry_ms-C.TAIL_MS),self.funding_tail_exclusive_end_ms-1)
        rows={}
        for record in self.view.records(symbol,'fundingRate',boundary=True):
            selected,proof=authenticated_funding_month(self.store,record,self.end_ms,self.funding_tail_exclusive_end_ms)
            for row in selected:
                if row.time_ms in rows:raise SourceIncomplete('C270_DUPLICATE_SOURCE_COORDINATE')
                rows[row.time_ms]=row
            self.source_reads.append({'path':proof.path,'sha256':proof.sha256,'mode':'OLD_CLOSED_LIABILITY_FUNDING_ONLY',
                'start_ms':self.end_ms,'exclusive_end_ms':self.funding_tail_exclusive_end_ms,
                'cash_applied_ms':self.end_ms+C.TAIL_MS})
        return tuple(rows[t] for t in sorted(rows))

    def boundary_open(self,symbol):
        self._ensure_authority();found=[]
        if symbol not in self._plan_symbols:raise C.C270Locked('C270_BOUNDARY_SYMBOL_SCOPE')
        for record in self.view.records(symbol,'klines',boundary=True):
            target=M.contained(str(self.store),record['path'])
            try:raw,published,csv=M.read_archive(str(target),record['path'])
            except (OSError,ValueError,RuntimeError) as exc:raise SourceIncomplete(str(exc)) from exc
            if len(raw)!=record['bytes'] or published!=record['sha256'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
                raise SourceIncomplete('C270_BOUNDARY_SOURCE_CHECKSUM_OR_LENGTH')
            previous=None
            for i,line in enumerate(csv.splitlines()):
                if i==0 and line==M.KLINE_HEADER:continue
                t=int(line.split(b',',1)[0])
                if previous is not None and t<=previous:raise SourceIncomplete('C270_BOUNDARY_TIMESTAMP_ORDER')
                previous=t
                if t==self.signal_end_ms:
                    opening=decimal(line.split(b',',2)[1])
                    if opening<=0:raise SourceIncomplete('C270_INVALID_EXACT_BOUNDARY_OPEN')
                    found.append(BoundaryOpen(t,opening))
            self.source_reads.append({'path':record['path'],'sha256':record['sha256'],
                'mode':'EXACT_END_OPEN_ONLY_NO_FUTURE_HLCV','open_ms':self.signal_end_ms})
        if len(found)!=1:raise SourceIncomplete('C270_MISSING_OR_DUPLICATE_BOUNDARY_OPEN')
        return found[0]
