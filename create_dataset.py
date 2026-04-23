import pandas as pd
import json
import numpy as np
from pathlib import Path

def to_python(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    return obj

train_df = pd.read_parquet("data/hotpot_qa/fullwiki/train-00000-of-00002.parquet")
print("Tong so mau train:", len(train_df))

samples = []
for i in range(100):
    row = train_df.iloc[i]
    titles = row["context"]["title"]
    sentences = row["context"]["sentences"]
    context = [{"title": str(t), "text": " ".join(to_python(s)) if isinstance(s, (list, np.ndarray)) else str(s)} for t, s in zip(titles, sentences)]

    level = str(row.get("level", "medium")).lower()
    difficulty = "easy" if "easy" in level else ("hard" if "hard" in level else "medium")

    samples.append({
        "qid": f"hpqa_{i}",
        "difficulty": str(difficulty),
        "question": str(row["question"]),
        "gold_answer": str(row["answer"]),
        "context": context
    })

out_path = Path("data/hotpot_qa_100.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(samples, f, ensure_ascii=False, indent=2)

print(f"Da tao {len(samples)} mau vao {out_path}")
print("Vi du mau 0:", samples[0]["question"][:100], "...")
print("Do kho:", samples[0]["difficulty"])