from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

from .mock_runtime import actor_answer, evaluator, reflector
from .schemas import AttemptTrace, QAExample, ReflectionEntry, RunRecord
import time 

@dataclass
class BaseAgent:
    agent_type: Literal["react", "reflexion"]
    max_attempts: int = 1
    confidence_threshold: float = 0.95  # cho adaptive_max_attempts
    max_memory_tokens: int = 500  # cho memory_compression

    def _compress_memory(self, reflection_memory: list[str]) -> list[str]:
        """memory_compression: nén memory khi quá dài"""
        total_tokens = sum(len(m) // 4 for m in reflection_memory)
        if total_tokens <= self.max_memory_tokens:
            return reflection_memory
        # Giữ lại 50% gần nhất
        keep_count = max(1, len(reflection_memory) // 2)
        return reflection_memory[-keep_count:]

    def _classify_failure_mode(self, example: QAExample, traces: list[AttemptTrace], reflections: list[ReflectionEntry], final_score: int, final_answer: str) -> str:
        """Phân loại failure mode đa dạng cho báo cáo"""
        if final_score == 1:
            return "none"

        # Kiểm tra số attempts
        if len(traces) >= self.max_attempts:
            # Đã thử hết lượt mà vẫn sai
            if len(reflections) >= 2:
                return "reflection_overfit"  # reflexion không giúp cải thiện

        # Kiểm tra pattern câu trả lời
        answer_normalized = final_answer.lower().strip()

        # Entity drift: trả lời có entity khác với gold
        if len(traces) > 1:
            prev_answers = [t.answer for t in traces[:-1]]
            if any(a.lower().strip() != answer_normalized for a in prev_answers):
                return "entity_drift"  # thay đổi entity qua các attempt

        # Incomplete multi-hop: câu hỏi có từ "which", "what" nhưng trả lời ngắn
        question_words = example.question.lower().split()
        if any(w in question_words for w in ["which", "what", "where", "who"]) and len(answer_normalized.split()) <= 2:
            if len(traces) > 1 and len(traces[0].answer.split()) > len(answer_normalized.split()):
                return "incomplete_multi_hop"  # câu trả lời ngắn hơn attempt đầu

        # Looping: cùng một câu trả lời lặp lại
        if len(traces) >= 2:
            if traces[-1].answer.lower().strip() == traces[-2].answer.lower().strip():
                return "looping"  # lặp lại câu trả lời

        # Default
        return "wrong_final_answer"

    def run(self, example: QAExample) -> RunRecord:
        reflection_memory: list[str] = []
        reflections: list[ReflectionEntry] = []
        traces: list[AttemptTrace] = []
        final_answer = ""
        final_score = 0
        stopped_early = False

        for attempt_id in range(1, self.max_attempts + 1):
            attempt_start = time.perf_counter()

            answer, token = actor_answer(example, attempt_id, reflection_memory)
            judge = evaluator(example, answer)

            # adaptive_max_attempts: dừng sớm nếu confidence cao
            if judge.score == 1 and getattr(judge, 'confidence', 1.0) >= self.confidence_threshold:
                stopped_early = True

            if self.agent_type == "reflexion" and judge.score == 0 and attempt_id < self.max_attempts:
                reflection = reflector(example, attempt_id, answer, judge)
                reflections.append(reflection)
                reflection_memory.append(reflection.lesson + " " + reflection.next_strategy)
                # memory_compression: nén memory nếu quá dài
                reflection_memory = self._compress_memory(reflection_memory)

            attempt_latency = int((time.perf_counter() - attempt_start) * 1000)
            trace = AttemptTrace(
                attempt_id=attempt_id, answer=answer, score=judge.score,
                reason=judge.reason, token_estimate=token, latency_ms=attempt_latency,
                reflection=reflections[-1] if reflections else None
            )
            traces.append(trace)

            final_answer = answer
            final_score = judge.score
            if stopped_early or judge.score == 1:
                break

        total_tokens = sum(t.token_estimate for t in traces)
        total_latency = sum(t.latency_ms for t in traces)
        failure_mode = self._classify_failure_mode(example, traces, reflections, final_score, final_answer)
        return RunRecord(
            qid=example.qid, question=example.question, gold_answer=example.gold_answer,
            agent_type=self.agent_type, predicted_answer=final_answer,
            is_correct=bool(final_score), attempts=len(traces), token_estimate=total_tokens,
            latency_ms=total_latency, failure_mode=failure_mode, reflections=reflections,
            traces=traces
        )

class ReActAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(agent_type="react", max_attempts=1)

class ReflexionAgent(BaseAgent):
    def __init__(self, max_attempts: int = 3) -> None:
        super().__init__(agent_type="reflexion", max_attempts=max_attempts)
