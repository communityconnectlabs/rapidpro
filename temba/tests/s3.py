import gzip
import io
import iso8601
import regex

from collections import defaultdict
from datetime import datetime
from typing import Dict, List
from unittest.mock import call

from temba.archives.models import FileAndHash
from temba.utils import chunk_list, json


class MockEventStream:
    def __init__(self, records: List[Dict], max_payload_size: int = 256):
        # serialize records as a JSONL payload
        buffer = io.BytesIO()
        for record in records:
            buffer.write(json.dumps(record).encode("utf-8"))
            buffer.write(b"\n")

        payload = buffer.getvalue()
        payload_chunks = chunk_list(payload, size=max_payload_size)

        self.events = [{"Records": {"Payload": chunk}} for chunk in payload_chunks]
        self.events.append(
            {"Stats": {"Details": {"BytesScanned": 123, "BytesProcessed": 234, "BytesReturned": len(payload)}}}
        )
        self.events.append({"End": {}})

    def __iter__(self):
        for event in self.events:
            yield event


class MockS3Client:
    """
    A mock of the boto S3 client
    """

    def __init__(self):
        self.objects = {}
        self.calls = defaultdict(list)

    def put_jsonl(self, bucket: str, key: str, records: List[Dict]):
        stream = io.BytesIO()
        gz = gzip.GzipFile(fileobj=stream, mode="wb")

        for record in records:
            gz.write(json.dumps(record).encode("utf-8"))
            gz.write(b"\n")
        gz.close()

        self.objects[(bucket, key)] = stream

    def put_object(self, Bucket: str, Key: str, Body, **kwargs):
        self.calls["put_object"].append(call(Bucket=Bucket, Key=Key, Body=Body, **kwargs))

        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key, **kwargs):
        stream = self.objects[(Bucket, Key)]
        stream.seek(0)
        return {"Bucket": Bucket, "Key": Key, "Body": stream}

    def delete_object(self, Bucket, Key, **kwargs):
        del self.objects[(Bucket, Key)]
        return {"DeleteMarker": False, "VersionId": "versionId", "RequestCharged": "requester"}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        matches = []
        for o in self.objects.keys():
            if o[1].startswith(Prefix):
                matches.append({"Key": o[1]})

        return dict(Contents=matches)

    def select_object_content(self, Bucket, Key, **kwargs):
        stream = self.objects[(Bucket, Key)]
        stream.seek(0)
        zstream = gzip.GzipFile(fileobj=stream)

        records = []
        while True:
            line = zstream.readline()
            if not line:
                break

            # unlike real S3 we don't actually filter any records by expression
            records.append(json.loads(line.decode("utf-8")))

        return {"Payload": MockEventStream(records)}


def jsonlgz_encode(records: list) -> tuple:
    stream = io.BytesIO()
    wrapper = FileAndHash(stream)
    gz = gzip.GzipFile(fileobj=wrapper, mode="wb")

    for record in records:
        gz.write(json.dumps(record).encode("utf-8"))
        gz.write(b"\n")
    gz.close()

    return stream, wrapper.hash.hexdigest(), wrapper.size


def select_matches(expression: str, record: dict) -> bool:
    """
    Our greatly simplified version of S3 select matching
    """
    conditions = _parse_expression(expression)
    for field, op, val in conditions:
        if not _condition_matches(field, op, val, record):
            return False
    return True


def _condition_matches(field, op, val, record: dict) -> bool:
    # find the value in the record
    actual = record
    for key in field.split("."):
        actual = actual[key]

    if isinstance(val, datetime):
        actual = iso8601.parse_date(actual)

    if op == "=":
        return actual == val
    elif op == ">=":
        return actual >= val
    elif op == ">":
        return actual > val
    elif op == "<=":
        return actual <= val
    elif op == "<":
        return actual < val
    elif op == "IN":
        return actual in val


def _parse_expression(exp: str) -> list:
    """
    Expressions we generate for S3 Select are very limited and don't require intelligent parsing
    """
    conditions = exp[33:].split(" AND ")
    parsed = []
    for con in conditions:
        match = regex.match(r"(.*)\s(=|!=|>|>=|<|<=|IN)\s(.+)", con)
        col, op, val = match.group(1), match.group(2), match.group(3)

        if col.startswith("CAST("):
            col = regex.match(r"CAST\((.+) AS .+\)", col).group(1)

        col = col[2:]  # remove alias prefix
        parsed.append((col, op, _parse_value(val)))

    return parsed


def _parse_value(val: str):
    if val.startswith("CAST('") and val.endswith("' AS TIMESTAMP)"):
        return iso8601.parse_date(val[6:31])
    elif val.startswith("("):
        return [_parse_value(v) for v in val[1:-1].split(", ")]
    elif val.startswith("'"):
        return val[1:-1]
    elif val[0].isdigit():
        return int(val)
    elif val == "TRUE":
        return True
    elif val == "FALSE":
        return False