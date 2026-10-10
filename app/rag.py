from app.documents import normalize

NO_INFORMATION = "В загруженных документах нет этой информации"


def build_response(result: dict, hits: list[dict]) -> dict:

    empty = {"answer": NO_INFORMATION, "sources": [], "found": False}
    claims = result.get("claims")
    if result.get("found") is not True or not isinstance(claims, list) or not claims or len(claims) > 8:
        return empty
    source_map = {i + 1: hit["payload"] for i, hit in enumerate(hits)}
    sources = {}
    paragraphs = []
    for claim in claims:
        if not isinstance(claim, dict):
            return empty
        source_id = claim.get("source_id")
        if type(source_id) is not int or source_id not in source_map:
            return empty
        text, quote = claim.get("text"), claim.get("quote")
        if not isinstance(text, str) or not isinstance(quote, str):
            return empty
        text, quote = text.strip(), quote.strip()
        payload = source_map[source_id]
        if not text or len(text) > 1600 or len(normalize(quote)) < 8 or normalize(quote) not in normalize(payload["text"]):
            return empty
        if source_id not in sources:
            sources[source_id] = {
                "id": source_id, "document_id": payload["document_id"], "name": payload["name"],
                "page": payload.get("page"), "chunk": payload["chunk"], "url": payload.get("url"),
                "quotes": [],
            }
        if quote not in sources[source_id]["quotes"]:
            sources[source_id]["quotes"].append(quote)
        paragraphs.append(f"{text} [{source_id}]")
    return {"answer": "\n\n".join(paragraphs), "sources": list(sources.values()), "found": True}
