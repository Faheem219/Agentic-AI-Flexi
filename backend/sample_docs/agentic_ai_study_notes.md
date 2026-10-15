# Agentic AI and Workflow Automation: Study Notes

These notes summarise the core ideas of practical agentic AI: workflow automation, AI agents, retrieval-augmented generation (RAG), cache-augmented generation (CAG) and reliability engineering. They are used as a sample document for the Veritas knowledge base.

## 1. Automations versus AI agents

An automation is a fixed sequence of steps triggered by an event, for example "when a new e-mail arrives, extract the invoice PDF, parse the totals and add a row to Airtable". Workflow tools such as n8n represent these steps as nodes connected on a canvas: triggers (Gmail, webhooks, schedules), actions (HTTP requests, database writes), data transformation (cleaning and formatting) and control flow (IF conditions, loops over items). Automations are predictable and cheap, but they cannot handle situations that were not anticipated by the designer.

An AI agent adds a large language model (LLM) that decides what to do next. A plain LLM only maps a prompt to text. An agent wraps the LLM in a loop: it reasons about the goal, chooses a tool (for example "create calendar event" or "search documents"), observes the tool's result and repeats until the task is complete. The agent therefore combines four components: a model (the reasoning engine), memory (conversation history or stored knowledge), tools (APIs the agent may call) and instructions (the system prompt that defines role, constraints and output format).

A useful rule of thumb is to use deterministic workflows wherever the path is known, and agents only where flexible decision-making is genuinely required. Many production systems are hybrids: a workflow handles triggers, retries and data plumbing, and an agent node handles the ambiguous step.

## 2. Prompting an agent

A robust agent prompt is structured into sections. The role states who the agent is ("You are a calendar assistant for a small clinic"). Constraints list what the agent must never do and the business rules it must follow (time zones, working hours, confirmation before deleting an event). Tools describe when each tool should be used. Instructions describe the step-by-step procedure. The input/output section defines exactly what the agent receives and the format it must return, which is essential when the output is consumed by another program. Consistent, machine-readable outputs can be enforced with structured output (JSON schemas) through providers such as OpenRouter or the model's native API.

Prompts are iterated empirically: run the agent on realistic requests, inspect failures (for example events created in the wrong time zone), adjust the constraints and test again.

## 3. Retrieval-augmented generation (RAG)

RAG grounds an LLM's answer in an external knowledge base instead of relying on what the model memorised during training. It has two phases.

In the ingestion phase, documents are loaded, split into chunks (typically a few hundred tokens with some overlap so that facts spanning a boundary are not lost), converted into vectors with an embedding model, and stored in a vector database together with metadata such as the document name and page.

In the retrieval phase, the user's question is embedded with the same model, the most similar chunks are retrieved by cosine similarity, and those chunks are inserted into the prompt so the LLM can answer from them and cite them.

Pure vector search has known limitations: it can miss exact keywords, product codes and rare names. Hybrid retrieval combines dense vector search with sparse keyword search such as BM25, and the two rankings can be merged with reciprocal rank fusion. Contextual retrieval further improves recall by prepending a short description of where a chunk comes from (document title, section) before embedding it, so that an isolated chunk such as "revenue grew 3%" still carries its context.

Keeping a RAG system up to date requires an update strategy: detect new or changed documents, delete the stale chunks of a modified document and re-ingest only what changed, rather than rebuilding the whole index.

## 4. Cache-augmented generation (CAG)

Cache-augmented generation is an alternative to RAG for small, stable knowledge bases. Instead of retrieving chunks at query time, the entire knowledge base is loaded into the model's context window once and reused (with provider-side prompt caching where available) for every question. CAG removes retrieval errors and latency and is simpler to build, but it only works when the corpus fits in the context window, and cost grows with the size of the cached context. RAG remains the better choice for large or frequently changing corpora.

## 5. Reliability: error handling for agents and workflows

Production agentic systems fail in predictable ways: APIs time out, rate limits are hit, models return malformed output and third-party services change. Three layers of failure handling are recommended. First, node-level retries with back-off handle transient errors such as HTTP 429 or 503 responses. Second, workflow-level fallbacks route around a failing component, for example switching to a different model or a secondary data source. Third, a global error handler records the failure, alerts a human and stores enough context to replay the job. Naming conventions, clear workflow structure and automated backups make such systems maintainable.

## 6. Choosing a model

There is no single best model. Small, fast models are well suited to high-volume steps such as classification and extraction; larger reasoning models are better for planning, writing and critique. Selecting the model per step, and having a fallback when a provider is unavailable, balances quality, latency and cost.
