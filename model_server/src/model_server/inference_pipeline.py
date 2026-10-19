from __future__ import annotations

from common.types import InferenceResult, ProcessedWindow
from model_adapter.stage1_adapter import Stage1Adapter, Stage1AdapterError
from model_server.dummy_stage1 import predict_stage1
from model_server.dummy_stage2 import predict_stage2 as predict_dummy_stage2
from model_server.placeholder_stage2 import predict_stage2_unknown
from model_server.result_aggregator import DEFAULT_MODEL_VERSION, build_inference_result


class InferencePipeline:
    def __init__(
        self,
        model_version: str = DEFAULT_MODEL_VERSION,
        stage1_mode: str = "dummy",
        stage1_artifact_path: str | None = None,
        stage2_mode: str = "dummy",
    ):
        self.model_version = model_version
        self.stage1_mode = stage1_mode
        self.stage2_mode = stage2_mode
        adapter_modes = {"checkpoint", "legacy_logreg", "threshold"}
        self.stage1_adapter = Stage1Adapter(stage1_artifact_path) if stage1_mode in adapter_modes and stage1_artifact_path else None
        if self.stage1_adapter is not None:
            try:
                self.stage1_adapter.load()
                model_id = self.stage1_adapter.metadata.get("model_id")
                if model_id:
                    self.model_version = str(model_id)
            except Stage1AdapterError:
                self.stage1_adapter = None
                self.stage1_mode = "dummy_fallback"
                self.model_version = DEFAULT_MODEL_VERSION

    def infer(self, window: ProcessedWindow) -> InferenceResult:
        if self.stage1_adapter is not None:
            stage1_result = self.stage1_adapter.predict(window)
        else:
            stage1_result = predict_stage1(window)

        if self.stage2_mode in {"disabled", "placeholder_unknown", "static_moving_cnn"}:
            stage2_result = predict_stage2_unknown(window, stage1_result)
        else:
            stage2_result = predict_dummy_stage2(window, stage1_result)
        return build_inference_result(
            window=window,
            stage1_result=stage1_result,
            stage2_result=stage2_result,
            model_version=self.model_version,
        )

    def get_model_status(self) -> dict[str, str | None]:
        if self.stage1_adapter is None:
            return {"model_status": "dummy_ready", "model_version": self.model_version, "stage1_mode": self.stage1_mode}
        info = self.stage1_adapter.get_model_info()
        return {
            "model_status": f"{info.get('adapter_mode', 'checkpoint')}_ready",
            "model_version": self.model_version,
            "stage1_mode": self.stage1_mode,
            "stage2_mode": self.stage2_mode,
            "stage1_artifact_path": info.get("artifact_path"),
        }
