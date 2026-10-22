"""CPU checks in the optional training environment; no model downloads."""
import unittest
import torch
from transformers import Qwen3Config, Qwen3ForCausalLM
from peft import get_peft_model, LoraConfig

from bot.training_memory import prepare_low_memory_qlora


class QLoRAPreparationTests(unittest.TestCase):
    def test_half_precision_frozen_embeddings_and_finite_lora_gradients(self):
        torch.manual_seed(73)
        model = Qwen3ForCausalLM(Qwen3Config(vocab_size=64, hidden_size=32, intermediate_size=64,
                                            num_hidden_layers=1, num_attention_heads=2,
                                            num_key_value_heads=2, head_dim=16)).to(torch.bfloat16)
        prepare_low_memory_qlora(model)
        self.assertEqual(model.get_input_embeddings().weight.dtype, torch.bfloat16)
        self.assertEqual(model.get_output_embeddings().weight.dtype, torch.bfloat16)
        self.assertTrue(all(not p.requires_grad for p in model.parameters()))
        norms = [m for m in model.modules() if m.__class__.__name__.endswith('RMSNorm')]
        self.assertTrue(norms)
        self.assertTrue(all(m.weight.dtype == torch.float32 for m in norms))
        model = get_peft_model(model, LoraConfig(r=2, lora_alpha=4, target_modules=['q_proj','v_proj'],
                                                task_type='CAUSAL_LM'))
        model.train()
        ids = torch.tensor([[1, 2, 3, 4, 5, 6]])
        with torch.autocast('cpu', dtype=torch.bfloat16):
            loss = model(input_ids=ids, labels=ids).loss
        loss.backward()
        grads = [p.grad for p in model.parameters() if p.requires_grad]
        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(all(g is not None and torch.isfinite(g).all() for g in grads))
        self.assertTrue(any(g.abs().sum() > 0 for g in grads))
