"""Builds a tiny tokenizer and models offline, saves and reloads them, runs a pipeline."""

import os
import tempfile

CORPUS = [
    "the cat sat on the mat", "the dog sat on the log", "a cat and a dog sat together",
    "the mat and the log are on the floor", "the cat saw the dog on the mat",
] * 4  # fmt: skip


def make_tokenizer():
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast

    tokenizer = Tokenizer(models.WordPiece(unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    trainer = trainers.WordPieceTrainer(
        vocab_size=40, special_tokens=["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"],
        show_progress=False,  # its progress lines carry durations
    )  # fmt: skip
    tokenizer.train_from_iterator(CORPUS, trainer)
    return PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, unk_token="[UNK]", pad_token="[PAD]",
        cls_token="[CLS]", sep_token="[SEP]", mask_token="[MASK]",
    )  # fmt: skip


def run(home: str) -> None:
    # Set before transformers is imported: its cache paths are read at import time.
    os.environ.update(HF_HUB_OFFLINE="1", HF_HOME=home, TOKENIZERS_PARALLELISM="false",
                      HF_HUB_DISABLE_PROGRESS_BARS="1")  # fmt: skip
    import torch
    from transformers import BertConfig, BertForMaskedLM, GPT2Config, GPT2LMHeadModel, pipeline
    from transformers.utils import logging as hf_logging

    hf_logging.disable_progress_bar()  # its bars carry rates, which differ from run to run

    torch.set_num_threads(1)
    tokenizer = make_tokenizer()
    print("token ids", tokenizer("the cat sat on the mat")["input_ids"])

    torch.manual_seed(0)
    config = BertConfig(
        vocab_size=len(tokenizer), hidden_size=16, num_hidden_layers=2,
        num_attention_heads=2, intermediate_size=32, max_position_embeddings=32,
    )  # fmt: skip
    model = BertForMaskedLM(config).eval()
    with tempfile.TemporaryDirectory(dir=home) as directory:
        model.save_pretrained(directory)
        print("saved", sorted(os.listdir(directory)))
        reloaded = BertForMaskedLM.from_pretrained(directory)
        filler = pipeline("fill-mask", model=reloaded, tokenizer=tokenizer)
        for guess in filler("the cat sat on the [MASK]", top_k=3):
            print("fill-mask", guess["token"], f"{guess['score']:.4f}")

    torch.manual_seed(0)
    gpt = GPT2LMHeadModel(GPT2Config(
        vocab_size=len(tokenizer), n_embd=16, n_layer=2, n_head=2, n_positions=32,
        bos_token_id=2, eos_token_id=3, pad_token_id=0,
    )).eval()  # fmt: skip
    prompt = tokenizer("the cat", return_tensors="pt")
    out = gpt.generate(**prompt, max_new_tokens=6, do_sample=False)
    print("generate", out[0].tolist())
    print("decoded", tokenizer.decode(out[0]))


def main() -> None:
    with tempfile.TemporaryDirectory() as home:
        run(home)
