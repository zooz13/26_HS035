"""Parser and validator for ESP32 `CSI_DATA` UART records."""

from __future__ import annotations

import ast
import csv
import logging
import time
from dataclasses import dataclass
from enum import Enum
from math import isfinite

from app.models import SCHEMA_VERSION, CanonicalCsiFrame, ParseErrorRecord


LOGGER = logging.getLogger(__name__)
EXPECTED_TAB_FIELD_COUNT = 25
EXPECTED_CSV_FIELD_COUNT = 28
CSI_DATA = "CSI_DATA"


class ParseStatus(str, Enum):
    PARSED = "parsed"
    IGNORED = "ignored"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ParseResult:
    status: ParseStatus
    frame: CanonicalCsiFrame | None = None
    error_record: ParseErrorRecord | None = None
    error: str | None = None


class CsiParser:
    """Stateful parser that tracks rejected and ignored UART records."""

    def __init__(self, gateway_id: str, device_id: str, link_id: str) -> None:
        self.gateway_id = gateway_id
        self.device_id = device_id
        self.link_id = link_id
        self.parsed_frame_count = 0
        self.parse_error_count = 0
        self.ignored_line_count = 0

    def parse_line(self, line: str, gateway_ts: float | None = None) -> ParseResult:
        """Parse one line without allowing malformed input to escape."""

        timestamp = time.time() if gateway_ts is None else gateway_ts
        if not isinstance(timestamp, (int, float)) or not isfinite(timestamp):
            raise ValueError("gateway_ts must be a finite Unix epoch number")
        timestamp = float(timestamp)

        if not isinstance(line, str):
            return self._reject(
                "line must be a string",
                repr(line),
                timestamp,
            )

        normalized = line.rstrip("\r\n")
        if not (
            normalized.startswith(f"{CSI_DATA}\t")
            or normalized.startswith(f"{CSI_DATA},")
        ):
            self.ignored_line_count += 1
            return ParseResult(status=ParseStatus.IGNORED)

        try:
            if normalized.startswith(f"{CSI_DATA},"):
                frame = self._parse_csv_csi_data(normalized, timestamp)
            else:
                frame = self._parse_tab_csi_data(normalized, timestamp)
        except (csv.Error, ValueError, SyntaxError, TypeError) as exc:
            return self._reject(str(exc), normalized, timestamp)

        self.parsed_frame_count += 1
        return ParseResult(status=ParseStatus.PARSED, frame=frame)

    def _parse_tab_csi_data(
        self, line: str, gateway_ts: float
    ) -> CanonicalCsiFrame:
        fields = line.split("\t", 24)
        if len(fields) != EXPECTED_TAB_FIELD_COUNT:
            raise ValueError(
                f"expected {EXPECTED_TAB_FIELD_COUNT} tab-separated fields, "
                f"got {len(fields)}"
            )
        numeric_fields = self._parse_numeric_fields(
            fields,
            range(1, 24),
            mac_index=2,
        )
        csi_value = self._parse_csi_array(fields[24])
        return self._build_frame(
            fields=fields,
            numeric_fields=numeric_fields,
            csi_value=csi_value,
            gateway_ts=gateway_ts,
            raw_line=line,
        )

    def _parse_csv_csi_data(
        self, line: str, gateway_ts: float
    ) -> CanonicalCsiFrame:
        fields = next(csv.reader([line], strict=True))
        if len(fields) != EXPECTED_CSV_FIELD_COUNT:
            raise ValueError(
                f"expected {EXPECTED_CSV_FIELD_COUNT} CSV fields, "
                f"got {len(fields)}"
            )
        numeric_fields = self._parse_numeric_fields(
            fields,
            range(1, 27),
            mac_index=2,
        )
        csi_value = self._parse_csi_array(fields[27])
        data_len = numeric_fields[25]
        self._validate_csi_data(csi_value, data_len)
        return CanonicalCsiFrame(
            schema_version=SCHEMA_VERSION,
            type="csi_frame",
            gateway_id=self.gateway_id,
            device_id=self.device_id,
            link_id=self.link_id,
            gateway_ts=gateway_ts,
            id=numeric_fields[1],
            mac=fields[2],
            rssi=numeric_fields[3],
            rate=numeric_fields[4],
            sig_mode=numeric_fields[5],
            mcs=numeric_fields[6],
            bandwidth=numeric_fields[7],
            smoothing=numeric_fields[8],
            not_sounding=numeric_fields[9],
            aggregation=numeric_fields[10],
            stbc=numeric_fields[11],
            fec_coding=numeric_fields[12],
            sgi=numeric_fields[13],
            noise_floor=numeric_fields[14],
            fft_gain=numeric_fields[15],
            agc_gain=numeric_fields[16],
            compensate_gain_x1000000=numeric_fields[17],
            ampdu_cnt=numeric_fields[18],
            channel=numeric_fields[19],
            secondary_channel=numeric_fields[20],
            local_timestamp=numeric_fields[21],
            ant=numeric_fields[22],
            sig_len=numeric_fields[23],
            rx_format=numeric_fields[24],
            len=data_len,
            first_word_invalid=numeric_fields[26],
            csi_format="int8_interleaved_iq",
            data=tuple(csi_value),
            raw_line=line,
        )

    @staticmethod
    def _parse_csi_array(value: str) -> list[int] | tuple[int, ...]:
        try:
            csi_value = ast.literal_eval(value)
        except (ValueError, SyntaxError) as exc:
            raise ValueError("CSI array is not a valid Python literal") from exc

        if not isinstance(csi_value, (list, tuple)):
            raise ValueError("CSI field must be a list or tuple")
        if any(type(value) is not int for value in csi_value):
            raise ValueError("every CSI value must be an integer")
        if any(value < -128 or value > 127 for value in csi_value):
            raise ValueError("CSI values must be in int8 range -128..127")
        return csi_value

    @staticmethod
    def _parse_numeric_fields(
        fields: list[str],
        indices: range,
        *,
        mac_index: int,
    ) -> dict[int, int]:
        numeric_fields: dict[int, int] = {}
        for index in indices:
            if index == mac_index:
                continue
            try:
                numeric_fields[index] = int(fields[index])
            except ValueError as exc:
                raise ValueError(
                    f"field {index} must be an integer; got {fields[index]!r}"
                ) from exc
        return numeric_fields

    @staticmethod
    def _validate_csi_data(
        csi_value: list[int] | tuple[int, ...],
        data_len: int,
    ) -> None:
        if data_len != len(csi_value):
            raise ValueError(
                f"CSI length mismatch: declared {data_len}, "
                f"actual {len(csi_value)}"
            )

    def _build_frame(
        self,
        *,
        fields: list[str],
        numeric_fields: dict[int, int],
        csi_value: list[int] | tuple[int, ...],
        gateway_ts: float,
        raw_line: str,
    ) -> CanonicalCsiFrame:
        data_len = numeric_fields[22]
        self._validate_csi_data(csi_value, data_len)
        return CanonicalCsiFrame(
            schema_version=SCHEMA_VERSION,
            type="csi_frame",
            gateway_id=self.gateway_id,
            device_id=self.device_id,
            link_id=self.link_id,
            gateway_ts=gateway_ts,
            id=numeric_fields[1],
            mac=fields[2],
            rssi=numeric_fields[3],
            rate=numeric_fields[4],
            sig_mode=numeric_fields[5],
            mcs=numeric_fields[6],
            bandwidth=numeric_fields[7],
            smoothing=numeric_fields[8],
            not_sounding=numeric_fields[9],
            aggregation=numeric_fields[10],
            stbc=numeric_fields[11],
            fec_coding=numeric_fields[12],
            sgi=numeric_fields[13],
            noise_floor=numeric_fields[14],
            ampdu_cnt=numeric_fields[15],
            channel=numeric_fields[16],
            secondary_channel=numeric_fields[17],
            local_timestamp=numeric_fields[18],
            ant=numeric_fields[19],
            sig_len=numeric_fields[20],
            rx_format=numeric_fields[21],
            len=data_len,
            first_word_invalid=numeric_fields[23],
            csi_format="int8_interleaved_iq",
            data=tuple(csi_value),
            raw_line=raw_line,
        )

    def _reject(
        self,
        error: str,
        line: str,
        gateway_ts: float,
    ) -> ParseResult:
        self.parse_error_count += 1
        LOGGER.warning(
            "Rejected CSI_DATA line: %s; line_preview=%r",
            error,
            line[:200],
        )
        return ParseResult(
            status=ParseStatus.REJECTED,
            error_record=ParseErrorRecord(
                schema_version=SCHEMA_VERSION,
                type="parse_error",
                gateway_id=self.gateway_id,
                device_id=self.device_id,
                link_id=self.link_id,
                gateway_ts=gateway_ts,
                raw_line=line,
                error=error,
            ),
            error=error,
        )
