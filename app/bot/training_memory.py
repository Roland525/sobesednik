"""Optional Qwen3 QLoRA preparation without a full FP32 vocabulary copy."""


def prepare_low_memory_qlora(model):
    import torch
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    for module in model.modules():
        if isinstance(module, torch.nn.LayerNorm) or module.__class__.__name__.endswith('RMSNorm'):
            module.to(dtype=torch.float32)
    # Non-reentrant checkpointing works with frozen embedding inputs.
    # Large embedding/head tensors keep the checkpoint's BF16/FP16 dtype;
    # normalization and trainable adapters retain FP32 precision.
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.config.use_cache = False
    return model
