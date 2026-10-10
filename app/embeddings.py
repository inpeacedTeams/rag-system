"""Real CPU/ONNX embeddings. No remote embeddings endpoint or synthetic vectors."""
import math
import threading
from app.config import Settings
from app.documents import Chunk, DocumentError
from app.providers import ProviderError


class LocalEmbeddings:
    def __init__(self, config: Settings):
        self.config = config
        self._model = None
        self._tokenizer = None
        self._lock = threading.RLock()
        self.token_budget = 480

    def _load(self):
        if self._model is not None:
            return
        try:
            from fastembed import TextEmbedding
            from tokenizers import Tokenizer
            supported = {m['model']: m for m in TextEmbedding.list_supported_models()}
            description = supported.get(self.config.embedding_model)
            if description is None or description['dim'] != self.config.embedding_dimensions:
                raise ProviderError("Проверьте EMBEDDING_MODEL и EMBEDDING_DIMENSIONS: модель или размер вектора не поддерживается.")
            # Chunk tokenizer integration is validated for this specific FastEmbed model.
            if self.config.embedding_model != 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2':
                raise ProviderError("Этот адаптер использует paraphrase-multilingual-MiniLM-L12-v2. Для другой модели нужен отдельный адаптер разбиения.")
            model = TextEmbedding(model_name=self.config.embedding_model, cache_dir=self.config.embedding_cache_dir,
                                  threads=self.config.embedding_threads, providers=['CPUExecutionProvider'])
            # FastEmbed 0.7.4 exposes the underlying ONNX tokenizer. Clone it to avoid
            # modifying the embedding model's own truncation/padding configuration.
            tokenizer = Tokenizer.from_str(model.model.tokenizer.to_str())
            tokenizer.no_truncation()
            tokenizer.no_padding()
            self._tokenizer = tokenizer
            self._model = model
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("Не удалось загрузить локальную модель эмбеддингов. При первой загрузке нужны интернет и доступ на запись к каталогу моделей.") from exc

    def prepare_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        with self._lock:
            self._load()
            output = []
            for chunk in chunks:
                encoding = self._tokenizer.encode(chunk.text, add_special_tokens=False)
                if len(encoding.ids) <= self.token_budget:
                    output.append(Chunk(chunk.text, chunk.page, len(output) + 1))
                    continue
                # Keep exact original text, never reconstruct it via tokenizer.decode().
                for start in range(0, len(encoding.ids), self.token_budget - 60):
                    stop = min(start + self.token_budget, len(encoding.ids))
                    begin_char = encoding.offsets[start][0]
                    end_char = encoding.offsets[stop - 1][1]
                    text = chunk.text[begin_char:end_char].strip()
                    if text:
                        output.append(Chunk(text, chunk.page, len(output) + 1))
                    if stop == len(encoding.ids):
                        break
            if not output:
                raise DocumentError("Не удалось выделить текстовые фрагменты.")
            return output

    def embeddings(self, texts: list[str], query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            self._load()
            if any(len(self._tokenizer.encode(t, add_special_tokens=False).ids) > self.token_budget for t in texts):
                raise DocumentError("Текст слишком длинный для эмбеддинга. Сократите вопрос; документы должны проходить prepare_chunks.")
            try:
                iterator = self._model.query_embed(texts, batch_size=16) if query else self._model.passage_embed(texts, batch_size=16)
                vectors = [v.tolist() for v in iterator]
                if len(vectors) != len(texts) or any(len(v) != self.config.embedding_dimensions or not all(math.isfinite(x) for x in v) for v in vectors):
                    raise ValueError("Invalid embedding output")
                return vectors
            except Exception as exc:
                raise ProviderError("Не удалось вычислить локальные эмбеддинги.") from exc
