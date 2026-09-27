"""Portable, inert routing-failure report receipts and byte validation.

Receipts bind retained report bytes to retained failure bytes. They are evidence
integrity records, not signatures, routing certificates, or acceptance authority.
"""
from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import stat
import secrets
from typing import Mapping

ReportName = "RoutingFailureReport.html"
ReceiptName = "RoutingFailureReport.receipt.json"
ReceiptVersion = "routing-failure-report-receipt-v1"
MaximumReportBytes = 256 * 1024
MaximumReceiptBytes = 4096
ReportNames = frozenset({ReportName, ReceiptName})
ReportCsp = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
ReportStyle = """
body{font:16px/1.5 system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 24px;color:#17252f;background:#f5f7fa}
h1{font-size:32px}h2{margin-top:32px;border-bottom:2px solid #ccd7df}article{background:white;padding:20px;margin:20px 0;border-left:4px solid #b85426}
dt{font-weight:650;margin-top:12px}dd{margin:4px 0 12px}pre{font:14px/1.5 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
table{border-collapse:collapse;width:100%}th,td{text-align:left;border:1px solid #ccd7df;padding:10px}p{overflow-wrap:anywhere}
"""


def FileIdentity(Name: str, Data: bytes) -> dict[str, object]:
    return {"Name": Name, "SizeBytes": len(Data), "Sha256": sha256(Data).hexdigest()}


def BuildReportReceipt(FailureName: str, FailureBytes: bytes, ReportBytes: bytes) -> bytes:
    Value = {"SchemaVersion": ReceiptVersion,
             "Source": FileIdentity(FailureName, FailureBytes),
             "Report": FileIdentity(ReportName, ReportBytes)}
    return (json.dumps(Value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _UniqueObject(Pairs):
    Result = {}
    for Key, Value in Pairs:
        if Key in Result:
            raise ValueError("duplicate receipt key")
        Result[Key] = Value
    return Result


class _InertHtml(HTMLParser):
    """Conservative v1 markup grammar; no browser execution or URL resolution."""
    Tags = frozenset({"html", "head", "meta", "title", "style", "body", "h1", "h2",
                      "h3", "p", "dl", "dt", "dd", "pre", "article", "table", "tr", "th", "td"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.Stack = []
        self.Counts = {}
        self.CspCount = 0
        self.Style = []

    def handle_starttag(self, Tag, Attributes):
        if Tag not in self.Tags or len(dict(Attributes)) != len(Attributes):
            raise ValueError("unsafe or duplicate HTML tag/attribute")
        Attrs = dict(Attributes)
        Allowed = ({"lang": "en"},) if Tag == "html" else ({},)
        if Tag == "meta":
            Allowed = ({"charset": "utf-8"},
                       {"name": "viewport", "content": "width=device-width, initial-scale=1"},
                       {"http-equiv": "Content-Security-Policy", "content": ReportCsp})
            if Attrs == Allowed[-1]:
                self.CspCount += 1
            if not self.Stack or self.Stack[-1] != "head":
                raise ValueError("metadata outside document head")
        if Attrs not in Allowed:
            raise ValueError("unsafe HTML attributes")
        if Tag == "style" and (not self.Stack or self.Stack[-1] != "head"):
            raise ValueError("style outside head")
        self.Counts[Tag] = self.Counts.get(Tag, 0) + 1
        if Tag != "meta":
            self.Stack.append(Tag)

    def handle_endtag(self, Tag):
        if not self.Stack or self.Stack.pop() != Tag:
            raise ValueError("unbalanced HTML")

    def handle_startendtag(self, Tag, Attributes):
        if Tag != "meta":
            raise ValueError("unsupported self-closing tag")
        self.handle_starttag(Tag, Attributes)

    def handle_data(self, Data):
        if self.Stack and self.Stack[-1] == "style":
            self.Style.append(Data)

    def handle_decl(self, Declaration):
        if Declaration.lower() != "doctype html":
            raise ValueError("unsupported declaration")

    def handle_pi(self, Data):
        raise ValueError("processing instruction")

    def Check(self):
        if self.Stack or self.CspCount != 1:
            raise ValueError("incomplete document or CSP")
        if any(self.Counts.get(Tag) != 1 for Tag in ("html", "head", "body", "title")):
            raise ValueError("missing or duplicate document sections")
        if self.Counts.get("style", 0) > 1 or (self.Style and "".join(self.Style) != ReportStyle):
            raise ValueError("unsupported stylesheet")


def _IdentityMatches(Data: bytes, Record: object) -> bool:
    return (isinstance(Record, dict)
            and type(Record.get("SizeBytes", Record.get("Bytes"))) is int
            and Record.get("SizeBytes", Record.get("Bytes")) == len(Data)
            and Record.get("Sha256") == sha256(Data).hexdigest())


def ValidateReportPair(*, FailureName: str, FailureData: bytes,
                       ReportData: bytes | None, ReceiptData: bytes | None,
                       ExpectedReport: object = None, ExpectedReceipt: object = None,
                       RequireInventory: bool = False, SourceIdentity: object = None) -> dict[str, object]:
    """Validate immutable observations; callers own safe file/member selection."""
    def Result(Status, Reason):
        return {"Status": Status, "Reason": Reason}
    if ReportData is None and ReceiptData is None:
        return Result("Rejected" if ExpectedReport or ExpectedReceipt else "Unavailable",
                      "listed report missing" if ExpectedReport or ExpectedReceipt else "report not retained")
    if ReportData is None or ReceiptData is None:
        return Result("Rejected", "report and receipt must both be retained")
    if len(ReportData) > MaximumReportBytes or len(ReceiptData) > MaximumReceiptBytes:
        return Result("Rejected", "report evidence exceeds reader limits")
    if RequireInventory and (ExpectedReport is None or ExpectedReceipt is None):
        return Result("Rejected", "unlisted report or receipt")
    for Data, Expected in ((ReportData, ExpectedReport), (ReceiptData, ExpectedReceipt)):
        if Expected is not None and not _IdentityMatches(Data, Expected):
            return Result("Rejected", "retained report identity mismatch")
    try:
        Receipt = json.loads(ReceiptData, object_pairs_hook=_UniqueObject)
        if not isinstance(Receipt, dict) or Receipt.get("SchemaVersion") != ReceiptVersion:
            raise ValueError("unsupported receipt schema")
        Source = Receipt.get("Source")
        Report = Receipt.get("Report")
        if (not isinstance(Source, dict) or not isinstance(Report, dict)
                or Source.get("Name") != FailureName or Report.get("Name") != ReportName
                or not FailureName.endswith(".RoutingFailure.json")
                or any(Token in FailureName for Token in ("/", "\\"))
                or (Source != SourceIdentity if SourceIdentity is not None
                    else not _IdentityMatches(FailureData, Source))
                or not _IdentityMatches(ReportData, Report)):
            return Result("Rejected", "report source or content binding mismatch")
        Parser = _InertHtml()
        Parser.feed(ReportData.decode("utf-8")); Parser.close(); Parser.Check()
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return Result("Malformed", "invalid receipt or unsupported/inert HTML grammar")
    return {"Status": "Available", "Reason": None, "Source": Source,
            "Report": Report, "Receipt": FileIdentity(ReceiptName, ReceiptData)}


def ReadReportFile(Directory: Path, Name: str, Limit: int) -> bytes | None:
    """Bounded descriptor-relative read; reject links in every path component."""
    if Name not in ReportNames and (not Name.endswith(".RoutingFailure.json")
                                    or any(Token in Name for Token in ("/", "\\"))):
        raise ValueError("unrecognized report member")
    Absolute = Path(os.path.abspath(Directory))
    Descriptors = []
    try:
        Current = os.open(Absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        Descriptors.append(Current)
        for Part in Absolute.parts[1:]:
            Current = os.open(Part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=Current)
            Descriptors.append(Current)
        try:
            Leaf = os.open(Name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=Current)
        except FileNotFoundError:
            return None
        Descriptors.append(Leaf)
        Info = os.fstat(Leaf)
        if not stat.S_ISREG(Info.st_mode) or Info.st_size > Limit:
            raise ValueError("unsafe or oversized report member")
        Parts = []
        Remaining = Limit + 1
        while Remaining:
            Chunk = os.read(Leaf, min(Remaining, 65536))
            if not Chunk:
                break
            Parts.append(Chunk); Remaining -= len(Chunk)
        Data = b"".join(Parts)
        if len(Data) != Info.st_size or len(Data) > Limit:
            raise ValueError("report member changed while reading")
        return Data
    finally:
        for Descriptor in reversed(Descriptors):
            os.close(Descriptor)


def ObserveReportPair(FailurePath: Path, FailureData: bytes = b"", *, SourceIdentity: object = None, RecheckSource: bool = False) -> dict[str, object]:
    """Observe the fixed sibling pair; absence never changes routing outcome."""
    Records = {}
    try:
        Values = {}
        for Name, Limit in ((ReportName, MaximumReportBytes), (ReceiptName, MaximumReceiptBytes)):
            Data = ReadReportFile(FailurePath.parent, Name, Limit)
            Values[Name] = Data
            Records[Name] = {"Path": str(Path(os.path.abspath(FailurePath.parent / Name))),
                             "Exists": Data is not None}
            if Data is not None:
                Records[Name].update({Key: Value for Key, Value in FileIdentity(Name, Data).items() if Key != "Name"})
        if RecheckSource:
            CurrentSource = ReadReportFile(FailurePath.parent, FailurePath.name, 4 * 1024 * 1024)
            if CurrentSource is None or FileIdentity(FailurePath.name, CurrentSource) != SourceIdentity:
                raise ValueError("retained source changed after report publication")
            FailureData = CurrentSource
        Result = ValidateReportPair(FailureName=FailurePath.name, FailureData=FailureData,
                                    ReportData=Values[ReportName], ReceiptData=Values[ReceiptName],
                                    SourceIdentity=SourceIdentity)
    except (OSError, ValueError) as Error:
        Result = {"Status": "Rejected", "Reason": "unsafe report observation: " + type(Error).__name__}
    return {**Result, "Artifacts": Records}


def ProjectListedReport(FailureRecord: object, Artifacts: Mapping[str, object], ReadBytes) -> dict[str, object]:
    """Project report evidence from immutable observations and retained listings.

    ReadBytes receives only the selected failure path or its two fixed siblings;
    the archive/snapshot owner maps those names into its verified member table.
    """
    if not isinstance(FailureRecord, dict) or not isinstance(FailureRecord.get("Path"), str):
        return {"Status": "Unavailable", "Reason": "no authoritative failure artifact"}
    FailurePath = Path(FailureRecord["Path"])
    Expected = []
    Data = []
    try:
        Canonical = {ReportName: "RoutingFailureReport", ReceiptName: "RoutingFailureReportReceipt"}
        for Key, Record in Artifacts.items():
            if isinstance(Record, dict) and isinstance(Record.get("Path"), str):
                Name = Path(Record["Path"]).name
                if Name in ReportNames and (Key != Canonical[Name]
                        or Record["Path"] != str(FailurePath.parent / Name)):
                    return {"Status": "Rejected", "Reason": "duplicate or misplaced report inventory record"}
        for Name, Key in ((ReportName, "RoutingFailureReport"), (ReceiptName, "RoutingFailureReportReceipt")):
            Sibling = str(FailurePath.parent / Name)
            Record = Artifacts.get(Key)
            if Record is not None:
                if not isinstance(Record, dict) or Record.get("Path") != Sibling:
                    return {"Status": "Rejected", "Reason": "report is outside selected failure directory"}
                if sum(isinstance(Value, dict) and Value.get("Path") == Sibling
                       for Value in Artifacts.values()) != 1:
                    return {"Status": "Rejected", "Reason": "duplicate report inventory record"}
            Member = ReadBytes(Sibling)
            if Member is not None and (not isinstance(Record, dict) or Record.get("Exists") is not True):
                return {"Status": "Rejected", "Reason": "unlisted report or receipt"}
            Expected.append(Record if isinstance(Record, dict) and Record.get("Exists") is True else None)
            Data.append(Member)
        if Data == [None, None] and Expected == [None, None]:
            return {"Status": "Unavailable", "Reason": "report not retained"}
        FailureData = ReadBytes(str(FailurePath))
        if FailureData is None or not _IdentityMatches(FailureData, FailureRecord):
            return {"Status": "Rejected", "Reason": "source failure identity mismatch"}
        Result = ValidateReportPair(FailureName=FailurePath.name, FailureData=FailureData,
                                    ReportData=Data[0], ReceiptData=Data[1],
                                    ExpectedReport=Expected[0], ExpectedReceipt=Expected[1], RequireInventory=True)
        if Result["Status"] == "Available":
            Result["Path"] = str(FailurePath.parent / ReportName)
        return Result
    except (OSError, ValueError, TypeError):
        return {"Status": "Rejected", "Reason": "unsafe report member selection"}


def ArchiveRelativeMember(RecordedPath: str, RunName: str) -> str:
    """Map a retained original run path under an archive, without opening it."""
    Parts = RecordedPath.split("/")
    if (RunName in ("", ".", "..") or "/" in RunName or "\\" in RecordedPath
            or any(Part in (".", "..") for Part in Parts)
            or any(not Part for Part in Parts[1:])):
        raise ValueError("unsafe recorded artifact path")
    Indices = [Index for Index, Part in enumerate(Parts) if Part == RunName]
    if not Indices:
        raise ValueError("artifact does not belong to selected run")
    return "/".join(Parts[Indices[-1]:])


def UniqueReportMembers(Members, SelectedFailure: str | None) -> bool:
    """Every report member must belong to the one selected failure directory."""
    Actual = {Name for Name in Members if Path(Name).name in ReportNames}
    Expected = ({str(Path(SelectedFailure).parent / Name) for Name in ReportNames}
                if SelectedFailure is not None else set())
    return Actual.issubset(Expected)


def DiscoverReportMembers(Root: Path) -> set[str]:
    """Inspect bounded directory names without following links or reading files."""
    Absolute = Path(os.path.abspath(Root))
    Descriptors = []
    Members = set()
    Remaining = 50000
    try:
        Current = os.open(Absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        Descriptors.append(Current)
        for Part in Absolute.parts[1:]:
            Current = os.open(Part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=Current)
            Descriptors.append(Current)
        def Visit(Descriptor, Prefix, Depth):
            nonlocal Remaining
            if Depth > 32:
                raise ValueError("report membership depth limit")
            for Name in sorted(os.listdir(Descriptor)):
                Remaining -= 1
                if Remaining < 0:
                    raise ValueError("report membership entry limit")
                Info = os.stat(Name, dir_fd=Descriptor, follow_symlinks=False)
                Relative = Prefix + Name
                if stat.S_ISLNK(Info.st_mode):
                    raise ValueError("unsafe run-directory member")
                if Name in ReportNames:
                    Members.add(Relative)
                if stat.S_ISDIR(Info.st_mode):
                    Child = os.open(Name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=Descriptor)
                    try:
                        Visit(Child, Relative + "/", Depth + 1)
                    finally:
                        os.close(Child)
        Visit(Current, "", 0)
        return Members
    finally:
        for Descriptor in reversed(Descriptors):
            os.close(Descriptor)


def OpenEvidenceDirectory(Directory: Path) -> list[int]:
    """Retain non-following handles to every directory component."""
    Absolute = Path(os.path.abspath(Directory))
    Descriptors = []
    try:
        Current = os.open(Absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        Descriptors.append(Current)
        for Part in Absolute.parts[1:]:
            Current = os.open(Part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=Current)
            Descriptors.append(Current)
        return Descriptors
    except BaseException:
        for Descriptor in reversed(Descriptors):
            os.close(Descriptor)
        raise


def WriteEvidenceMember(DirectoryDescriptor: int, Name: str, Data: bytes) -> None:
    """Atomically publish under the retained directory, even after path swaps."""
    if Path(Name).name != Name or Name in ("", ".", ".."):
        raise ValueError("invalid evidence member name")
    Temporary = ".RoutingFailureReport-" + secrets.token_hex(12)
    Descriptor = os.open(Temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=DirectoryDescriptor)
    try:
        with os.fdopen(Descriptor, "wb") as Stream:
            Stream.write(Data)
        os.replace(Temporary, Name, src_dir_fd=DirectoryDescriptor, dst_dir_fd=DirectoryDescriptor)
    finally:
        try:
            os.unlink(Temporary, dir_fd=DirectoryDescriptor)
        except OSError:
            pass


def VerifyEvidenceDirectory(Directory: Path, Descriptor: int) -> None:
    """Require the recorded directory name still identifies the retained object."""
    Current = OpenEvidenceDirectory(Directory)
    try:
        Before, After = os.fstat(Descriptor), os.fstat(Current[-1])
        if (Before.st_dev, Before.st_ino) != (After.st_dev, After.st_ino):
            raise ValueError("evidence directory changed during publication")
    finally:
        for Value in reversed(Current):
            os.close(Value)
