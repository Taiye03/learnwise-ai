# One live OCI Generative AI call. Run: python test_genai.py
from model_provider import OCIModelProvider

model = OCIModelProvider()
print("Model:", model.model_id)
from content_provider import PROBLEMS

for rung, hint in PROBLEMS["frac-add-1"]["hints"].items():
    print(f"\nRung {rung} approved: {hint}")
    print(f"Rung {rung} AI:       {model.generate_hint(hint, rung)}")
