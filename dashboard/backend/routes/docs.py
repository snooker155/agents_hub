"""
The documentation corpus over HTTP, for the dashboard's Docs page.

The page is written in JSX, but a document that changes with every release
(the changelog) is served from the corpus the agents read (tools/docs_tool.py,
docs/index.json), so the page and ``read_doc`` show the same text and nothing
has to be copied by hand.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from tools.docs_tool import read_doc

router = APIRouter(prefix="/api/docs", tags=["docs"])


@router.get("/{doc_id}")
def get_doc(doc_id: str, lang: str = "en"):
    result = json.loads(read_doc.invoke({"doc_id": doc_id, "lang": lang}))
    if not result.get("ok"):
        status = 404 if result.get("code") == "not_found" else 500
        raise HTTPException(status_code=status, detail=result.get("error"))
    result.pop("ok", None)
    return result
