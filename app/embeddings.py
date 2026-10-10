"""Lightweight lexical BM25 vectors, not neural/semantic embeddings.

Document term frequency and length normalization are computed here. Qdrant's
IDF modifier supplies inverse document frequency at query time. Average document
length is a fixed tuning value, so this is a BM25 variant, not corpus-exact BM25.
"""
from collections import Counter
import hashlib
import math
import re
from app.config import Settings
from app.documents import Chunk, DocumentError

STOPWORDS = frozenset('''а без бы был была были было в вам вас весь во вот все всего всех вы где да для до его ее если есть еще же за здесь и из или им их к как какой какая какие когда кто ли либо мне мы на над надо наш не него нее нет ни но о об от по под при про с со так такой там те тем то того тоже ты у уже что чтобы это этот эта эти я the a an and or of to in on at for is are was were be been with by from this that these those it its as what which who when how'''.split())
# Deliberately conservative suffix normalization, not a full morphological analyzer.
RUSSIAN_SUFFIXES = tuple(sorted(set('''иями ями ами ого его ому ему ыми ими ая яя ое ее ые ие ой ей ом ем ов ев ах ях их ых ую юю ают яют ает яет ить ать ять ете ите ует уют а я ы и у ю е о ь'''.split()), key=len, reverse=True))


def normalize_term(word: str) -> str:
    word = word.lower().replace('ё', 'е')
    if re.fullmatch('[а-я]+', word):
        for suffix in RUSSIAN_SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[:-len(suffix)]
    elif re.fullmatch('[a-z]+', word) and len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    words = re.findall(r'[a-zа-яё0-9]+', text.lower())
    return [normalize_term(word) for word in words if word.replace('ё', 'е') not in STOPWORDS and (len(word) > 1 or word.isdigit())]


def term_id(term: str) -> int:
    # Stable 32-bit sparse vocabulary, independent of process/host/Python hash seed.
    # Rare hash collisions are possible, as in other hashed lexical encoders.
    return int.from_bytes(hashlib.blake2b(term.encode('utf-8'), digest_size=4).digest(), 'little')


class BM25Embeddings:
    def __init__(self, config: Settings):
        self.config = config
        if not math.isfinite(config.bm25_k1) or not math.isfinite(config.bm25_b) or not math.isfinite(config.bm25_avg_length):
            raise ValueError('BM25 parameters must be finite')
        if config.bm25_k1 <= 0 or not 0 <= config.bm25_b <= 1 or config.bm25_avg_length <= 0:
            raise ValueError('Invalid BM25 parameters')

    def prepare_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        output = [Chunk(c.text, c.page, 0) for c in chunks if tokenize(c.text)]
        if not output:
            raise DocumentError('В документе нет слов, пригодных для поиска.')
        return [Chunk(c.text, c.page, i + 1) for i, c in enumerate(output)]

    def embeddings(self, texts: list[str], query: bool = False) -> list[dict]:
        result = []
        for text in texts:
            terms = tokenize(text)
            counts = Counter(terms)
            weights = {}
            length = len(terms)
            for term, frequency in counts.items():
                index = term_id(term)
                if query:
                    weight = 1.0  # Qdrant multiplies this by its IDF statistic.
                else:
                    k1, b = self.config.bm25_k1, self.config.bm25_b
                    weight = frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * length / self.config.bm25_avg_length))
                weights[index] = weights.get(index, 0.0) + weight
            indices = sorted(weights)
            result.append({'indices': indices, 'values': [weights[i] for i in indices]})
        return result
