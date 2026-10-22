"""Current authoritative ledger schema; new stores only, no migrations."""

SCHEMA = """
CREATE TABLE ledger_meta (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
);
CREATE TABLE task_heads (
    task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL
);
CREATE TABLE tasks (
    task_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
    parent_id TEXT REFERENCES task_heads(task_id), payload TEXT NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY(task_id,revision)
);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_revision INTEGER NOT NULL,
    worker_id TEXT NOT NULL, map_revision INTEGER NOT NULL, phase TEXT NOT NULL,
    checkpoint_id TEXT, outcome TEXT, started_at REAL NOT NULL, ended_at REAL,
    FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision)
);
CREATE UNIQUE INDEX active_worker ON runs(worker_id) WHERE ended_at IS NULL;
CREATE TABLE artifacts (
    hash TEXT PRIMARY KEY, size INTEGER NOT NULL CHECK(size>=0), created_at REAL NOT NULL
);
CREATE TABLE artifact_refs (
    owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, slot TEXT NOT NULL,
    hash TEXT NOT NULL REFERENCES artifacts(hash),
    PRIMARY KEY(owner_type,owner_id,slot)
);
CREATE INDEX artifact_ref_hash ON artifact_refs(hash);
CREATE TABLE cursors (
    run_id TEXT NOT NULL REFERENCES runs(run_id), stream_id TEXT NOT NULL,
    head_seq INTEGER NOT NULL DEFAULT 0, classified_seq INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    CHECK(classified_seq>=0 AND classified_seq<=head_seq), PRIMARY KEY(run_id,stream_id)
);
CREATE TABLE events (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stream_id TEXT NOT NULL,
    seq INTEGER NOT NULL, capture_key TEXT NOT NULL, kind TEXT NOT NULL,
    payload TEXT NOT NULL, payload_hash TEXT NOT NULL, disposition TEXT,
    created_at REAL NOT NULL,
    CHECK(disposition IS NULL OR disposition IN ('reduced','extracted','no_change','dropped','pending')),
    UNIQUE(run_id,stream_id,seq), UNIQUE(run_id,capture_key),
    FOREIGN KEY(run_id,stream_id) REFERENCES cursors(run_id,stream_id)
);
CREATE INDEX pending_events ON events(run_id,stream_id,seq) WHERE disposition='pending';
CREATE TABLE capture_sources (
    source_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    stream_id TEXT NOT NULL, revision INTEGER NOT NULL, generation TEXT NOT NULL,
    committed_offset INTEGER NOT NULL CHECK(committed_offset>=0),
    decoder_version TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE passes (
    pass_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stream_id TEXT NOT NULL,
    from_seq INTEGER NOT NULL, to_seq INTEGER NOT NULL CHECK(to_seq>=from_seq),
    task_revision INTEGER NOT NULL, cursor_revision INTEGER NOT NULL,
    event_digest TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
    FOREIGN KEY(run_id,stream_id) REFERENCES cursors(run_id,stream_id)
);
CREATE TABLE record_heads (
    record_id TEXT PRIMARY KEY, version INTEGER NOT NULL
);
CREATE TABLE records (
    record_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
    task_id TEXT NOT NULL REFERENCES task_heads(task_id), kind TEXT NOT NULL,
    payload TEXT NOT NULL, evidence TEXT NOT NULL, inputs TEXT NOT NULL,
    supersedes INTEGER, validity TEXT NOT NULL CHECK(validity IN ('current','invalid','dropped')),
    retention TEXT NOT NULL CHECK(retention IN ('context','store')),
    reason TEXT NOT NULL, consuming_step TEXT, expires_when TEXT NOT NULL,
    storage_bytes INTEGER NOT NULL CHECK(storage_bytes>=0),
    PRIMARY KEY(record_id,version)
);
CREATE INDEX task_records ON records(task_id,validity);
CREATE TABLE map_heads (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, record_id TEXT NOT NULL,
    version INTEGER NOT NULL, PRIMARY KEY(repository,snapshot,record_id)
);
CREATE TABLE map_records (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, record_id TEXT NOT NULL,
    version INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
    evidence TEXT NOT NULL, inputs TEXT NOT NULL, applicability TEXT NOT NULL,
    PRIMARY KEY(repository,snapshot,record_id,version)
);
CREATE TABLE record_entities (
    record_id TEXT NOT NULL, version INTEGER NOT NULL,
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, entity_id TEXT NOT NULL, entity_version INTEGER NOT NULL,
    PRIMARY KEY(record_id,version,repository,snapshot,entity_id),
    FOREIGN KEY(record_id,version) REFERENCES records(record_id,version),
    FOREIGN KEY(repository,snapshot,entity_id,entity_version)
        REFERENCES map_records(repository,snapshot,record_id,version)
);
CREATE TABLE receipts (
    receipt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    check_id TEXT NOT NULL, check_version TEXT NOT NULL, payload TEXT NOT NULL,
    inputs_hash TEXT NOT NULL, environment_hash TEXT NOT NULL,
    start_hash TEXT NOT NULL, end_hash TEXT NOT NULL, exit_code INTEGER NOT NULL,
    artifact_hash TEXT NOT NULL REFERENCES artifacts(hash), duration REAL NOT NULL,
    valid INTEGER NOT NULL CHECK(valid IN (0,1))
);
CREATE TABLE checkpoints (
    checkpoint_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    ledger_revision INTEGER NOT NULL, map_revision INTEGER NOT NULL,
    task_revision INTEGER NOT NULL, payload TEXT NOT NULL,
    packet_hash TEXT NOT NULL REFERENCES artifacts(hash), acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE retrieval_runs (
    retrieval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task_heads(task_id),
    input_hash TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(task_id,input_hash)
);
CREATE TABLE leases (
    repository TEXT NOT NULL, canonical_path TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), payload TEXT NOT NULL,
    PRIMARY KEY(repository,canonical_path)
);
CREATE TABLE outbox (
    operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL,
    lease_token TEXT, lease_until REAL, last_error TEXT,
    CHECK(status IN ('pending','claimed','delivered')), UNIQUE(kind,idempotency_key)
);
CREATE INDEX outbox_ready ON outbox(status,available_at,lease_until);
CREATE TABLE event_origins (
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES capture_sources(source_id),
    generation TEXT NOT NULL, byte_offset INTEGER NOT NULL,
    decoder_version TEXT NOT NULL,
    PRIMARY KEY(event_id,source_id,generation,byte_offset)
);
CREATE INDEX origin_source ON event_origins(source_id,generation,byte_offset);
CREATE TABLE artifact_chunks (
    hash TEXT NOT NULL REFERENCES artifacts(hash) ON DELETE CASCADE,
    offset INTEGER NOT NULL, size INTEGER NOT NULL, chunk_hash TEXT NOT NULL,
    PRIMARY KEY(hash,offset)
);
CREATE TABLE native_bindings (
    binding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    stream_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT NOT NULL,
    decoder_version TEXT NOT NULL, latest_usage TEXT,
    UNIQUE(adapter,session_id,stream_id),
    FOREIGN KEY(run_id,stream_id) REFERENCES cursors(run_id,stream_id)
);
CREATE TABLE native_deliveries (
    binding_id TEXT NOT NULL REFERENCES native_bindings(binding_id),
    delivery_id TEXT NOT NULL, payload_hash TEXT NOT NULL, event_ids TEXT NOT NULL,
    PRIMARY KEY(binding_id,delivery_id)
);
CREATE TABLE native_event_origins (
    binding_id TEXT NOT NULL, delivery_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    PRIMARY KEY(binding_id,delivery_id,event_id),
    FOREIGN KEY(binding_id,delivery_id) REFERENCES native_deliveries(binding_id,delivery_id)
);
CREATE TABLE progress_heads (
    task_id TEXT PRIMARY KEY REFERENCES task_heads(task_id), revision INTEGER NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), cursor_id TEXT NOT NULL REFERENCES record_heads(record_id)
);
CREATE TABLE progress_steps (
    task_id TEXT NOT NULL REFERENCES task_heads(task_id), step_id TEXT NOT NULL,
    status TEXT NOT NULL, payload TEXT NOT NULL, evidence TEXT NOT NULL,
    PRIMARY KEY(task_id,step_id)
);
CREATE TABLE progress_deliverables (
    task_id TEXT NOT NULL REFERENCES task_heads(task_id), path TEXT NOT NULL,
    payload TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(task_id,path)
);
CREATE TABLE progress_updates (
    run_id TEXT NOT NULL REFERENCES runs(run_id), request_hash TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(run_id,request_hash)
);
CREATE TABLE check_executions (
    execution_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_id TEXT NOT NULL, check_id TEXT NOT NULL, check_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','finished')),
    payload TEXT NOT NULL, UNIQUE(run_id,request_id)
);
CREATE INDEX reusable_receipts ON receipts(run_id,check_id,check_version,inputs_hash,environment_hash,valid);
CREATE TABLE map_snapshots (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, base_commit TEXT NOT NULL,
    import_path TEXT NOT NULL, kind TEXT NOT NULL, map_hash TEXT NOT NULL,
    PRIMARY KEY(repository,snapshot)
);
CREATE TABLE map_anchor_cache (
    repository TEXT NOT NULL, worktree TEXT NOT NULL, path TEXT NOT NULL,
    symbol TEXT NOT NULL, source_stamp TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(repository,worktree,path,symbol)
);
CREATE TABLE map_overlays (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, baseline TEXT NOT NULL,
    head_commit TEXT NOT NULL, branch TEXT NOT NULL, git_dir TEXT NOT NULL,
    PRIMARY KEY(repository,snapshot),
    FOREIGN KEY(repository,snapshot) REFERENCES map_snapshots(repository,snapshot)
);
CREATE TABLE map_relations (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, source TEXT NOT NULL,
    kind TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY(repository,snapshot,source,kind,target)
);
CREATE INDEX map_incoming ON map_relations(repository,snapshot,target,source);
CREATE TABLE map_patches (
    repository TEXT NOT NULL, patch_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_hash TEXT NOT NULL, result TEXT NOT NULL,
    PRIMARY KEY(repository,patch_id)
);
CREATE TABLE map_evidence (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, record_id TEXT NOT NULL,
    version INTEGER NOT NULL, event_id TEXT NOT NULL REFERENCES events(event_id),
    PRIMARY KEY(repository,snapshot,record_id,version,event_id),
    FOREIGN KEY(repository,snapshot,record_id,version)
        REFERENCES map_records(repository,snapshot,record_id,version)
);
CREATE TABLE map_sources (
    repository TEXT NOT NULL,snapshot TEXT NOT NULL,record_id TEXT NOT NULL,
    path TEXT NOT NULL,symbol TEXT NOT NULL,sha256 TEXT NOT NULL,
    PRIMARY KEY(repository,snapshot,record_id,path,symbol)
);
CREATE INDEX map_source_paths ON map_sources(repository,snapshot,path,record_id);
CREATE TABLE map_refresh_queue (
    repository TEXT NOT NULL,snapshot TEXT NOT NULL,record_id TEXT NOT NULL,generation INTEGER NOT NULL,
    PRIMARY KEY(repository,snapshot,record_id)
);
CREATE INDEX entity_consumers ON record_entities(repository,snapshot,entity_id,entity_version);
CREATE TABLE task_map_inputs (
    task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,repository TEXT NOT NULL,
    snapshot TEXT NOT NULL,entity_id TEXT NOT NULL,entity_version INTEGER NOT NULL,
    PRIMARY KEY(task_id,task_revision,repository,snapshot,entity_id),
    FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision),
    FOREIGN KEY(repository,snapshot,entity_id,entity_version) REFERENCES map_records(repository,snapshot,record_id,version)
);
CREATE INDEX task_entity_consumers ON task_map_inputs(repository,snapshot,entity_id,entity_version);
CREATE TABLE task_replan_queue (
    task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,repository TEXT NOT NULL,
    snapshot TEXT NOT NULL,entity_id TEXT NOT NULL,entity_version INTEGER NOT NULL,reason TEXT NOT NULL,
    PRIMARY KEY(task_id,task_revision,repository,snapshot,entity_id),
    FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision)
);
CREATE TABLE map_search_keys (
    rowid INTEGER PRIMARY KEY, repository TEXT NOT NULL,snapshot TEXT NOT NULL,record_id TEXT NOT NULL,
    UNIQUE(repository,snapshot,record_id)
);
CREATE VIRTUAL TABLE map_search USING fts5(summary,semantic);
CREATE TABLE plan_heads(plan_id TEXT PRIMARY KEY,revision INTEGER NOT NULL);
CREATE TABLE plans (
    plan_id TEXT NOT NULL,revision INTEGER NOT NULL,request_hash TEXT NOT NULL,payload TEXT NOT NULL,
    PRIMARY KEY(plan_id,revision)
);
CREATE TABLE plan_units (
    plan_id TEXT NOT NULL,plan_revision INTEGER NOT NULL,task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,
    PRIMARY KEY(plan_id,plan_revision,task_id),
    FOREIGN KEY(plan_id,plan_revision) REFERENCES plans(plan_id,revision),
    FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision)
);
CREATE TABLE plan_prerequisites (
    task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,producer TEXT NOT NULL,output_id TEXT NOT NULL,output_version INTEGER NOT NULL,
    PRIMARY KEY(task_id,task_revision,producer,output_id),
    FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision)
);
CREATE TABLE unit_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),workdir TEXT NOT NULL,payload TEXT NOT NULL
);
CREATE TABLE unit_completions (
    task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),payload TEXT NOT NULL,
    PRIMARY KEY(task_id,task_revision),FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision)
);
CREATE TABLE unit_outputs (
    task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,output_id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,
    PRIMARY KEY(task_id,task_revision,output_id),
    FOREIGN KEY(task_id,task_revision) REFERENCES unit_completions(task_id,task_revision)
);
CREATE TABLE unit_materializations (
    task_id TEXT NOT NULL,task_revision INTEGER NOT NULL,producer TEXT NOT NULL,output_id TEXT NOT NULL,
    integration_run TEXT NOT NULL REFERENCES runs(run_id),payload TEXT NOT NULL,
    PRIMARY KEY(task_id,task_revision,producer,output_id),FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision)
);
CREATE TABLE context_requests (
    run_id TEXT NOT NULL REFERENCES runs(run_id),request_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,request_hash TEXT NOT NULL,
    PRIMARY KEY(run_id,request_id)
);
CREATE TABLE native_producer_queue (
    binding_id TEXT NOT NULL REFERENCES native_bindings(binding_id), delivery_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL, payload TEXT NOT NULL, bytes INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
    PRIMARY KEY(binding_id,delivery_id)
);
CREATE TABLE native_capture_gaps (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id), reason TEXT NOT NULL
);
CREATE TABLE native_launch_contracts (
    launch_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    worker_id TEXT NOT NULL, adapter TEXT NOT NULL
);
CREATE TABLE native_tool_calls (
    launch_id TEXT NOT NULL REFERENCES native_launches(launch_id),
    session_id TEXT NOT NULL, actor_id TEXT NOT NULL, call_id TEXT NOT NULL,
    input_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('admitted','settled','denied')),
    PRIMARY KEY(launch_id,session_id,actor_id,call_id)
);
CREATE INDEX native_tools_pending ON native_tool_calls(launch_id,status);
CREATE TABLE native_children (
    launch_id TEXT NOT NULL REFERENCES native_launches(launch_id),
    actor_id TEXT NOT NULL, session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','settled')),
    PRIMARY KEY(launch_id,actor_id)
);
CREATE INDEX native_child_sessions ON native_children(launch_id,session_id);
CREATE INDEX capture_run_sources ON capture_sources(run_id,source_id);
CREATE TABLE companion_jobs (
                    job_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id), pass_id TEXT UNIQUE NOT NULL REFERENCES passes(pass_id),
                    worker_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX companion_job_status ON companion_jobs(status);

CREATE TABLE learning_cursors (
    agent_id TEXT NOT NULL,stream_id TEXT NOT NULL,assessed_seq INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(agent_id,stream_id)
);
CREATE TABLE learning_jobs (
    job_id TEXT PRIMARY KEY,request_key TEXT NOT NULL UNIQUE,agent_id TEXT NOT NULL,
    stream_id TEXT NOT NULL,run_id TEXT,status TEXT NOT NULL,payload TEXT NOT NULL
);
CREATE INDEX learning_job_status ON learning_jobs(status);
CREATE TABLE native_reads (
                    launch_id TEXT NOT NULL, session_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                    call_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, read_key TEXT NOT NULL,
                    source_stamp TEXT NOT NULL, success INTEGER NOT NULL,
                    PRIMARY KEY(launch_id,session_id,actor_id,call_id));
CREATE INDEX native_read_reuse ON native_reads(launch_id,session_id,actor_id,checkpoint_id,read_key,source_stamp);
CREATE TABLE runtime_cycles(scope TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE tool_executions(run_id TEXT NOT NULL,request_id TEXT NOT NULL,input_hash TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,request_id));
CREATE TABLE map_source_observations(repository TEXT NOT NULL,snapshot TEXT NOT NULL,path TEXT NOT NULL,stamp TEXT NOT NULL,PRIMARY KEY(repository,snapshot,path));
CREATE TABLE map_publications(run_id TEXT NOT NULL REFERENCES runs(run_id),repository TEXT NOT NULL,snapshot TEXT NOT NULL,record_id TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(run_id,repository,snapshot,record_id));
CREATE INDEX map_publication_lookup ON map_publications(repository,snapshot,record_id,version);
CREATE TABLE map_aliases(repository TEXT NOT NULL,snapshot TEXT NOT NULL,record_id TEXT NOT NULL,version INTEGER NOT NULL,term TEXT NOT NULL,PRIMARY KEY(repository,snapshot,record_id,term));
CREATE INDEX map_alias_lookup ON map_aliases(repository,snapshot,term);
CREATE TABLE search_calls(launch_id TEXT NOT NULL,session_id TEXT NOT NULL,actor_id TEXT NOT NULL,call_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(launch_id,session_id,actor_id,call_id));
CREATE TABLE "native_launches"(launch_id TEXT PRIMARY KEY REFERENCES native_launch_contracts(launch_id),run_id TEXT NOT NULL REFERENCES runs(run_id),request_id TEXT NOT NULL,request_hash TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,error TEXT);
CREATE UNIQUE INDEX active_native_launch ON native_launches(run_id) WHERE status<>'archived';
INSERT INTO ledger_meta VALUES(1,0);
"""
