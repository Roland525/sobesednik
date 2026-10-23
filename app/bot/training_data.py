"""CPU-only training validation; reject bad datasets before allocating a model."""
import hashlib
import re


def informative_dialogue(prompt, target):
    """Conservative automatic Q/A selection; never an assertion of factual truth.

    Names/preferences belong in attributed memory, not anonymous model targets.
    Explicit human correction/confirmation is handled separately by the caller.
    """
    if not isinstance(prompt, str) or not isinstance(target, str):
        return False
    words = re.findall(r'\w+', target.casefold())
    question = '?' in prompt or re.match(r'^(?:почему|как|что такое|чем|зачем|когда|сколько)\b', prompt, re.I)
    personal = re.search(r'\b(?:я|меня|мне|мой|моя|моё|мое|мои|тебя|тебе|твой|твоя)\b',
                         prompt + ' ' + target, re.I)
    return bool(question and not personal and '?' not in target
                and len(words) >= 7 and len(set(words))/len(words) >= .7
                and not re.search(r'\b(\w+)(?:\s+\1){2}', target, re.I)
                and prompt.strip(' .!?').casefold() != target.strip(' .!?').casefold())


def training_texts(prompt, target):
    if not isinstance(prompt, str) or not isinstance(target, str) or not prompt.strip() or not target.strip():
        raise ValueError('Training messages must contain nonempty text')
    if any(marker in prompt or marker in target for marker in ('<|', '|>')):
        raise ValueError('Control tokens are not allowed in conversation data')
    prefix = '<|im_start|>user\n' + prompt + '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    return prefix, prefix + target + '<|im_end|>\n'


def prepare_samples(rows, tokenizer, sequence_length, min_examples):
    ranks = {'human_dialogue': 0, 'confirmed': 1, 'correction': 2}
    prompts, duplicates, overlong = {}, 0, 0
    for row in rows:
        messages, source = row.get('messages'), row.get('source')
        if (source not in ranks or not isinstance(messages, list) or len(messages) != 2
            or messages[0].get('role') != 'user' or messages[1].get('role') != 'assistant'):
            raise ValueError('Unsupported training example format')
        prompt, target = messages[0].get('content'), messages[1].get('content')
        training_texts(prompt, target)
        normalized = re.sub(r'\s+', ' ', prompt.casefold().replace('ё', 'е')).strip(' .!?')
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        if digest in prompts:
            duplicates += 1
        if digest not in prompts or ranks[source] >= ranks[prompts[digest][0]]:
            prompts[digest] = (source, prompt, target)
    samples = []
    for digest, (_, prompt, target) in sorted(prompts.items()):
        prefix_text, full_text = training_texts(prompt, target)
        prefix = tokenizer.encode(prefix_text, add_special_tokens=False)
        tokens = tokenizer.encode(full_text, add_special_tokens=False)
        if tokens[:len(prefix)] != prefix:
            raise ValueError('Tokenizer boundary mismatch; refusing incorrect response labels')
        if len(tokens) > sequence_length:
            overlong += 1
            continue
        if len(tokens) <= len(prefix):
            raise ValueError('Training target has no tokens')
        samples.append((tokens, [-100] * len(prefix) + tokens[len(prefix):], digest))
    if len(samples) < max(2, min_examples):
        raise RuntimeError(f'Not enough distinct questions fit the sequence length: {len(samples)}.')
    heldout_count = max(1, len(samples) // 5)
    stats = {'input_examples': len(rows), 'duplicate_prompts': duplicates, 'overlong': overlong,
             'distinct_questions': len(samples), 'heldout_examples': heldout_count}
    return samples[heldout_count:], samples[:heldout_count], stats
