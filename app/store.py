import uuid
import requests
from app.config import Settings
from app.providers import ProviderError


class VectorStore:
    def __init__(self, config: Settings):
        self.config = config
        self.root = f"/collections/{config.collection}"

    def request(self, method: str, path: str, body=None, allow_missing=False):
        headers = {"api-key": self.config.qdrant_api_key} if self.config.qdrant_api_key else {}
        try:
            response = requests.request(method, self.config.qdrant_url + path, json=body, headers=headers, timeout=(5, 60))
            if allow_missing and response.status_code == 404:
                return None
            if not response.ok:
                raise ProviderError(f"Ошибка векторной базы (HTTP {response.status_code}).")
            return response.json().get("result")
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError("Векторная база недоступна. Проверьте запуск Qdrant.") from exc

    def ensure_collection(self):
        existing = self.request("GET", self.root, allow_missing=True)
        if existing is None:
            self.request("PUT", self.root, {
                "sparse_vectors": {"bm25": {"index": {"on_disk": True}, "modifier": "idf"}}
            })
        else:
            sparse = existing["config"]["params"].get("sparse_vectors") or {}
            if "bm25" not in sparse or sparse["bm25"].get("modifier") != "idf":
                raise ProviderError("Коллекция несовместима с BM25. Задайте QDRANT_COLLECTION=documents_bm25_v1 и загрузите документы заново.")

        for field in ("workspace", "document_id"):
            self.request("PUT", self.root + "/index?wait=true", {"field_name": field, "field_schema": "keyword"})

    @staticmethod
    def scope(workspace: str, doc_id: str | None = None):
        must = [{"key": "workspace", "match": {"value": workspace}}]
        if doc_id:
            must.append({"key": "document_id", "match": {"value": doc_id}})
        return {"must": must}

    def add(self, workspace: str, document: dict, chunks: list, vectors: list):
        if len(chunks) != len(vectors):
            raise ProviderError("Количество чанков и эмбеддингов не совпадает.")
        points = [{
            "id": str(uuid.uuid4()), "vector": {"bm25": vector},
            "payload": {**document, "workspace": workspace, "page": chunk.page, "chunk": chunk.number, "text": chunk.text},
        } for chunk, vector in zip(chunks, vectors)]

        self.request("PUT", self.root + "/points?wait=true", {"points": points})

    def documents(self, workspace: str):
        docs = {}
        offset = None
        while True:
            body = {"filter": self.scope(workspace), "limit": 256, "with_payload": ["document_id", "name", "url", "kind", "created_at", "chunk_count"], "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            result = self.request("POST", self.root + "/points/scroll", body)
            for point in result["points"]:
                item = point["payload"]
                docs[item["document_id"]] = item
            offset = result.get("next_page_offset")
            if offset is None:
                break
        return sorted(docs.values(), key=lambda d: d["created_at"], reverse=True)

    def search(self, workspace: str, vector: dict):
        result = self.request("POST", self.root + "/points/query", {
            "query": vector, "using": "bm25", "filter": self.scope(workspace), "limit": self.config.top_k,
            "score_threshold": self.config.min_score, "with_payload": True, "with_vector": False,
        })
        return result["points"]

    def clear(self, workspace: str, doc_id: str | None = None):
        self.request("POST", self.root + "/points/delete?wait=true", {"filter": self.scope(workspace, doc_id)})
