"""Trusted fixture/generation contracts only; no imported application execution."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def node(script):
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


FIXTURE = """
import { fixtureResponse } from './examples/document/operational-metrics/downstream/scenarios.mjs';
const rows = [
 {metric_name:'codex.memory.startup',attrs:{state:'succeeded',phase:'one'},
  occurrences:2,total_value:9,last_seen:'2026-11-07 12:00:00'},
 {metric_name:'codex.memory.startup',attrs:{state:'skipped_rate_limit'},
  occurrences:3,total_value:8,last_seen:'2026-11-07 11:59:00'},
 {metric_name:'codex.memories.followup',attrs:null,
  occurrences:1,total_value:5,last_seen:'2026-11-07 11:58:00'},
];
const urls = ['codex.memory.','codex.memories.'].map(p =>
 `http://localhost/api/v2/metrics?name_prefix=${p}&agent=codex&since=2026-11-07T11:59:00Z&limit=500`)
 .concat(['codex.memory.','codex.memories.'].map(p =>
 `http://localhost/api/v2/metrics?name_prefix=${p}&agent=codex&limit=1`));
"""


def test_fixture_projects_each_query_in_a_concurrent_four_request_load():
    observed = node(
        FIXTURE
        + """
console.log(JSON.stringify(await Promise.all(urls.map(async url =>
 fixtureResponse(rows,'fixture',url,'initial')))));
"""
    )
    groups = [response["body"]["metrics"] for response in observed]
    assert [len(group) for group in groups] == [2, 0, 1, 1]
    assert groups[0][0]["occurrences"] == 2
    assert groups[0][0]["total_value"] == 9
    assert groups[2][0]["last_seen"] == "2026-11-07 12:00:00"
    assert groups[3][0]["metric_name"] == "codex.memories.followup"


def test_faults_and_revisions_belong_to_action_phases_not_http_ordinals():
    observed = node(
        FIXTURE
        + """
const batch = (mode,phase) => urls.map(url => fixtureResponse(rows,mode,url,phase));
console.log(JSON.stringify({initial:batch('refresh-error-recovery','initial'),
 failed:batch('refresh-error-recovery','refresh-failed'),
 recovered:batch('refresh-error-recovery','recovered'),
 initialError:batch('initial-error','initial'),
 retry:batch('initial-error','initial-recovered'),
 refreshed:batch('fixture','refreshed')}));
"""
    )
    assert [row["status"] for row in observed["initial"]] == [200] * 4
    assert [row["status"] for row in observed["failed"]] == [503] * 4
    assert [row["status"] for row in observed["initialError"]] == [503] * 4
    assert [row["status"] for row in observed["retry"]] == [200] * 4
    for phase in ("recovered", "refreshed"):
        assert observed[phase][0]["body"]["metrics"][0]["total_value"] == 26


def test_fixture_agent_session_scope_and_normal_latest_cap():
    observed = node(
        FIXTURE
        + """
const read = query => fixtureResponse(rows,'fixture','http://localhost/api/v2/metrics?'+query,'initial').body.metrics;
console.log(JSON.stringify({one:read('limit=1'),wrongAgent:read('agent=claude'),
 unsupportedSession:fixtureResponse(rows,'fixture','http://localhost/api/v2/metrics?session_id=missing','initial'),
 knownSession:fixtureResponse(rows.map(row=>({...row,session_id:'fixture-memory'})),'fixture','http://localhost/api/v2/metrics?session_id=fixture-memory','initial'),
 wrongKnownSession:fixtureResponse(rows.map(row=>({...row,session_id:'fixture-memory'})),'fixture','http://localhost/api/v2/metrics?session_id=missing','initial'),
 offset:read('since=2026-11-07T13:00:00%2B01:00'),
 missingPrefix:read('name_prefix=fixture.missing.')}));
"""
    )
    assert len(observed["one"]) == 1
    assert observed["wrongAgent"] == []
    assert observed["unsupportedSession"]["supported"] is False
    assert "session_id fixture scope" in observed["unsupportedSession"]["reason"]
    assert len(observed["knownSession"]["body"]["metrics"]) == 3
    assert observed["wrongKnownSession"]["body"]["metrics"] == []
    assert len(observed["offset"]) == 1
    assert observed["missingPrefix"] == []


def test_range_transition_data_and_failure_follow_the_whole_phase():
    observed = node(
        FIXTURE
        + """
const options={rangeWindows:{wide_hours:168,narrow_hours:24}};
const read=(phase,url=urls[0]) => fixtureResponse(rows,'range-transition',url,phase,options);
console.log(JSON.stringify({initial:read('initial'),wide:read('wide-loaded'),
 failed:urls.map(url=>read('narrow-failed',url)),
 recovered:read('narrow-recovered'),wrongPrefix:read('wide-loaded',urls[1])}));
"""
    )

    def older(response):
        return [
            row
            for row in response["body"]["metrics"]
            if row.get("attrs", {}) and row["attrs"].get("phase") == "older-window"
        ]

    # The first URL requests only one minute of data; wide phase metadata cannot
    # override the actual query's lower bound.
    assert older(observed["wide"]) == []
    assert older(observed["initial"]) == []
    assert older(observed["recovered"]) == []
    assert observed["wrongPrefix"]["body"]["metrics"] == []
    assert all(response["status"] == 503 for response in observed["failed"])


def test_generation_waits_for_all_concurrent_and_sequential_requests():
    observed = node("""
import { ReadGenerations } from './examples/document/operational-metrics/downstream/scenarios.mjs';
let time=0, releaseTimes=[];
const batches=new ReadGenerations({now:()=>time,sleep:async ms=>{time+=ms;}});
const initial=batches.begin('initial',{deferred:true});
for(let n=0;n<4;n++){
 const row={ordinal:n+1}; batches.record(row);
 batches.defer(row,async()=>{
  releaseTimes.push(n);batches.complete(row);
  if(n===3){
   await Promise.resolve();
   const sequential={ordinal:5}; batches.record(sequential);
   await Promise.resolve();batches.complete(sequential);
  }
 });
}
await batches.wait(initial,{pending:true});
await batches.release(initial);
const single=batches.begin('refresh');
const row={ordinal:6};batches.record(row);batches.complete(row);
await batches.wait(single);
console.log(JSON.stringify({releaseTimes,groups:batches.describe(),time}));
""")
    assert observed["releaseTimes"] == [0, 1, 2, 3]
    assert observed["groups"][0]["request_ordinals"] == [1, 2, 3, 4, 5]
    assert observed["groups"][0]["settled"] is True
    assert observed["groups"][1]["request_ordinals"] == [6]
    assert observed["groups"][1]["settled"] is True
    assert observed["time"] >= 450


def test_generation_completion_includes_later_arrivals_and_all_responses():
    observed = node("""
import { ReadGenerations } from './examples/document/operational-metrics/downstream/scenarios.mjs';
let time=0;const first={ordinal:1};let second;
const batches=new ReadGenerations({now:()=>time,sleep:async ms=>{
 time+=ms;
 if(time===25)batches.complete(first);
 if(time===100){second={ordinal:2};batches.record(second);}
 if(time===200)batches.complete(second);
}});
const generation=batches.begin('initial');batches.record(first);
await batches.wait(generation);
console.log(JSON.stringify({time,rows:generation.requests,description:batches.describe()}));
""")
    assert observed["time"] >= 350
    assert [row["completed"] for row in observed["rows"]] == [True, True]
    assert observed["description"][0]["request_ordinals"] == [1, 2]


def test_race_releases_complete_newer_and_older_generations_and_records_cancellation():
    observed = node("""
import { ReadGenerations } from './examples/document/operational-metrics/downstream/scenarios.mjs';
let time=0;const releases=[];
const batches=new ReadGenerations({now:()=>time,sleep:async ms=>{time+=ms;}});
const older=batches.begin('initial',{deferred:true});
for(let n=0;n<4;n++){const row={ordinal:n+1};batches.record(row);
 batches.defer(row,async()=>{releases.push(['older',n]);
  if(n===0)batches.complete(row,{errorText:'net::ERR_ABORTED'});else batches.complete(row);
 });}
await batches.wait(older,{pending:true});
const newer=batches.begin('newer-first',{deferred:true});
for(let n=0;n<4;n++){const row={ordinal:n+5};batches.record(row);
 batches.defer(row,async()=>{releases.push(['newer',n]);batches.complete(row);});}
await batches.wait(newer,{pending:true});
await batches.release(newer,1);await batches.release(older);
console.log(JSON.stringify({releases,groups:batches.describe(),failure:older.requests[0].failure,
 revisions:[older.revision,newer.revision]}));
""")
    assert [row[0] for row in observed["releases"]] == ["newer"] * 4 + ["older"] * 4
    assert all(group["settled"] for group in observed["groups"])
    assert observed["failure"]["errorText"] == "net::ERR_ABORTED"
    assert observed["revisions"] == [0, 1]


def test_unsettled_and_post_quiet_requests_are_explicitly_incomplete():
    observed = node("""
import { ReadGenerations } from './examples/document/operational-metrics/downstream/scenarios.mjs';
let time=0;const batches=new ReadGenerations({now:()=>time,
 sleep:async ms=>{time+=ms;},deadlineMilliseconds:250});
const failed=batches.begin('initial');batches.record({ordinal:1});let error;
try{await batches.wait(failed);}catch(e){error=e.message;}
const single=batches.begin('refresh');
const row={ordinal:2};batches.record(row);batches.complete(row);
await batches.wait(single);const late={ordinal:3};batches.record(late);
console.log(JSON.stringify({error,late,groups:batches.describe()}));
""")
    assert "did not settle within observation deadline" in observed["error"]
    assert observed["groups"][0]["settled"] is False
    assert observed["late"]["late_arrival"] is True
    assert observed["groups"][1]["late_requests"] == 1
    assert observed["groups"][1]["settled"] is False


def test_wide_marker_obeys_actual_since_and_group_scalars_stay_distinct():
    observed = node(
        FIXTURE
        + """
const base='http://localhost/api/v2/metrics?name_prefix=codex.memory.&agent=codex&limit=500';
const options={rangeWindows:{wide_hours:168,narrow_hours:24}};
const read=since=>fixtureResponse(rows,'range-transition',
 base+'&since='+since,'wide-loaded',options);
const acrossSessions=[
 {...rows[0],session_id:'a'},
 {...rows[0],session_id:'b',occurrences:1,total_value:4,last_seen:'2026-11-07 11:00:00'},
];
console.log(JSON.stringify({wide:read('2026-10-31T12:05:00Z'),
 narrow:read('2026-11-06T12:05:00Z'),
 grouped:fixtureResponse(acrossSessions,'fixture',base,'initial'),
 scoped:fixtureResponse(acrossSessions,'fixture',base+'&session_id=b','initial')}));
"""
    )
    wide = observed["wide"]["body"]["metrics"]
    narrow = observed["narrow"]["body"]["metrics"]
    assert any(row["total_value"] == 99 for row in wide)
    assert not any(row["total_value"] == 99 for row in narrow)
    assert observed["grouped"]["body"]["metrics"] == [
        {
            "metric_name": "codex.memory.startup",
            "attrs": {"state": "succeeded", "phase": "one"},
            "occurrences": 3,
            "total_value": 13,
            "last_seen": "2026-11-07 12:00:00",
        }
    ]
    assert observed["scoped"]["body"]["metrics"][0]["occurrences"] == 1
    assert observed["scoped"]["body"]["metrics"][0]["total_value"] == 4
