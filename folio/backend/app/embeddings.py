"""MiniLM CPU inference tuned for the single-instance free demo."""
import os
from functools import cached_property
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

class LocalCPUEmbedding(ONNXMiniLM_L6_V2):
    def __init__(self):
        super().__init__(preferred_providers=['CPUExecutionProvider'])
    @cached_property
    def model(self):
        options=self.ort.SessionOptions()
        options.intra_op_num_threads=1
        options.inter_op_num_threads=1
        options.execution_mode=self.ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level=self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level=3
        options.add_session_config_entry('session.intra_op.allow_spinning','0')
        options.add_session_config_entry('session.inter_op.allow_spinning','0')
        return self.ort.InferenceSession(os.path.join(self.DOWNLOAD_PATH,self.EXTRACTED_FOLDER_NAME,'model.onnx'),
             providers=['CPUExecutionProvider'],sess_options=options)
