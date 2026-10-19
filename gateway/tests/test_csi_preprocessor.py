import numpy as np

from app.baseline_state import BaselineState
from app.csi_preprocessor import (
    build_motion_input,
    iq_to_amplitude,
    normalize_array,
    parse_raw_csi_packet,
    stage1_feature_vector,
)


def make_baseline() -> BaselineState:
    ones = np.ones(192, dtype=np.float32)
    zeros = np.zeros(192, dtype=np.float32)
    return BaselineState(
        baseline_id="baseline_room_101_v001",
        segment="full_buffer",
        preprocessing_type="packet_mean_normalized",
        window_size=128,
        stride=64,
        median=ones,
        robust_scale=ones,
        diff_abs_p95_scale=1.0,
        temporal_std_p95_scale=1.0,
        baseline_amp_mean=ones,
        baseline_amp_median=ones,
        baseline_amp_std=zeros,
        baseline_shape=zeros,
        num_windows=1,
        duration_sec=300.0,
        window_duration_sec=5.0,
    )


def test_iq_order_is_imag_then_real() -> None:
    amplitude = iq_to_amplitude(
        np.asarray([3, 4, 5, 12], dtype=np.float32)
    )

    np.testing.assert_allclose(amplitude, [5.0, 13.0])


def test_raw_packet_384_values_becomes_192_amplitudes() -> None:
    raw = [3, 4] * 192

    amplitude = parse_raw_csi_packet({"data": raw})

    assert amplitude.shape == (192,)
    np.testing.assert_allclose(amplitude, np.full(192, 5.0))


def test_packet_mean_normalization_and_nonfinite_cleanup() -> None:
    matrix = np.asarray(
        [[1.0, 3.0], [np.nan, np.inf]],
        dtype=np.float32,
    )

    normalized = normalize_array(matrix, "packet_mean_normalized")

    np.testing.assert_allclose(normalized[0], [0.5, 1.5])
    assert np.isfinite(normalized).all()
    np.testing.assert_allclose(normalized[1], [0.0, 0.0])


def test_stage1_features_and_motion_input_follow_reference_contract() -> None:
    amplitude = np.tile(
        np.linspace(1.0, 2.0, 192, dtype=np.float32),
        (130, 1),
    )

    features = stage1_feature_vector(amplitude, make_baseline())
    motion_input = build_motion_input(amplitude)

    for name in (
        "static_score",
        "motion_score",
        "shape_score",
        "robust_max_shape_score",
    ):
        assert np.isfinite(features[name])
    assert motion_input["shape"] == [128, 192]
    assert len(motion_input["values"]) == 128
    assert len(motion_input["values"][0]) == 192

