"""Export an OpenMed disease-detection model to ONNX for in-browser use (Transformers.js).

    pip install "optimum[exporters]" onnxruntime transformers
    python export_onnx.py <model_path_or_hf_id> <out_dir>

Then host <out_dir> (e.g. push to a Hugging Face repo) and set AI_MODEL in index.html
to that repo id. RxDx will run disease NER fully in the browser.
"""
import subprocess, sys

model = sys.argv[1] if len(sys.argv) > 1 else "OpenMed/OpenMed-NER-DiseaseDetect-SuperClinical-434M"
out = sys.argv[2] if len(sys.argv) > 2 else "rxdx-disease-onnx"
subprocess.run(
    ["optimum-cli", "export", "onnx", "--model", model, "--task", "token-classification", out],
    check=True,
)
print(f"Exported to {out}. Host it (e.g. on Hugging Face) and set AI_MODEL to its id.")
