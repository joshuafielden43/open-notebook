# Citations - Trace AI Responses to Sources

Citations connect chat answers to materials in your notebooks. This guide describes what the app does today.

---

## What you get today

When models follow the citation convention, answers can include markers such as:

```
[source:source:abc123]
[note:note:xyz789]
```

In the UI those markers become **clickable links that open the whole source or note**. That is enough to jump to the document and verify claims yourself.

Open Notebook does **not** yet:

- Auto-highlight a specific passage or page inside the source
- Guarantee every sentence has a citation
- Show a numbered bibliography with section/page for every claim

The README and product comparison treat citation depth as basic and improving — not full passage-level proof like some cloud research products.

---

## How to use them

1. Ask a question in notebook chat (or source chat) with relevant sources in context.
2. If the answer includes source markers, click them to open that source.
3. Read the source to verify the claim. For tighter quotes, ask the model explicitly:

```
Quote the exact sentence that supports that claim and cite the source.
Which source says X? Include the surrounding paragraph.
```

---

## Improving citation quality

- Put the right sources in context (insights or full content as needed)
- Prefer models that follow instructions reliably
- Ask for quotes when you need verification, not just a link

---

## Related

- [Chat effectively](chat-effectively.md)
- [AI context and RAG](../2-CORE-CONCEPTS/ai-context-rag.md)
