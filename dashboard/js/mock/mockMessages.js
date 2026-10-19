import { CONFIG } from '../config.js';

function nowMinus(ms) {
  return new Date(Date.now() - ms).toISOString();
}


export function makeMockRoomRegistered(room = {}) {
  return {
    type: 'room_registered',
    payload: {
      room_id: room.roomId || room.room_id || CONFIG.ROOM_ID,
      room_name: room.roomName || room.room_name || CONFIG.ROOM_NAME,
      device_group_id: room.deviceGroupId || room.device_group_id || CONFIG.DEVICE_GROUP_ID,
      status: 'registered',
      registered_at: new Date().toISOString(),
      message: 'Mock 모드에서 방 정보 등록이 완료되었습니다.',
    },
  };
}

export function makeMockSystemStatus() {
  return {
    type: 'system_status',
    payload: {
      overall_status: 'normal',
      server_time: new Date().toISOString(),
      model_server: {
        status: 'running',
        last_inference_at: nowMinus(1100),
        stage1_artifact_id: 'stage1_threshold_v001',
        stage2_mode: 'static_moving_cnn',
        error_message: null,
      },
      csi_input: {
        status: 'receiving',
        device_id: CONFIG.DEVICE_GROUP_ID,
        last_received_at: nowMinus(1800),
        fps: null,
      },
      baseline: {
        status: 'ready',
        setup_state: 'running',
        room_id: CONFIG.ROOM_ID,
        baseline_id: 'baseline_room_101_v001',
        calibration_id: 'calib_room_101_v001',
        stage1_artifact_id: 'stage1_threshold_v001',
        stage2_mode: 'static_moving_cnn',
        started_at: nowMinus(3600_000),
        completed_at: nowMinus(3600_000 - 30_000),
        updated_at: nowMinus(3600_000 - 30_000),
        duration_sec: 300,
        message: 'baseline과 calibration 설정이 완료되었습니다.',
      },
    },
  };
}

export function makeMockInferenceResult() {
  const motionList = ['static_person', 'moving_person'];
  const motion = motionList[Math.floor(Math.random() * motionList.length)];

  return {
    type: 'inference_result',
    payload: {
      room_id: CONFIG.ROOM_ID,
      device_id: CONFIG.DEVICE_GROUP_ID,
      window_id: `win_${Date.now()}`,
      window: {
        start_at: nowMinus(5000),
        end_at: new Date().toISOString(),
        duration_sec: 5,
      },
      stage1: {
        label: 'occupied',
        score: 2.91,
        threshold: 2.506834,
        artifact_id: 'stage1_threshold_v001',
        confidence: null,
      },
      stage2: {
        label: motion,
        confidence: motion === 'static_person' ? 0.87 : 0.82,
        status: 'ok',
        artifact_id: 'stage2_static_moving_v001',
      },
      final: {
        label: motion === 'static_person' ? 'occupied_static_person' : 'occupied_moving_person',
        confidence: motion === 'static_person' ? 0.87 : 0.82,
      },
      final_status: motion === 'static_person' ? 'occupied_static_person' : 'occupied_moving_person',
      decided_at: new Date().toISOString(),
    },
  };
}

export function makeMockRiskAssessment() {
  return {
    type: 'risk_assessment',
    payload: {
      room_id: CONFIG.ROOM_ID,
      device_id: CONFIG.DEVICE_GROUP_ID,
      event_timestamp: new Date().toISOString(),
      residual_presence: 'high',
      risk_level: 'danger',
      occupancy_summary: {
        empty_ratio: 0.0,
        occupied_ratio: 1.0,
        recent_occupied: true,
      },
      motion_summary: {
        static_ratio: 1.0,
        moving_ratio: 0.0,
        static_persistence_seconds: 30.0,
      },
      confidence: 0.9,
      reason: 'static_presence_persisted',
      limitations: [],
    },
  };
}

export function makeMockAlert() {
  return {
    type: 'alert_event',
    payload: {
      id: `evt_${Date.now()}`,
      level: 'DANGER',
      title: '[위험 알람]',
      message: '실내 잔류 위험이 높습니다. / 사유: static_presence_persisted',
      device_id: CONFIG.DEVICE_GROUP_ID,
      room_id: CONFIG.ROOM_ID,
      stage1_label: 'occupied',
      stage2_label: 'static_person',
      risk_level: 'danger',
      final_label: 'high',
      occurred_at: new Date().toISOString(),
    },
  };
}

export function makeMockEventLogs() {
  return {
    type: 'event_log_list',
    payload: {
      items: [
        {
          id: 'evt_001',
          occurred_at: nowMinus(10_000),
          level: 'DANGER',
          device_id: CONFIG.DEVICE_GROUP_ID,
          stage1_label: 'occupied',
          stage2_label: 'static_person',
          final_label: 'high',
          message: '실내 잔류 위험 높음',
        },
        {
          id: 'evt_002',
          occurred_at: nowMinus(30_000),
          level: 'WARNING',
          device_id: CONFIG.DEVICE_GROUP_ID,
          stage1_label: 'occupied',
          stage2_label: 'static_person',
          final_label: 'possible',
          message: '실내 잔류 가능성 있음',
        },
        {
          id: 'evt_003',
          occurred_at: nowMinus(240_000),
          level: 'ERROR',
          device_id: CONFIG.DEVICE_GROUP_ID,
          stage1_label: 'unknown',
          stage2_label: 'unknown',
          final_label: 'system_error',
          message: 'CSI 입력 끊김',
        },
      ],
    },
  };
}

export function makeMockBaselineCollecting() {
  return {
    type: 'baseline_status',
    payload: {
      status: 'collecting',
      setup_state: 'baseline_collecting',
      room_id: CONFIG.ROOM_ID,
      baseline_id: null,
      calibration_id: null,
      started_at: new Date().toISOString(),
      completed_at: null,
      duration_sec: CONFIG.BASELINE_DURATION_SEC,
      message: 'baseline 수집 상태로 전환했습니다. 실제 empty CSI 수집과 baseline/finish는 하드웨어·전처리기가 수행합니다.',
    },
  };
}

export function makeMockBaselineReady(startedAt) {
  return {
    type: 'baseline_status',
    payload: {
      status: 'ready',
      setup_state: 'baseline_ready',
      room_id: CONFIG.ROOM_ID,
      baseline_id: 'baseline_room_101_v001',
      calibration_id: null,
      stage1_artifact_id: 'stage1_threshold_v001',
      stage2_mode: 'static_moving_cnn',
      started_at: startedAt,
      completed_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      duration_sec: CONFIG.BASELINE_DURATION_SEC,
      message: 'baseline profile이 등록되었습니다. calibration 시작이 필요합니다.',
    },
  };
}

export function makeMockCalibrationCollecting(baselineId = 'baseline_room_101_v001') {
  return {
    type: 'baseline_status',
    payload: {
      status: 'calibration_collecting',
      setup_state: 'calibration_collecting',
      room_id: CONFIG.ROOM_ID,
      baseline_id: baselineId,
      calibration_id: null,
      stage1_artifact_id: 'stage1_threshold_v001',
      stage2_mode: 'static_moving_cnn',
      started_at: new Date().toISOString(),
      completed_at: null,
      updated_at: new Date().toISOString(),
      duration_sec: CONFIG.CALIBRATION_EXPECTED_WINDOWS * CONFIG.CALIBRATION_WINDOW_DURATION_SEC,
      message: 'calibration 수집 상태로 전환했습니다. empty calibration window 전송과 finish는 하드웨어·전처리기가 수행합니다.',
    },
  };
}

export function makeMockCalibrationReady(baselineId = 'baseline_room_101_v001') {
  return {
    type: 'baseline_status',
    payload: {
      status: 'ready',
      setup_state: 'ready',
      room_id: CONFIG.ROOM_ID,
      baseline_id: baselineId,
      calibration_id: `calib_room_101_v${String(Date.now()).slice(-3)}`,
      stage1_artifact_id: 'stage1_threshold_v001',
      stage2_mode: 'static_moving_cnn',
      started_at: null,
      completed_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      duration_sec: CONFIG.CALIBRATION_EXPECTED_WINDOWS * CONFIG.CALIBRATION_WINDOW_DURATION_SEC,
      message: 'calibration 설정 완료. 같은 baseline 기준으로 재보정이 가능합니다.',
    },
  };
}
