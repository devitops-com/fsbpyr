"""Tokenizes text with nltk, spaCy, sentencepiece and tiktoken, with no downloads."""

import tempfile
from pathlib import Path

import nltk
import sentencepiece as spm
import spacy
import tiktoken
from nltk.probability import FreqDist
from nltk.stem import PorterStemmer
from nltk.tokenize import RegexpTokenizer
from spacy.matcher import Matcher

TEXT = "The runners were running quickly, and the runners kept running through the rain."


def run_nltk() -> None:
    tokens = RegexpTokenizer(r"\w+").tokenize(TEXT.lower())
    stems = [PorterStemmer().stem(token) for token in tokens]
    print("nltk stems", stems[:6])
    print("nltk bigrams", list(nltk.ngrams(tokens, 2))[:3])
    print("nltk freq", FreqDist(stems).most_common(3))


def run_spacy() -> None:
    nlp = spacy.blank("en")
    doc = nlp("Dr. Smith's cat isn't running to New York.")
    print("spacy tokens", [token.text for token in doc])
    matcher = Matcher(nlp.vocab)
    matcher.add("RUN", [[{"LOWER": "running"}, {"LOWER": "to"}]])
    print("spacy matches", [doc[start:end].text for _, start, end in matcher(doc)])
    ruler = nlp.add_pipe("entity_ruler")
    ruler.add_patterns([{"label": "GPE", "pattern": "New York"}])
    print("spacy ents", [(ent.text, ent.label_) for ent in nlp(doc.text).ents])
    german = spacy.util.get_lang_class("de")()
    print("spacy de", [token.text for token in german("Das ist z.B. ein Haus.")])
    print("spacy registry", spacy.registry.tokenizers.get("spacy.Tokenizer.v1").__name__)


def run_sentencepiece(directory: Path) -> None:
    prefix = str(directory / "tiny")
    spm.SentencePieceTrainer.train(
        input="work/corpus.txt", model_prefix=prefix, vocab_size=40, num_threads=1,
        model_type="unigram", minloglevel=2,
    )  # fmt: skip
    processor = spm.SentencePieceProcessor(model_file=prefix + ".model")
    ids = processor.encode("the cat sees a red ball")
    print("sentencepiece ids", ids)
    print("sentencepiece decode", processor.decode(ids))


def run_tiktoken() -> None:
    print("tiktoken encodings", sorted(tiktoken.list_encoding_names()))
    ranks = {bytes([byte]): byte for byte in range(256)}
    ranks[b"th"] = 256
    encoding = tiktoken.Encoding(
        name="corpus", pat_str=r"\w+|\s+|[^\w\s]+", mergeable_ranks=ranks,
        special_tokens={"<|end|>": 257},
    )  # fmt: skip
    ids = encoding.encode("the third<|end|>", allowed_special="all")
    print("tiktoken ids", ids, encoding.decode(ids))


def main() -> None:
    run_nltk()
    run_spacy()
    with tempfile.TemporaryDirectory() as directory:
        run_sentencepiece(Path(directory))
    run_tiktoken()
