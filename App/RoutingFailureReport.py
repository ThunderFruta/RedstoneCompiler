"""Descriptor-safe publication of source-bound Markdown failure reports."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from time import perf_counter
from typing import Mapping

from App.RoutingFailureArtifacts import (BuildReportReceipt, ReceiptName, ReportName,
    ReportNames, ValidateReportPair, FileIdentity, ObserveReportPair,
    OpenEvidenceDirectory, WriteEvidenceMember, VerifyEvidenceDirectory)
from App.RoutingFailureMarkdown import RenderRoutingFailureMarkdown

MaximumArtifactBytes = 4 * 1024 * 1024
MaximumReportBytes = 256 * 1024


class ArtifactReadLimit(ValueError):
    """The source cannot be authenticated within the report reader's byte cap."""


def PublishRoutingFailureReport(*, RunDirectory: Path, FailurePath: Path,
                                SourceIdentity: Mapping[str, object]) -> dict[str, object]:
    """Best effort publication; conventional failures never replace routing failure.

    Source bytes must match the run inventory. Unsupported/malformed artifacts
    get a metadata report. Oversize input fails closed without extra unbounded
    hashing. Cancellation exceptions propagate.
    """
    Started = perf_counter()
    DirectoryDescriptors: list[int] = []
    try:
        Directory = RunDirectory.resolve()
        Source = FailurePath.parent.resolve() / FailurePath.name
        if Source.parent != Directory or Source.is_symlink():
            raise ValueError("failure artifact must be a regular run-local file")
        if str(Source) != SourceIdentity.get("Path"):
            raise ValueError("failure artifact inventory identity missing")
        DirectoryDescriptors = OpenEvidenceDirectory(Directory)
        DirectoryDescriptor = DirectoryDescriptors[-1]
        Flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        Descriptor = os.open(Source.name, Flags, dir_fd=DirectoryDescriptor)
        with os.fdopen(Descriptor, "rb") as Stream:
            Info = os.fstat(Stream.fileno())
            if not stat.S_ISREG(Info.st_mode) or Info.st_size != SourceIdentity.get("Bytes"):
                raise ValueError("failure artifact identity changed")
            if Info.st_size > MaximumArtifactBytes:
                raise ArtifactReadLimit("failure artifact exceeds bounded reader size")
            Raw = Stream.read(MaximumArtifactBytes + 1)
        if len(Raw) > MaximumArtifactBytes:
            raise ArtifactReadLimit("failure artifact grew beyond reader size")
        if sha256(Raw).hexdigest() != SourceIdentity.get("Sha256"):
            raise ValueError("failure artifact hash changed")
        if any(os.path.lexists(Directory / Name) for Name in ReportNames):
            Existing = ObserveReportPair(Source, Raw)
            if Existing["Status"] != "Available":
                return {"Status": Existing["Status"], "ErrorType": Existing["Reason"],
                        "WallSeconds": perf_counter() - Started, "Bytes": 0}
            VerifyEvidenceDirectory(Directory, DirectoryDescriptor)
            return {"Status": "Published", "Path": str(Directory / Existing["Report"]["Name"]),
                    "Bytes": Existing["Report"]["SizeBytes"], "Sha256": Existing["Report"]["Sha256"],
                    "Source": Existing["Source"], "Receipt": Existing["Receipt"],
                    "WallSeconds": perf_counter() - Started}
        Payload = None
        State = "Unsupported — artifact exceeds bounded reader size"
        if len(Raw) <= MaximumArtifactBytes:
            try:
                Payload = json.loads(Raw)
                State = "Available"
                if not isinstance(Payload, dict) or not isinstance(Payload.get("Failure"), dict):
                    State = "Malformed"
                elif Payload.get("SchemaVersion") != "routing-failure-v1":
                    State = "Unsupported"
                elif any(not isinstance(Payload["Failure"].get(Key), str)
                         or not Payload["Failure"][Key] for Key in ("Stage", "Reason")):
                    State = "Malformed"
            except (ValueError, UnicodeError, RecursionError):
                State = "Malformed"
        Content = RenderRoutingFailureMarkdown(Source, SourceIdentity, Payload, State).encode("utf-8")
        if len(Content) > MaximumReportBytes:
            raise ValueError("report byte limit exceeded")
        Receipt = BuildReportReceipt(Source.name, Raw, Content)
        Validation = ValidateReportPair(FailureName=Source.name, FailureData=Raw,
                                        ReportData=Content, ReceiptData=Receipt)
        if Validation["Status"] != "Available":
            raise ValueError("generated report failed its report artifact contract")
        # Publish the receipt last: it commits the content/source binding.
        for Name, Data in ((ReportName, Content), (ReceiptName, Receipt)):
            WriteEvidenceMember(DirectoryDescriptor, Name, Data)
        VerifyEvidenceDirectory(Directory, DirectoryDescriptor)
        return {"Status": "Published", "Path": str(Directory / ReportName),
                "Bytes": len(Content), "Sha256": FileIdentity(ReportName, Content)["Sha256"],
                "Source": Validation["Source"], "Receipt": Validation["Receipt"],
                "WallSeconds": perf_counter() - Started}
    except Exception as Error:
        return {"Status": "Unavailable", "ErrorType": type(Error).__name__,
                "WallSeconds": perf_counter() - Started, "Bytes": 0}
    finally:
        for Descriptor in reversed(DirectoryDescriptors):
            os.close(Descriptor)
