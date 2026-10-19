import pytest

from app.csi_parser import CsiParser, ParseStatus


VALID_FIELDS = [
    "CSI_DATA",
    "126943",
    "1a:00:00:00:00:00",
    "-46",
    "11",
    "1",
    "0",
    "1",
    "1",
    "1",
    "0",
    "0",
    "0",
    "0",
    "-90",
    "0",
    "11",
    "2",
    "753582003",
    "0",
    "47",
    "1",
    "6",
    "0",
    "[0,0,0,0,-17,-10]",
]

VALID_CSV_FIELDS = [
    "CSI_DATA",
    "126943",
    "1a:00:00:00:00:00",
    "-46",
    "11",
    "1",
    "0",
    "1",
    "1",
    "1",
    "0",
    "0",
    "0",
    "0",
    "-90",
    "-2",
    "40",
    "1000000",
    "0",
    "11",
    "2",
    "753582003",
    "0",
    "47",
    "1",
    "6",
    "0",
    "[0,0,0,0,-17,-10]",
]


@pytest.fixture
def parser() -> CsiParser:
    return CsiParser(
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
    )


def make_line(**field_updates: str) -> str:
    fields = VALID_FIELDS.copy()
    for index, value in field_updates.items():
        fields[int(index)] = value
    return "\t".join(fields)


def make_csv_line(**field_updates: str) -> str:
    fields = VALID_CSV_FIELDS.copy()
    for index, value in field_updates.items():
        fields[int(index)] = value
    fields[27] = f'"{fields[27]}"'
    return ",".join(fields)


def test_parses_valid_csi_data_into_canonical_frame(parser: CsiParser) -> None:
    gateway_ts = 1710000000.123
    raw_line = make_line()

    result = parser.parse_line(raw_line, gateway_ts=gateway_ts)

    assert result.status is ParseStatus.PARSED
    assert result.error is None
    assert result.error_record is None
    assert result.frame is not None
    assert result.frame.type == "csi_frame"
    assert result.frame.gateway_ts == gateway_ts
    assert result.frame.id == 126943
    assert result.frame.mac == "1a:00:00:00:00:00"
    assert result.frame.rssi == -46
    assert result.frame.noise_floor == -90
    assert result.frame.bandwidth == 1
    assert result.frame.local_timestamp == 753582003
    assert result.frame.rx_format == 1
    assert result.frame.len == 6
    assert result.frame.csi_format == "int8_interleaved_iq"
    assert result.frame.data == (0, 0, 0, 0, -17, -10)
    assert result.frame.raw_line == raw_line
    assert parser.parsed_frame_count == 1
    assert parser.parse_error_count == 0


def test_parses_esp32s3_csv_and_preserves_gain_fields(
    parser: CsiParser,
) -> None:
    gateway_ts = 1710000000.123
    raw_line = make_csv_line()

    result = parser.parse_line(raw_line, gateway_ts=gateway_ts)

    assert result.status is ParseStatus.PARSED
    assert result.frame is not None
    assert result.frame.id == 126943
    assert result.frame.fft_gain == -2
    assert result.frame.agc_gain == 40
    assert result.frame.compensate_gain_x1000000 == 1000000
    assert result.frame.ampdu_cnt == 0
    assert result.frame.channel == 11
    assert result.frame.local_timestamp == 753582003
    assert result.frame.len == 6
    assert result.frame.data == (0, 0, 0, 0, -17, -10)
    assert result.frame.raw_line == raw_line


def test_csv_gain_fields_are_serialized(parser: CsiParser) -> None:
    result = parser.parse_line(make_csv_line())

    assert result.frame is not None
    record = result.frame.to_dict()
    assert record["fft_gain"] == -2
    assert record["agc_gain"] == 40
    assert record["compensate_gain_x1000000"] == 1000000


def test_tab_record_omits_unavailable_gain_fields(parser: CsiParser) -> None:
    result = parser.parse_line(make_line())

    assert result.frame is not None
    record = result.frame.to_dict()
    assert "fft_gain" not in record
    assert "agc_gain" not in record
    assert "compensate_gain_x1000000" not in record


def test_inference_dict_excludes_raw_line(parser: CsiParser) -> None:
    result = parser.parse_line(make_line(), gateway_ts=1710000000.123)

    assert result.frame is not None
    record = result.frame.to_dict()
    assert record["type"] == "csi_frame"
    assert record["data"] == [0, 0, 0, 0, -17, -10]
    assert "raw_line" not in record


def test_rawlog_dict_includes_raw_line(parser: CsiParser) -> None:
    raw_line = make_line()
    result = parser.parse_line(raw_line, gateway_ts=1710000000.123)

    assert result.frame is not None
    record = result.frame.to_dict(include_raw_line=True)
    assert record["raw_line"] == raw_line


def test_ignores_non_csi_data_line(parser: CsiParser) -> None:
    result = parser.parse_line(
        "INFO\tboot complete",
        gateway_ts=1710000000.123,
    )

    assert result.status is ParseStatus.IGNORED
    assert result.frame is None
    assert result.error_record is None
    assert parser.ignored_line_count == 1
    assert parser.parse_error_count == 0


def test_rejects_wrong_field_count_without_raising(parser: CsiParser) -> None:
    raw_line = "CSI_DATA\t1\t2"
    result = parser.parse_line(raw_line, gateway_ts=1710000000.123)

    assert result.status is ParseStatus.REJECTED
    assert "expected 25" in (result.error or "")
    assert result.error_record is not None
    assert result.error_record.type == "parse_error"
    assert result.error_record.gateway_ts == 1710000000.123
    assert result.error_record.raw_line == raw_line
    assert result.error_record.error == result.error
    assert result.error_record.raw_bytes_hex is None
    assert result.error_record.exceeded_max_line_bytes is None
    assert parser.parse_error_count == 1


def test_rejects_malformed_csi_literal_without_raising(parser: CsiParser) -> None:
    result = parser.parse_line(make_line(**{"24": "[0, broken]"}))

    assert result.status is ParseStatus.REJECTED
    assert "valid Python literal" in (result.error or "")


def test_rejects_data_length_mismatch(parser: CsiParser) -> None:
    result = parser.parse_line(make_line(**{"22": "384"}))

    assert result.status is ParseStatus.REJECTED
    assert "CSI length mismatch" in (result.error or "")


def test_rejects_csv_data_length_mismatch(parser: CsiParser) -> None:
    result = parser.parse_line(make_csv_line(**{"25": "384"}))

    assert result.status is ParseStatus.REJECTED
    assert "CSI length mismatch" in (result.error or "")


def test_rejects_csv_with_wrong_field_count(parser: CsiParser) -> None:
    result = parser.parse_line("CSI_DATA,1,2")

    assert result.status is ParseStatus.REJECTED
    assert "expected 28 CSV fields" in (result.error or "")


@pytest.mark.parametrize("invalid_value", ["-129", "128"])
def test_rejects_csi_values_outside_int8(
    parser: CsiParser, invalid_value: str
) -> None:
    result = parser.parse_line(
        make_line(**{"22": "1", "24": f"[{invalid_value}]"})
    )

    assert result.status is ParseStatus.REJECTED
    assert "int8 range" in (result.error or "")


def test_rejects_non_integer_csi_values(parser: CsiParser) -> None:
    result = parser.parse_line(make_line(**{"22": "1", "24": "[1.5]"}))

    assert result.status is ParseStatus.REJECTED
    assert "must be an integer" in (result.error or "")


def test_rejects_invalid_numeric_header_field(parser: CsiParser) -> None:
    result = parser.parse_line(make_line(**{"3": "not-an-rssi"}))

    assert result.status is ParseStatus.REJECTED
    assert "field 3 must be an integer" in (result.error or "")


def test_split_maxsplit_preserves_csi_field_validation(parser: CsiParser) -> None:
    result = parser.parse_line(make_line(**{"24": "[1]\textra"}))

    assert result.status is ParseStatus.REJECTED
    assert "valid Python literal" in (result.error or "")


def test_removes_only_uart_line_ending_from_raw_line(parser: CsiParser) -> None:
    raw_line = make_line()

    result = parser.parse_line(
        f"{raw_line}\r\n",
        gateway_ts=1710000000.123,
    )

    assert result.frame is not None
    assert result.frame.raw_line == raw_line


@pytest.mark.parametrize("gateway_ts", [float("nan"), float("inf"), "invalid"])
def test_rejects_invalid_gateway_timestamp(
    parser: CsiParser, gateway_ts: object
) -> None:
    with pytest.raises(ValueError, match="finite Unix epoch"):
        parser.parse_line(make_line(), gateway_ts=gateway_ts)  # type: ignore[arg-type]
