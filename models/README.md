Models are project-local and intentionally not committed. Run
`.venv/Scripts/python.exe tools/setup_model.py` to fetch the pinned Basic Pitch
ONNX model into `.runtime/models`. No shared ComfyUI models are accessed.
The script records the source commit, SHA-256 hash and original license.
