"""Behavior contracts for selected, bounded, non-authoritative observations."""

import json
from pathlib import Path

import pytest

from Compilation.Hooks import (CompilerHooks, CaptureCompilerActions,
    EmitCompilerAction, RunCompilerOperation, ObserveCompilerRun,
    ReadCompilerTrace, DiagnoseCompilerTrace)
from Compilation.Pipeline import CompileSvToLitematic


def test_stage_selection_detaches_fields_and_callback_records():
    Seen = []
    def Callback(Event):
        Seen.append(Event['Stage'])
        Event['Fields']['Values'].append('callback mutation')
    Hooks = CompilerHooks(Stages=('physical',), Callback=Callback)
    Values = ['original']
    with CaptureCompilerActions(Hooks):
        EmitCompilerAction('frontend.parse', 'begin')
        EmitCompilerAction('physical.routing', 'selected', Values=Values)
        EmitCompilerAction('physicality', 'begin')
    assert Hooks.WaitForCallbacks()
    Hooks.Close()
    Values.append('later mutation')
    assert Seen == ['physical.routing']
    assert Hooks.Document()['Events'][0]['Fields']['Values'] == ['original']
    Copy = Hooks.Document()
    Copy['Events'].clear()
    assert len(Hooks.Document()['Events']) == 1


def test_overflow_and_invalid_fields_are_explicit_and_bounded():
    Hooks = CompilerHooks(MaxEvents=2, MaxBytes=1024)
    with CaptureCompilerActions(Hooks):
        EmitCompilerAction('parse', 'begin', Invalid=object())
        EmitCompilerAction('parse', 'begin', Huge='x' * 5000)
        for _ in range(10):
            EmitCompilerAction('parse', 'progress')
    Document = Hooks.Document()
    assert len(Document['Events']) == 2
    assert Document['DroppedEvents'] == 10
    assert sum(len(json.dumps(Event, sort_keys=True, separators=(',', ':')).encode()) for Event in Document['Events']) <= 1024
    Tiny = CompilerHooks(MaxBytes=1)
    Tiny.Emit('parse', 'begin')
    assert Tiny.Document()['Events'] == []
    assert Tiny.Document()['DroppedEvents'] == 1


def test_observer_error_and_reentry_preserve_operation_result_and_exception():
    Hooks = CompilerHooks()
    def BrokenCallback(Event):
        EmitCompilerAction('parse', 'recursive')
        raise ValueError('observer failure')
    Hooks.Callback = BrokenCallback
    Sentinel = object()
    Error = RuntimeError('compiler failure')
    def Fail():
        raise Error
    with CaptureCompilerActions(Hooks):
        assert RunCompilerOperation('parse', lambda: Sentinel) is Sentinel
        with pytest.raises(RuntimeError) as Caught:
            RunCompilerOperation('parse', Fail)
    assert Caught.value is Error
    assert Hooks.WaitForCallbacks()
    Hooks.Close()
    assert [Event['Action'] for Event in Hooks.Document()['Events']] == ['begin', 'finish', 'begin', 'failed']
    assert Hooks.CallbackErrors == 4
    assert Hooks.DroppedEvents == 4


def test_nested_capture_restores_outer_and_disabled_operation():
    Outer, Inner = CompilerHooks(), CompilerHooks()
    with CaptureCompilerActions(Outer):
        EmitCompilerAction('parse', 'before')
        with CaptureCompilerActions(Inner):
            EmitCompilerAction('parse', 'inside')
        EmitCompilerAction('parse', 'after')
    EmitCompilerAction('parse', 'disabled')
    assert [Event['Action'] for Event in Outer.Document()['Events']] == ['before', 'after']
    assert [Event['Action'] for Event in Inner.Document()['Events']] == ['inside']


def test_failed_real_frontend_is_saved_and_diagnosable(tmp_path):
    Source = tmp_path / 'Broken.sv'
    Source.write_text('module broken(input a, output y); assign y = ; endmodule')
    Output = tmp_path / 'Broken.litematic'
    Hooks = CompilerHooks(Stages=('frontend',))
    with pytest.raises(Exception) as Caught:
        CompileSvToLitematic(InputPath=Source, OutputPath=Output,
            DiagramPath=tmp_path / 'Diagram.json', Workdir=tmp_path / 'Frontend', Hooks=Hooks)
    assert not Output.exists()
    assert not Output.with_suffix('.CompilerTrace.json').exists()
    Document = ReadCompilerTrace(Output.with_suffix('.CompilerHooks'))
    Diagnosis = DiagnoseCompilerTrace(Document)
    assert Diagnosis['Outcome'] == 'failed'
    assert Diagnosis['FailedOperations'] == ['frontend.parse']
    assert Document['Failure']['ErrorType'] == type(Caught.value).__name__
    assert Diagnosis['Truncated'] is False


def test_trace_io_failure_cannot_replace_original_error(tmp_path):
    Error = RuntimeError('original')
    @ObserveCompilerRun
    def Compile(*, OutputPath, Hooks):
        raise Error
    BlockingFile = tmp_path / 'file'
    BlockingFile.write_text('not a directory')
    Hooks = CompilerHooks()
    with pytest.raises(RuntimeError) as Caught:
        Compile(OutputPath=BlockingFile / 'Design.litematic', Hooks=Hooks)
    assert Caught.value is Error
    assert Hooks.WriteError is not None


def test_completed_run_round_trip_and_reader_rejects_invalid_input(tmp_path):
    @ObserveCompilerRun
    def Compile(*, OutputPath, Hooks):
        return 37
    Output = tmp_path / 'Design.litematic'
    Hooks = CompilerHooks()
    assert Compile(OutputPath=Output, Hooks=Hooks) == 37
    PathValue = Output.with_suffix('.CompilerHooks')
    assert DiagnoseCompilerTrace(ReadCompilerTrace(PathValue))['Outcome'] == 'completed'
    with pytest.raises(ValueError):
        ReadCompilerTrace(PathValue, MaxFileBytes=1)
    (PathValue / 'Index.json').write_text('{"SchemaVersion":"unknown","Events":[]}')
    with pytest.raises(ValueError):
        ReadCompilerTrace(PathValue)


def test_blocked_listener_does_not_block_compiler_operation():
    from threading import Event
    Started, Release = Event(), Event()
    def Listener(Record):
        Started.set()
        Release.wait(2)
    Hooks = CompilerHooks(Callback=Listener)
    try:
        with CaptureCompilerActions(Hooks):
            EmitCompilerAction('parse', 'begin')
            assert Started.wait(1)
            assert RunCompilerOperation('parse', lambda: 73) == 73
        assert not Hooks.WaitForCallbacks(0)
        assert Hooks.Document()['PendingCallbacks'] > 0
    finally:
        Release.set()
        assert Hooks.WaitForCallbacks()
        Hooks.Close()


def test_unavailable_callback_thread_retains_observation_and_result(monkeypatch):
    from threading import Thread
    def Unavailable(Self):
        raise RuntimeError('no thread available')
    monkeypatch.setattr(Thread, 'start', Unavailable)
    Hooks = CompilerHooks(Callback=lambda Event: None)
    with CaptureCompilerActions(Hooks):
        assert RunCompilerOperation('parse', lambda: 23) == 23
    Document = Hooks.Document()
    assert [Event['Action'] for Event in Document['Events']] == ['begin', 'finish']
    assert Document['DroppedCallbacks'] == 1
    assert Document['PendingCallbacks'] == 0
    Hooks.Close()



def test_split_stage_files_reconstruct_global_order_and_repeat_invocations(tmp_path):
    Hooks = CompilerHooks()
    for Stage, Action in [('parse', 'begin'), ('../physical/漢字', 'begin'),
                          ('parse', 'finish'), ('parse', 'begin'), ('parse', 'finish')]:
        Hooks.Emit(Stage, Action)
    Directory = tmp_path / 'Design.CompilerHooks'
    Hooks.SaveHooks(Directory)
    Index = json.loads((Directory / 'Index.json').read_text())
    assert len(Index['Files']) == 2
    assert Index['EventCount'] == 5
    assert {Entry['Stage'] for Entry in Index['Files']} == {'parse', '../physical/漢字'}
    for Entry in Index['Files']:
        assert '/' not in Entry['Name'] and '..' not in Entry['Name']
        Member = ReadCompilerTrace(Directory / Entry['Name'])
        assert Member['CoverageScope'] == 'stage'
        assert {Record['Stage'] for Record in Member['Events']} == {Entry['Stage']}
    assert ReadCompilerTrace(Directory)['Events'] == Hooks.Document()['Events']
    assert ReadCompilerTrace(Directory / 'Index.json')['Events'] == Hooks.Document()['Events']
    assert Hooks.Publication == {'Status': 'Saved', 'DirectoryPath': str(Directory),
        'IndexPath': str(Directory / 'Index.json'), 'HookFileCount': 2,
        'EventCount': 5, 'DroppedEvents': 0, 'WriteError': None}
    Snapshot = Hooks.Publication
    Snapshot['Status'] = 'bad'
    assert Hooks.Publication['Status'] == 'Saved'


def test_split_caps_remain_shared_and_filtered_or_unreached_stages_have_no_files(tmp_path):
    Hooks = CompilerHooks(Stages=('physical',), MaxEvents=2)
    Hooks.Emit('frontend', 'begin')
    for Stage in ['physical.place', 'physical.route', 'physical.check']:
        Hooks.Emit(Stage, 'begin')
    Directory = tmp_path / 'Design.CompilerHooks'
    Hooks.SaveHooks(Directory)
    Document = ReadCompilerTrace(Directory)
    assert [Record['Stage'] for Record in Document['Events']] == ['physical.place', 'physical.route']
    assert Document['DroppedEvents'] == 1
    assert Document['SelectedStages'] == ['physical']
    Index = json.loads((Directory / 'Index.json').read_text())
    assert len(Index['Files']) == 2
    assert Hooks.Publication['HookFileCount'] == 2
    Empty = CompilerHooks(Stages=('unreached',))
    Empty.SaveHooks(tmp_path / 'Empty')
    assert ReadCompilerTrace(tmp_path / 'Empty')['Events'] == []
    assert Empty.Publication['HookFileCount'] == 0


def test_split_legacy_readback_remains_available(tmp_path):
    Legacy = tmp_path / 'Legacy.CompilerTrace.json'
    Legacy.write_text(json.dumps({'SchemaVersion':'compiler-action-trace-v1',
        'Outcome':'failed', 'Events':[{'Stage':'parse', 'Action':'failed'}]}))
    assert DiagnoseCompilerTrace(ReadCompilerTrace(Legacy))['FailedOperations'] == ['parse']


def test_split_reader_rejects_missing_changed_unsafe_and_symlink_members(tmp_path):
    Hooks = CompilerHooks()
    Hooks.Emit('parse', 'begin')
    Directory = tmp_path / 'Design.CompilerHooks'
    Hooks.SaveHooks(Directory)
    IndexPath = Directory / 'Index.json'
    IndexBytes = IndexPath.read_bytes()
    Index = json.loads(IndexBytes)
    Entry = Index['Files'][0]
    Member = Directory / Entry['Name']
    Original = Member.read_bytes()
    Member.unlink()
    with pytest.raises(OSError):
        ReadCompilerTrace(Directory)
    Outside = tmp_path / 'Outside.json'
    Outside.write_bytes(Original)
    Member.symlink_to(Outside)
    with pytest.raises(OSError):
        ReadCompilerTrace(Directory)
    Member.unlink()
    Member.write_bytes(Original + b' ')
    with pytest.raises(ValueError):
        ReadCompilerTrace(Directory)
    Member.write_bytes(Original)
    Index['Files'][0]['Name'] = '../Outside.json'
    IndexPath.write_text(json.dumps(Index))
    with pytest.raises(ValueError):
        ReadCompilerTrace(Directory)
    IndexPath.write_bytes(IndexBytes)
    with pytest.raises(ValueError):
        ReadCompilerTrace(Directory, MaxFileBytes=len(IndexBytes))
    assert ReadCompilerTrace(Directory)['Events'] == Hooks.Document()['Events']


@pytest.mark.parametrize('Mutation', ['duplicate', 'wrong-stage', 'sequence', 'count', 'metadata'])
def test_split_reader_rejects_semantically_corrupt_inventory_even_with_updated_hashes(tmp_path, Mutation):
    from hashlib import sha256
    Hooks = CompilerHooks()
    Hooks.Emit('parse', 'begin')
    Hooks.Emit('parse', 'finish')
    Directory = tmp_path / 'Design.CompilerHooks'
    Hooks.SaveHooks(Directory)
    IndexPath = Directory / 'Index.json'
    Index = json.loads(IndexPath.read_text())
    Entry = Index['Files'][0]
    MemberPath = Directory / Entry['Name']
    Member = json.loads(MemberPath.read_text())
    if Mutation == 'duplicate':
        Index['Files'].append(dict(Entry))
    elif Mutation == 'wrong-stage':
        Member['Events'][0]['Stage'] = 'other'
    elif Mutation == 'sequence':
        Member['Events'][1]['Sequence'] = 0
    elif Mutation == 'count':
        Index['EventCount'] = 1
    elif Mutation == 'metadata':
        Member['Outcome'] = 'completed'
    Payload = json.dumps(Member).encode()
    MemberPath.write_bytes(Payload)
    Entry['SizeBytes'] = len(Payload)
    Entry['Sha256'] = sha256(Payload).hexdigest()
    IndexPath.write_text(json.dumps(Index))
    with pytest.raises(ValueError):
        ReadCompilerTrace(Directory)


def test_split_publication_failure_preserves_existing_directory_and_compile_result(tmp_path):
    Directory = tmp_path / 'Design.CompilerHooks'
    Directory.mkdir()
    Marker = Directory / 'keep.txt'
    Marker.write_text('preserved')
    @ObserveCompilerRun
    def Compile(*, OutputPath, Hooks):
        return 73
    Hooks = CompilerHooks()
    assert Compile(OutputPath=tmp_path / 'Design.litematic', Hooks=Hooks) == 73
    assert Marker.read_text() == 'preserved'
    assert Hooks.Publication['Status'] == 'Unavailable'
    assert Hooks.Publication['IndexPath'] is None
    assert Hooks.Publication['WriteError'] == 'FileExistsError'


def test_split_partial_write_never_publishes_index_or_masks_original_failure(tmp_path, monkeypatch):
    Hooks = CompilerHooks()
    Error = RuntimeError('compiler failed')
    @ObserveCompilerRun
    def Compile(*, OutputPath, Hooks):
        raise Error
    import os
    def BrokenOpen(*Arguments, **Keywords):
        raise OSError('storage failed')
    monkeypatch.setattr(os, 'fdopen', BrokenOpen)
    with pytest.raises(RuntimeError) as Caught:
        Compile(OutputPath=tmp_path / 'Design.litematic', Hooks=Hooks)
    assert Caught.value is Error
    assert not (tmp_path / 'Design.CompilerHooks' / 'Index.json').exists()
    assert Hooks.Publication['Status'] == 'Unavailable'
    with pytest.raises(OSError):
        ReadCompilerTrace(tmp_path / 'Design.CompilerHooks')



def test_split_safe_filenames_distinguish_stage_slug_collisions(tmp_path):
    Hooks = CompilerHooks()
    for Stage in ['a.b', 'a/b', 'a_b']:
        Hooks.Emit(Stage, 'begin')
    Directory = tmp_path / 'Hooks'
    Hooks.SaveHooks(Directory)
    Index = json.loads((Directory / 'Index.json').read_text())
    assert len({Entry['Name'] for Entry in Index['Files']}) == 3
    assert {Entry['Stage'] for Entry in Index['Files']} == {'a.b', 'a/b', 'a_b'}
    assert [Record['Stage'] for Record in ReadCompilerTrace(Directory)['Events']] == ['a.b', 'a/b', 'a_b']


def test_split_byte_budget_is_shared_across_stages_and_index_cannot_downgrade(tmp_path):
    Hooks = CompilerHooks(MaxBytes=200)
    for Number in range(10):
        Hooks.Emit('stage' + str(Number), 'begin')
    Directory = tmp_path / 'Hooks'
    Hooks.SaveHooks(Directory)
    Document = ReadCompilerTrace(Directory)
    assert len(Document['Events']) < 10
    assert Document['DroppedEvents'] > 0
    assert sum(len(json.dumps(Event, sort_keys=True, separators=(',', ':')).encode()) for Event in Document['Events']) <= 200
    (Directory / 'Index.json').write_text(json.dumps({'SchemaVersion':'compiler-action-trace-v1', 'Events':[]}))
    with pytest.raises(ValueError):
        ReadCompilerTrace(Directory)



def test_normal_run_creates_no_hook_artifacts_and_hooked_run_preserves_result(tmp_path):
    @ObserveCompilerRun
    def Compile(*, OutputPath, Hooks=None):
        return 41
    Normal = tmp_path / 'Normal.litematic'
    Hooked = tmp_path / 'Hooked.litematic'
    assert Compile(OutputPath=Normal) == 41
    assert not Normal.with_suffix('.CompilerHooks').exists()
    assert not Normal.with_suffix('.CompilerTrace.json').exists()
    Hooks = CompilerHooks()
    assert Hooks.Publication['Status'] == 'NotRun'
    assert Compile(OutputPath=Hooked, Hooks=Hooks) == 41
    assert ReadCompilerTrace(Hooked.with_suffix('.CompilerHooks'))['Outcome'] == 'completed'
    assert Hooks.Publication['Status'] == 'Saved'



@pytest.mark.parametrize('SwapPoint', ['after-reservation', 'during-member-open', 'during-index-publication'])
def test_split_directory_swap_cannot_redirect_writes_or_claim_saved(tmp_path, monkeypatch, SwapPoint):
    import os
    Directory = tmp_path / 'Owned.CompilerHooks'
    Moved = tmp_path / 'MovedOwned'
    Outside = tmp_path / 'Outside'
    Outside.mkdir()
    Sentinel = Outside / 'Index.json'
    Sentinel.write_text('untouched outside artifact')
    Before = {File.name: File.read_bytes() for File in Outside.iterdir()}
    Swapped = False
    OriginalStat, OriginalOpen, OriginalLink = os.stat, os.open, os.link
    def Swap():
        nonlocal Swapped
        Directory.rename(Moved)
        Directory.symlink_to(Outside, target_is_directory=True)
        Swapped = True
    def Observe(Name, *Arguments, **Keywords):
        if SwapPoint == 'after-reservation' and Name == Directory.name and Keywords.get('dir_fd') is not None and not Swapped:
            Swap()
        return OriginalStat(Name, *Arguments, **Keywords)
    def Open(Name, *Arguments, **Keywords):
        if SwapPoint == 'during-member-open' and isinstance(Name, str) and Name.startswith('Hook-') and not Swapped:
            Swap()
        return OriginalOpen(Name, *Arguments, **Keywords)
    def Publish(Source, Target, *Arguments, **Keywords):
        if SwapPoint == 'during-index-publication' and Target == 'Index.json' and not Swapped:
            Swap()
        return OriginalLink(Source, Target, *Arguments, **Keywords)
    monkeypatch.setattr(os, 'stat', Observe)
    monkeypatch.setattr(os, 'open', Open)
    monkeypatch.setattr(os, 'link', Publish)
    @ObserveCompilerRun
    def Compile(*, OutputPath, Hooks):
        return 73
    Hooks = CompilerHooks()
    assert Compile(OutputPath=tmp_path / 'Owned.litematic', Hooks=Hooks) == 73
    assert Swapped
    assert Before == {File.name: File.read_bytes() for File in Outside.iterdir()}
    assert Hooks.Publication['Status'] == 'Unavailable'
    assert Hooks.Publication['IndexPath'] is None
    assert Hooks.WriteError is not None


def test_maximum_default_stage_inventory_with_long_metadata_is_default_readable(tmp_path):
    Stages = tuple(('s' + str(Number).zfill(4) + '-' + 'x' * 122) for Number in range(4096))
    Hooks = CompilerHooks(Stages=Stages)
    Hooks.Run = {'InputPath': 'x' * 2048, 'OutputPath': 'y' * 2048}
    for Stage in Stages:
        Hooks.Emit(Stage, 'begin')
    assert len(Hooks.Document()['Events']) == 4096
    Directory = tmp_path / 'Maximum.CompilerHooks'
    Hooks.SaveHooks(Directory)
    assert Hooks.Publication['Status'] == 'Saved'
    assert Hooks.Publication['HookFileCount'] == 4096
    TotalBytes = sum(File.stat().st_size for File in Directory.iterdir())
    assert TotalBytes <= 8_000_000
    Document = ReadCompilerTrace(Directory)
    assert len(Document['Events']) == 4096
    assert Document['SelectedStages'] == list(Stages)
    assert Document['Run'] == Hooks.Run


def test_storage_limit_preflight_and_stage_read_charge_each_file_once(tmp_path):
    TooSmall = CompilerHooks(MaxStorageBytes=1)
    TooSmall.Emit('parse', 'begin')
    Directory = tmp_path / 'TooSmall'
    TooSmall.SaveHooks(Directory)
    assert TooSmall.Publication['Status'] == 'Unavailable'
    assert not Directory.exists()
    Hooks = CompilerHooks()
    Hooks.Emit('parse', 'begin')
    Hooks.Emit('render', 'begin')
    Directory = tmp_path / 'Readable'
    Hooks.SaveHooks(Directory)
    Index = json.loads((Directory / 'Index.json').read_text())
    TotalBytes = (Directory / 'Index.json').stat().st_size + sum(Entry['SizeBytes'] for Entry in Index['Files'])
    Member = Directory / Index['Files'][0]['Name']
    assert ReadCompilerTrace(Member, MaxFileBytes=TotalBytes)['CoverageScope'] == 'stage'
    with pytest.raises(ValueError):
        ReadCompilerTrace(Member, MaxFileBytes=TotalBytes - 1)


def test_previous_full_metadata_stage_files_remain_readable(tmp_path):
    from hashlib import sha256
    Hooks = CompilerHooks()
    Hooks.Emit('parse', 'begin')
    Directory = tmp_path / 'LegacySplit'
    Hooks.SaveHooks(Directory)
    IndexPath = Directory / 'Index.json'
    Index = json.loads(IndexPath.read_text())
    Header = {Key: Value for Key, Value in Index.items() if Key not in ('SchemaVersion', 'Files', 'EventCount')}
    Entry = Index['Files'][0]
    Member = {**Header, 'SchemaVersion': 'compiler-action-stage-v1',
        'Stage': 'parse', 'Events': Hooks.Document()['Events']}
    Payload = json.dumps(Member).encode()
    (Directory / Entry['Name']).write_bytes(Payload)
    Entry['Sha256'], Entry['SizeBytes'] = sha256(Payload).hexdigest(), len(Payload)
    IndexPath.write_text(json.dumps(Index))
    assert ReadCompilerTrace(Directory)['Events'] == Hooks.Document()['Events']



def test_replacement_directory_with_existing_artifacts_is_rejected_before_writing(tmp_path, monkeypatch):
    import os
    Directory = tmp_path / 'Owned.CompilerHooks'
    Outside = tmp_path / 'Outside'
    Outside.mkdir()
    (Outside / 'Index.json').write_text('preserved')
    OriginalOpen = os.open
    Swapped = False
    def Open(Name, Flags, *Arguments, **Keywords):
        nonlocal Swapped
        if Flags & os.O_DIRECTORY and Keywords.get('dir_fd') is not None and not Swapped:
            if Directory.exists():
                Directory.rename(tmp_path / 'Original')
            Outside.rename(Directory)
            Swapped = True
        return OriginalOpen(Name, Flags, *Arguments, **Keywords)
    monkeypatch.setattr(os, 'open', Open)
    Hooks = CompilerHooks()
    Hooks.Emit('parse', 'begin')
    Hooks.SaveHooks(Directory)
    assert Swapped
    assert Hooks.Publication['Status'] == 'Unavailable'
    assert {File.name: File.read_text() for File in Directory.iterdir()} == {'Index.json':'preserved'}


@pytest.mark.parametrize('Raises', [False, True])
def test_empty_competing_directory_during_reservation_cannot_be_adopted(tmp_path, monkeypatch, Raises):
    import os
    Directory = tmp_path / 'Owned.CompilerHooks'
    Replacement = tmp_path / 'Replacement'
    Replacement.mkdir()
    OriginalOpen = os.open
    Swapped = False
    def Open(Name, Flags, *Arguments, **Keywords):
        nonlocal Swapped
        if Flags & os.O_DIRECTORY and Keywords.get('dir_fd') is not None and not Swapped:
            if Directory.exists():
                Directory.rename(tmp_path / 'Original')
            Replacement.rename(Directory)
            Swapped = True
        return OriginalOpen(Name, Flags, *Arguments, **Keywords)
    monkeypatch.setattr(os, 'open', Open)
    Result = object()
    Error = RuntimeError('original compiler failure')
    @ObserveCompilerRun
    def Compile(*, OutputPath, Hooks):
        if Raises:
            raise Error
        return Result
    Hooks = CompilerHooks()
    if Raises:
        with pytest.raises(RuntimeError) as Caught:
            Compile(OutputPath=tmp_path / 'Owned.litematic', Hooks=Hooks)
        assert Caught.value is Error
    else:
        assert Compile(OutputPath=tmp_path / 'Owned.litematic', Hooks=Hooks) is Result
    assert Swapped
    assert list(Directory.iterdir()) == []
    assert Hooks.Publication['Status'] == 'Unavailable'
    assert Hooks.Publication['IndexPath'] is None
