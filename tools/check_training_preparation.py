"""Real local tokenizer + synthetic facts; CPU only, no inference or private data."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tokenizers import Tokenizer
from bot.config import ROOT
from bot.training_data import prepare_samples


CASES = [
    ('Сколько будет два плюс два?', 'Четыре.'),
    ('Сколько будет три плюс три?', 'Шесть.'),
    ('Назови столицу Франции.', 'Париж.'),
    ('Назови столицу Италии.', 'Рим.'),
    ('Что такое метеор?', 'Это световое явление при входе тела в атмосферу.'),
    ('Что такое метеорит?', 'Это тело, достигшее поверхности Земли.'),
    ('Может ли звук распространяться в вакууме?', 'Нет, для звука нужна среда.'),
    ('Почему небо голубое?', 'Короткие волны солнечного света сильнее рассеиваются в атмосфере.'),
    ('Что больше: килобайт или мегабайт?', 'Мегабайт.'),
    ('Для чего нужна оперативная память?', 'Для временного хранения данных работающих программ.'),
    ('Как называется спутник Земли?', 'Луна.'),
    ('Пушкин получил Нобелевскую премию?', 'Нет. При его жизни Нобелевской премии ещё не существовало.'),
    ('Какой новый герой появился в сегодняшнем патче Dota?', 'Без актуальных патчноутов я не могу это подтвердить.'),
    ('Ты видишь мою демонстрацию без переданных кадров?', 'Нет, без кадров я не вижу демонстрацию.'),
    ('Какой мой любимый цвет, если я ещё не называл его?', 'Не знаю, ты его ещё не называл.'),
    ('Один метр это сколько сантиметров?', 'Сто сантиметров.'),
]


def main():
    path = ROOT / 'models/training-qwen3-8b-v2/tokenizer.json'
    tokenizer = Tokenizer.from_file(str(path))
    class Encoder:
        def encode(self, text, add_special_tokens=False):
            return tokenizer.encode(text, add_special_tokens=add_special_tokens).ids
    rows = [{'source': 'correction', 'messages': [{'role': 'user', 'content': q},
                                                {'role': 'assistant', 'content': a}]} for q, a in CASES]
    training, heldout, stats = prepare_samples(rows, Encoder(), 256, 16)
    assert not ({s[2] for s in training} & {s[2] for s in heldout})
    assert all(any(n != -100 for n in labels) and labels[0] == -100 for _, labels, _ in training + heldout)
    for marker in ('<|im_start|>', '<|im_end|>'):
        assert tokenizer.token_to_id(marker) is not None
    output = ROOT / 'data/training/qualification.jsonl'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), encoding='utf-8')
    report = {'scope': 'real tokenizer, synthetic qualification corpus; no weight training',
              'tokenizer_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'data_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'stats': stats,
              'max_sequence_tokens': max(len(s[0]) for s in training + heldout),
              'training_examples': len(training), 'heldout_examples': len(heldout),
              'target_labels_only': True, 'question_overlap': 0}
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports/training-preflight-oct22.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
