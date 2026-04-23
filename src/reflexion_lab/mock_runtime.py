from __future__ import annotations

from rich import json
from .schemas import QAExample, JudgeResult, ReflectionEntry
from .utils import normalize_answer
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from .prompts import ACTOR_SYSTEM, EVALUATOR_SYSTEM, REFLECTOR_SYSTEM
load_dotenv()

FAILURE_MODE_BY_QID = {"hp2": "incomplete_multi_hop", "hp4": "wrong_final_answer", "hp6": "entity_drift", "hp8": "entity_drift"}

model = ChatOpenAI(
    model="qwen2.5:3b",
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    temperature=0.2,
    streaming=False
)


def actor_answer(example: QAExample, attempt_id: int, reflection_memory: list[str]):
    context_text = " ".join([chunk.text for chunk in example.context])

    reflection_section = ""
    if reflection_memory:
        reflection_section = "Bài học từ các lần thử trước: \n" + "\n".join(f"- {m}" for m in reflection_memory)

    full_prompt = ACTOR_SYSTEM.format(context=context_text, question=example.question) + "\n" + reflection_section
    response = model.invoke(full_prompt)
    usage = response.usage_metadata or {}
    input_tok = usage.get("input_tokens", 0) or 0
    output_tok = usage.get("output_tokens", 0) or 0
    token_count = usage.get("total_tokens", input_tok + output_tok)
    if not token_count:
        token_count = len(full_prompt) // 4
    return response.content.strip(), token_count
    

def evaluator(example: QAExample, answer: str) -> JudgeResult:
    full_prompt = EVALUATOR_SYSTEM.format(
        question=example.question,
        answer=answer,
        ground_truth=example.gold_answer
    )

    response = model.invoke(full_prompt)
    content = response.content.strip() if response.content else ""

    # Thử parse JSON (3 lớp fallback)
    parsed = None
    # Lớp 1: JSON thuần
    try:
        parsed = json.loads(content)
    except Exception:
        pass
    # Lớp 2: Markdown code block
    if parsed is None:
        import re
        match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
            except Exception:
                pass
    # Lớp 3: Plain text có key-value
    if parsed is None:
        import re
        score_match = re.search(r'"score"\s*:\s*(\d+)', content)
        reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', content)
        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', content)
        if score_match and reason_match:
            parsed = {
                "score": int(score_match.group(1)),
                "reason": reason_match.group(1),
                "confidence": float(conf_match.group(1)) if conf_match else 1.0
            }

    # Nếu parse được JSON từ model → dùng kết quả model
    if parsed:
        return JudgeResult(
            score=parsed.get("score", 0),
            reason=parsed.get("reason", "No reason provided."),
            missing_evidence=parsed.get("missing_evidence", []),
            spurious_claims=parsed.get("spurious_claims", []),
            confidence=parsed.get("confidence", 1.0)
        )

    # Fallback cuối cùng: so sánh string trực tiếp
    if normalize_answer(example.gold_answer) == normalize_answer(answer):
        return JudgeResult(score=1, reason="Correct", missing_evidence=[], spurious_claims=[], confidence=1.0)
    return JudgeResult(score=0, reason="Incorrect", missing_evidence=[], spurious_claims=[answer], confidence=0.0)
    
def reflector(example: QAExample, attempt_id: int, answer: str, judge: JudgeResult) -> ReflectionEntry:
    # strategy = "Do the second hop explicitly: birthplace city -> river through that city." if example.qid == "hp2" else "Verify the final entity against the second paragraph before answering."
    # return ReflectionEntry(attempt_id=attempt_id, failure_reason=judge.reason, lesson="A partial first-hop answer is not enough; the final answer must complete all hops.", next_strategy=strategy)
    full_prompt = REFLECTOR_SYSTEM.format(
        question=example.question,
        wrong_answer=answer,
        ground_truth=example.gold_answer
    )
    
    response = model.invoke(full_prompt)
    content = response.content.strip() if response.content else ""

    # Thử parse JSON (3 lớp fallback)
    parsed = None
    # Lớp 1: JSON thuần
    try:
        parsed = json.loads(content)
    except Exception:
        pass
    # Lớp 2: Markdown code block
    if parsed is None:
        import re
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
            except Exception:
                pass
    # Lớp 3: Plain text có key-value
    if parsed is None:
        import re
        lesson_match = re.search(r'"lesson"\s*:\s*"([^"]*)"', content)
        strategy_match = re.search(r'"strategy"\s*:\s*"([^"]*)"', content)
        failure_match = re.search(r'"failure_reason"\s*:\s*"([^"]*)"', content)
        next_match = re.search(r'"next_strategy"\s*:\s*"([^"]*)"', content)
        if lesson_match or strategy_match:
            parsed = {
                "lesson": lesson_match.group(1) if lesson_match else "",
                "strategy": strategy_match.group(1) if strategy_match else "",
                "failure_reason": failure_match.group(1) if failure_match else judge.reason,
                "next_strategy": next_match.group(1) if next_match else ""
            }

    # Nếu parse được JSON từ model → dùng kết quả model
    if parsed:
        return ReflectionEntry(
            attempt_id=attempt_id,
            lesson=parsed.get("lesson", ""),
            strategy=parsed.get("strategy", ""),
            failure_reason=parsed.get("failure_reason", judge.reason),
            next_strategy=parsed.get("next_strategy", "")
        )

    # Fallback cuối cùng
    return ReflectionEntry(
        attempt_id=attempt_id,
        lesson="Câu trả lời chưa hoàn thành multi-hop reasoning. Cần xác minh câu trả lời cuối cùng với context.",
        strategy="Xác minh entity cuối cùng với đoạn context thứ 2.",
        failure_reason=judge.reason,
        next_strategy="Kiểm tra tất cả các bước suy luận trước khi trả lời."
    )