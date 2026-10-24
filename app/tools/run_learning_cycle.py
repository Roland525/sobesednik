"""Bounded local maintenance worker. Run only after the voice process releases GPU.

Training data, outputs and backups stay under ignored data/training. A failed
stage never changes the active model. No downloads occur inside this worker.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import ROOT, Config
from bot.learning_cycle import quality_gate, compatible_training_model
from bot.training_data import informative_dialogue
from bot.runtime import InstanceLock
from tools.process_tree import run_owned


STATE = ROOT / 'data/training/cycle-state.json'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def prerequisites():
    paths = ['.venv-training/Scripts/python.exe', 'models/training-qwen3-8b-v2/config.json',
             'tools/llama-cpp/bin/llama-quantize.exe',
             'tools/llama-cpp/src/llama.cpp-b11827/convert_hf_to_gguf.py']
    return all((ROOT / name).is_file() for name in paths)


def disk_ready():
    return shutil.disk_usage(ROOT).free >= 55 * 1024**3


def snapshot(guild):
    # Read a fresh snapshot, including deletions. Never reuse an earlier export.
    connection = sqlite3.connect((ROOT / 'data/learning.sqlite3').as_uri() + '?mode=ro', uri=True)
    try:
        count = connection.execute('SELECT count(*) FROM examples WHERE guild=?', (guild,)).fetchone()[0]
        rows = connection.execute('SELECT kind,prompt,target FROM examples WHERE guild=? ORDER BY rowid DESC LIMIT 256',
                                  (guild,)).fetchall()
    finally:
        connection.close()
    return count, [{'source': source, 'messages': [{'role': 'user', 'content': prompt},
                                                  {'role': 'assistant', 'content': target}]}
                   for source, prompt, target in reversed(rows)
                   if source != 'human_dialogue' or informative_dialogue(prompt, target)]


def execute(command, log, timeout):
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
    with log.open('w', encoding='utf-8') as output:
        status = run_owned(command, cwd=ROOT, env=env, stdout=output, timeout=timeout)
        if status:
            raise subprocess.CalledProcessError(status, str(command[0]))


def unload(model, config):
    import httpx
    response = httpx.post(config.ollama_host + '/api/generate', json={'model': model, 'keep_alive': 0},
                          timeout=90, trust_env=False)
    response.raise_for_status()


def commit_model(model, original_config, run):
    config_path = ROOT / 'config.json'
    if config_path.read_bytes() != original_config:
        raise RuntimeError('Configuration changed during training; candidate was not activated.')
    raw = json.loads(original_config.decode('utf-8-sig'))
    (run / 'config-before.json').write_bytes(original_config)
    raw['ollama_model'] = model
    atomic_json(config_path, raw)


def bounded_remove(path, run):
    path, run = path.resolve(), run.resolve()
    if path == run or run not in path.parents:
        raise ValueError('Cleanup path must be inside this training run.')
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()


def cycle(guild):
    config = Config.load()
    if not compatible_training_model(config.ollama_model):
        result={'status':'skipped','reason':'incompatible_selected_model','last_attempt':time.time(),'last_count':0}
        atomic_json(STATE,result)
        return result
    count, rows = snapshot(guild)
    if len(rows) < 16 or not prerequisites() or not disk_ready():
        result = {'status': 'skipped', 'reason': 'data_dependencies_or_disk',
                  'last_attempt': time.time(), 'last_count': 0}
        atomic_json(STATE, result)
        return result
    config = Config.load()
    original_config = (ROOT / 'config.json').read_bytes()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    run = ROOT / 'data/training/runs' / stamp
    run.mkdir(parents=True, exist_ok=False)
    state = {'status': 'running', 'guild': guild, 'last_count': count, 'last_attempt': time.time(),
             'run': str(run.relative_to(ROOT)), 'original_model': config.ollama_model}
    atomic_json(STATE, state)
    data = run / 'examples.jsonl'
    data.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    state['dataset_sha256'] = hashlib.sha256(data.read_bytes()).hexdigest()
    training_python = ROOT / '.venv-training/Scripts/python.exe'
    runtime_python = ROOT / '.venv/Scripts/python.exe'
    adapter, merged = run / 'adapter', run / 'merged'
    full, quantized = run / 'candidate-bf16.gguf', run / 'candidate-q4.gguf'
    candidate = 'sobesednik-learned-' + stamp + ':latest'
    loaded = config.ollama_model
    inference = ['--reasoning'] if config.ollama_reasoning else []
    stage = 'baseline'
    try:
        import asyncio
        from bot.feedback import FeedbackStore,evaluate_feedback,feedback_gate
        feedback_store=FeedbackStore(ROOT/'data/learning.sqlite3')
        try:feedback_cases=feedback_store.cases(guild)
        finally:feedback_store.close()
        atomic_json(run/'feedback-cases.json',feedback_cases)
        feedback_before=asyncio.run(evaluate_feedback(config.ollama_host,config.ollama_model,feedback_cases))
        atomic_json(run/'feedback-baseline.json',feedback_before)
        for stage_name, model in [('baseline', config.ollama_model)]:
            label = 'auto-' + stamp + '-' + stage_name
            execute([runtime_python, 'tools/evaluate_answers.py', '--model', model, '--label', label, '--resident-stt', *inference],
                    run / (stage_name + '.log'), 900)
            shutil.copy2(ROOT / ('reports/answers-' + label + '.json'), run / (stage_name + '.json'))
        unload(loaded, config)
        loaded = None
        stages = [
            ('train', [training_python, 'tools/train_local_lora.py', '--data', data, '--output', adapter,
                       '--steps', str(min(400, max(40, len(rows)*2)))], 1200),
            ('merge', [training_python, 'tools/merge_lora_candidate.py', '--adapter', adapter, '--output', merged], 1200),
            ('convert', [training_python, 'tools/convert_candidate_gguf.py', '--model', merged, '--output', full], 1200),
            ('quantize', [ROOT / 'tools/llama-cpp/bin/llama-quantize.exe', full, quantized, 'Q4_K_M', '4'], 900),
        ]
        for stage, command, timeout in stages:
            state['stage'] = stage
            atomic_json(STATE, state)
            execute(command, run / (stage + '.log'), timeout)
            if stage == 'convert':
                bounded_remove(merged, run)
            if stage == 'quantize':
                bounded_remove(full, run)
        stage = 'import'
        state['stage'] = stage
        atomic_json(STATE, state)
        import httpx
        template_response = httpx.post(config.ollama_host + '/api/show', json={'model': config.ollama_model},
                                       timeout=30, trust_env=False)
        template_response.raise_for_status()
        template = template_response.json()['template']
        modelfile = run / 'Modelfile'
        modelfile.write_text('FROM ./candidate-q4.gguf\nTEMPLATE """' + template + '"""\n'
                             'PARAMETER num_ctx 4096\nPARAMETER temperature 0.4\n', encoding='utf-8')
        execute([ROOT / 'tools/ollama/ollama.exe', 'create', candidate, '--file', modelfile], run / 'import.log', 900)
        bounded_remove(quantized, run)
        stage, loaded = 'evaluation', candidate
        state['stage'] = stage
        atomic_json(STATE, state)
        label = 'auto-' + stamp + '-candidate'
        execute([runtime_python, 'tools/evaluate_answers.py', '--model', candidate, '--label', label, '--resident-stt', *inference],
                run / 'evaluation.log', 900)
        shutil.copy2(ROOT / ('reports/answers-' + label + '.json'), run / 'candidate.json')
        training = json.loads((adapter / 'training-report.json').read_text(encoding='utf-8'))
        baseline = json.loads((run / 'baseline.json').read_text(encoding='utf-8'))
        evaluation = json.loads((run / 'candidate.json').read_text(encoding='utf-8'))
        reasons = quality_gate(baseline, evaluation, training)
        feedback_after=asyncio.run(evaluate_feedback(config.ollama_host,candidate,feedback_cases))
        atomic_json(run/'feedback-candidate.json',feedback_after)
        reasons.extend(feedback_gate(feedback_before,feedback_after))
        state.update(status='rejected' if reasons else 'accepted', reasons=reasons, candidate=candidate)
        if not reasons:
            stage = 'activation'
            commit_model(candidate, original_config, run)
        else:
            execute([ROOT / 'tools/ollama/ollama.exe', 'rm', candidate], run / 'discard.log', 60)
    except Exception as error:
        state.update(status='failed', stage=stage, error=type(error).__name__)
    finally:
        if loaded:
            try:
                unload(loaded, config)
            except Exception:
                pass
        state['finished'] = time.time()
        atomic_json(run / 'result.json', state)
        atomic_json(STATE, state)
        # Only generated, reproducible large intermediates are removed.
        for path in (merged, full, quantized):
            bounded_remove(path, run)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--guild', required=True, type=int)
    args = parser.parse_args()
    with InstanceLock(ROOT / '.learning-maintenance.lock'):
        # Refuse maintenance if another voice instance has not released its lock.
        with InstanceLock(ROOT / '.serega.lock'):
            pass
        result = cycle(args.guild)
    print(json.dumps({key: result[key] for key in ('status', 'reason', 'reasons', 'error') if key in result}))


if __name__ == '__main__':
    main()
