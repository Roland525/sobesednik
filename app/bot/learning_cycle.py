"""CPU-only maintenance policy and conservative candidate acceptance gate."""
from dataclasses import dataclass
import math
import statistics
import re


def compatible_training_model(model):
    """The prepared LoRA base is Qwen3 8B, never silently replace a chosen family."""
    return (model.casefold()=='huihui_ai/qwen3-abliterated:8b'
            or bool(re.fullmatch(r'sobesednik-learned-\d{8}-\d{6}:latest',model.casefold())))


@dataclass
class MaintenancePolicy:
    idle_seconds: float = 1800
    interval_seconds: float = 21600
    new_examples: int = 32

    def ready(self, *, now, idle_since, humans, count, last_count, last_attempt):
        return (not humans and now - idle_since >= self.idle_seconds
                and now - last_attempt >= self.interval_seconds
                and count - last_count >= self.new_examples)


def quality_gate(baseline, candidate, training):
    reasons = []
    before, after, changed = (training.get(key, float('nan')) for key in
                              ('heldout_loss_before', 'heldout_loss_after', 'lora_B_norm'))
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (before, after, changed)):
        reasons.append('nonfinite_training')
    elif after >= before or changed <= 0:
        reasons.append('training_did_not_improve')
    rows = [baseline.get('cases', []), candidate.get('cases', [])]
    maps = [{row['case']: row for row in group} for group in rows]
    if ('failure' in baseline or 'failure' in candidate or len(rows[0]) < 15
        or any(len(group) != len(mapping) for group, mapping in zip(rows, maps))
        or set(maps[0]) != set(maps[1])):
        return reasons + ['incomplete_evaluation']
    if any(row.get('narrow_check') is True and maps[1][name].get('narrow_check') is not True
           for name, row in maps[0].items()):
        reasons.append('answer_regression')
    if sum(row.get('narrow_check') is True for row in rows[1]) < 14:
        reasons.append('insufficient_answer_quality')
    latencies = []
    for group in rows:
        values = []
        for row in group:
            elapsed = row.get('elapsed_ms', float('nan'))
            first = row.get('timings', {}).get('llm_first_sentence_ms', elapsed)
            if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
                       for v in (elapsed, first)):
                return reasons + ['invalid_latency']
            values.append(first)
        latencies.append(statistics.median(values))
    if latencies[1] > max(2500, latencies[0] * 1.25):
        reasons.append('latency_regression')
    return reasons
