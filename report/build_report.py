"""Builds report/CA3_Agentic_AI_Report.docx from the college template.

The original template (CA3_Agentic_AI_Blank_Template_v2.docx) is never modified: this script
reads it, replaces the body of word/document.xml with the report content (keeping the
template's styles, footer, page set-up and section structure), adds figures and list
definitions, and writes a new .docx. Numbers in the evaluation sections are read from
backend/evals/results/latest.json so the report always matches the latest benchmark.

    python3 report/build_report.py
"""

from __future__ import annotations

import json
import re
import struct
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "CA3_Agentic_AI_Blank_Template_v2.docx"
OUT = ROOT / "report" / "CA3_Agentic_AI_Report.docx"
FIG = ROOT / "report" / "figures"
EVAL = json.loads((ROOT / "backend" / "evals" / "results" / "latest.json").read_text())
SAMPLE = json.loads((ROOT / "report" / "evidence" / "sample_run.json").read_text())
PYTEST = (ROOT / "report" / "evidence" / "pytest_output.txt").read_text()

TEXT_WIDTH = 9936  # dxa: 12240 page - 2 x 1152 margins
BULLET, NUM_ABS = 50, 51
_num_counter = [60]
_media: list[tuple[str, bytes, str]] = []  # (rid, data, ext)
_docpr = [100]
_fig = [0]
_tab = [0]


# ------------------------------------------------------------------ XML helpers
def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def run(text: str, b=False, i=False, mono=False, sz=None, color=None, hl=None) -> str:
    props = []
    if mono:
        props.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>')
    if b:
        props.append("<w:b/><w:bCs/>")
    if i:
        props.append("<w:i/><w:iCs/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if sz:
        props.append(f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>')
    if hl:
        props.append(f'<w:highlight w:val="{hl}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{esc(text)}</w:t></w:r>'


TOKEN = re.compile(r"(\*\*.+?\*\*|`.+?`|\[\[.+?\]\]|__.+?__)")


def rich(text: str, sz=None, b=False, color=None) -> str:
    """Inline markup: **bold**, __italic__, `code`, [[placeholder]] (yellow highlight)."""
    out = []
    for part in TOKEN.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append(run(part[2:-2], b=True, sz=sz, color=color))
        elif part.startswith("__") and part.endswith("__"):
            out.append(run(part[2:-2], i=True, b=b, sz=sz, color=color))
        elif part.startswith("`") and part.endswith("`"):
            out.append(run(part[1:-1], mono=True, sz=(sz or 21) - 2, b=b, color=color))
        elif part.startswith("[[") and part.endswith("]]"):
            out.append(run(part[2:-2], b=b, sz=sz, hl="yellow", color=color))
        else:
            out.append(run(part, b=b, sz=sz, color=color))
    return "".join(out)


def para(inner: str, style=None, jc=None, before=None, after=None, keep_next=False, num=None, lvl=0,
         ind=None, page_break=False, line=None) -> str:
    ppr = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        ppr.append("<w:keepNext/>")
    if page_break:
        ppr.append("<w:pageBreakBefore/>")
    if num is not None:
        ppr.append(f'<w:numPr><w:ilvl w:val="{lvl}"/><w:numId w:val="{num}"/></w:numPr>')
    if before is not None or after is not None or line is not None:
        attrs = ""
        if before is not None:
            attrs += f' w:before="{before}"'
        if after is not None:
            attrs += f' w:after="{after}"'
        if line is not None:
            attrs += f' w:line="{line}" w:lineRule="auto"'
        ppr.append(f"<w:spacing{attrs}/>")
    if ind is not None:
        ppr.append(f'<w:ind w:left="{ind}"/>')
    if jc:
        ppr.append(f'<w:jc w:val="{jc}"/>')
    p = f"<w:pPr>{''.join(ppr)}</w:pPr>" if ppr else ""
    return f"<w:p>{p}{inner}</w:p>"


def P(text: str, **kw) -> str:
    return para(rich(text), after=kw.pop("after", 140), **kw)


def H1(text: str, page_break=False) -> str:
    return para(run(text), style="Heading1", page_break=page_break)


def H2(text: str) -> str:
    return para(run(text), style="Heading2", after=80)


def H3(text: str) -> str:
    return para(run(text), style="Heading3", after=60)


def B(text: str, lvl=0) -> str:
    return para(rich(text), style="ListParagraph", num=BULLET, lvl=lvl, after=60)


def bullets(items: list[str]) -> str:
    return "".join(B(t) for t in items)


def numbered(items: list[str]) -> str:
    num_id = _num_counter[0]
    _num_counter[0] += 1
    return "".join(para(rich(t), style="ListParagraph", num=num_id, after=90) for t in items)


def field(label: str, value: str) -> str:
    return para(run(f"{label}: ", b=True) + rich(value), after=80)


def cell(text: str, width: int, fill=None, sz=18, bold=False) -> str:
    shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    paras = "".join(
        para(rich(line, sz=sz, b=bold), after=0, line=252) for line in str(text).split("\n"))
    return f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shd}</w:tcPr>{paras}</w:tc>'


def table(header: list[str], rows: list[list[str]], widths: list[float], caption: str | None = None, sz=18) -> str:
    total = TEXT_WIDTH
    ws = [int(total * w / sum(widths)) for w in widths]
    ws[-1] = total - sum(ws[:-1])
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="A6B4C8"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    tbl = [
        '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
        f'<w:tblW w:w="{total}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
        '<w:tblLayout w:type="fixed"/>'
        '<w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="90" w:type="dxa"/>'
        '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar>'
        '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
        "</w:tblPr>",
        "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in ws) + "</w:tblGrid>",
        '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(cell(h, w, "DBE5F1", sz, True) for h, w in zip(header, ws)) + "</w:tr>",
    ]
    for row in rows:
        tbl.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(cell(c, w, None, sz) for c, w in zip(row, ws)) + "</w:tr>")
    tbl.append("</w:tbl>")
    out = ""
    if caption:
        _tab[0] += 1
        out += para(run(f"Table {_tab[0]}: {caption}"), style="Caption", keep_next=True, before=120, after=60)
    return out + "".join(tbl) + para("", after=120)


def code(lines: list[str], caption: str | None = None) -> str:
    paras = "".join(para(run(l if l else " ", mono=True, sz=16), after=0, line=240) for l in lines)
    tbl = (
        '<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="C5D3EA"/><w:left w:val="single" w:sz="4" w:space="0" w:color="C5D3EA"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="C5D3EA"/><w:right w:val="single" w:sz="4" w:space="0" w:color="C5D3EA"/>'
        '</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:left w:w="140" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:right w:w="140" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="%d"/></w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr><w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
        '<w:shd w:val="clear" w:color="auto" w:fill="F3F5F8"/></w:tcPr>%s</w:tc></w:tr></w:tbl>'
    ) % (TEXT_WIDTH, TEXT_WIDTH, TEXT_WIDTH, paras)
    out = ""
    if caption:
        out += para(run(caption), style="Caption", keep_next=True, before=120, after=60)
    return out + tbl + para("", after=120)


def _img_size(data: bytes) -> tuple[int, int]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    i = 2  # JPEG: walk segments to a SOF marker
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return w, h
        i += 2 + seg_len
    raise ValueError("unknown image size")


def figure(path: Path, caption: str, width_in: float = 6.5, border=True, max_h_in: float = 8.4) -> str:
    data = path.read_bytes()
    w, h = _img_size(data)
    ext = "png" if data[:4] == b"\x89PNG" else "jpeg"
    rid = f"rIdImg{len(_media) + 1}"
    _media.append((rid, data, ext))
    cx = int(width_in * 914400)
    cy = int(cx * h / w)
    max_h = int(max_h_in * 914400)
    if cy > max_h:
        cx, cy = int(cx * max_h / cy), max_h
    _docpr[0] += 1
    pid = _docpr[0]
    ln = '<a:ln w="6350"><a:solidFill><a:srgbClr val="BFC8D6"/></a:solidFill></a:ln>' if border else ""
    drawing = (
        f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{pid}" name="Figure {pid}" descr="{esc(caption)}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{pid}" name="{path.name}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{ln}</pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )
    _fig[0] += 1
    return (para(drawing, jc="center", keep_next=True, before=120, after=40)
            + para(run(f"Figure {_fig[0]}: {caption}"), style="Caption", jc="center", after=200))


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


# ------------------------------------------------------------------ evaluation numbers
S = EVAL["summary"]
V, BL = S["veritas"], S["baseline"]
QS = EVAL["questions"]


def _pool(system: str, key: str) -> int:
    return sum(q[f"{system}_verification"][key] for q in QS)


VT, VV = _pool("veritas", "total"), _pool("veritas", "verified")
BT, BV, BN, BP = (_pool("baseline", k) for k in ("total", "verified", "not_found", "partial"))
B_DOI_G, B_DOI_C = _pool("baseline", "dois_given"), _pool("baseline", "doi_correct")
V_DOI_G, V_DOI_C = _pool("veritas", "dois_given"), _pool("veritas", "doi_correct")
pc = lambda a, b: f"{round(100 * a / b)}%" if b else "n/a"  # noqa: E731
judge_avg = lambda j: round(sum(j.values()) / len(j), 2)  # noqa: E731
GR, SC = EVAL["guardrails"], EVAL["scope"]
N_TESTS = int(re.search(r"(\d+) passed", PYTEST).group(1))
SM = SAMPLE["metrics"]


# ------------------------------------------------------------------ content
def build_body() -> str:
    x: list[str] = []
    a = x.append

    # ---------------- cover (template fields)
    a(para(run("CA3 – AGENTIC AI", b=True, sz=40), jc="center"))
    a(field("Course / Subject", "Agentic AI"))
    a(field("Course Title", "AI Agents & AI Automation – The Practical Agentic AI Guide (Udemy) [[and the further agentic-AI MOOCs listed in Section 3 – to be added]]"))
    a(field("Course Duration (minimum 45 hours)", "8 hours certified so far (8 h 12 min of video) [[+ remaining courses, total ≥ 45 h – to be updated]]"))
    a(field("Faculty", "[[____________________]]"))
    a(field("Division", "[[____________________]]"))
    a(field("Student", "Faheemuddin Sayyed      PRN: [[____________________]]"))
    a(field("Project", "Veritas – a multi-agent research assistant with verifiable citations"))
    a(para("", after=120))
    a(para(run("Contents", b=True, sz=24, color="365F91"), after=80))
    for line in [
        "1. Introduction to Agentic AI", "2. Understanding of Agentic AI", "3. Courses Completed",
        "4. Problem Statement", "5. Proposed Solution (incl. Agent Workflow and Agent/Tool Roles)",
        "6. Implementation", "7. Final Project Demonstration (incl. Testing and Evidence)",
        "8. Conclusion (incl. Individual Reflection)", "References", "Appendix A–C: Certificates, test output, sample brief",
    ]:
        a(para(run(line, sz=20), after=20, ind=240))

    # ---------------- 1. Introduction
    a(H1("1. Introduction to Agentic AI", page_break=True))
    a(H2("Objective"))
    a(P("This report documents what I learnt in the Agentic AI course through MOOCs, and how I applied that learning to design, build, test and deploy a working agentic AI system. It has three objectives:"))
    a(numbered([
        "**Learn:** summarise the concepts of agentic AI covered by the completed courses: AI agents and how they differ from plain LLM calls and fixed workflows, tool use, prompting, memory, retrieval-augmented generation (RAG), cache-augmented generation (CAG), multi-agent orchestration and reliability engineering.",
        "**Apply:** use those concepts to solve a specific, real problem. The project, **Veritas**, is a team of AI agents that researches a question in scholarly databases and writes a brief in which every claim is tied to a publication that was actually retrieved.",
        "**Evaluate:** measure the system against explicit success criteria with automated tests, a benchmark against a single-LLM baseline and red-team tests, and reflect on the design choices and the behaviour observed.",
    ]))
    a(H2("Key Concepts Understood"))
    a(P("Table 1 lists the core concepts I understood and where each one is applied in the project. Concepts marked “C1” come from the first completed course (Section 3). The others come from the project work and the further courses, which will be expanded as those courses are completed."))
    a(table(
        ["Concept", "What I understood", "Where it is used in Veritas"],
        [
            ["Automation vs. AI agent (C1)", "An automation follows a fixed, pre-designed path (trigger → actions). An agent puts an LLM in a loop that decides the next step, calls tools and observes results until the goal is met.", "Deterministic code for guardrails, verification and publishing; LLM agents only where judgement is needed (planning, researching, writing, critique)."],
            ["Agent components (C1)", "Model + instructions + tools + memory. The model reasons, tools act on the world, memory carries context.", "Each agent role has its own prompt, tool set and model tier (Section 5)."],
            ["Structured prompting (C1)", "Prompts work best when organised into Role, Constraints, Tools, Instructions and Input/Output format, and are iterated on failures.", "Every agent prompt in `prompts.py` uses this structure."],
            ["Consistent / structured outputs (C1)", "Machine-consumed outputs must follow a schema (the course used OpenRouter structured outputs).", "Planner, researcher and critic return JSON validated against JSON schemas."],
            ["Tool use and APIs (C1)", "Agents act through APIs: read the documentation, send HTTP requests, handle responses and errors.", "Researchers call OpenAlex, arXiv, Wikipedia and Crossref through function calling."],
            ["Memory (C1)", "Short-term conversation state vs. long-term stored knowledge.", "Graph state (short-term) and episodic memory of past runs recalled by embeddings (long-term)."],
            ["RAG pipeline (C1)", "Ingestion (load, chunk, embed, store) and retrieval (embed query, similarity search, ground the answer); hybrid and contextual retrieval fix pure-vector weaknesses; indexes must be updated when documents change.", "“Your library”: page-aware chunking, title-prefixed chunks, BM25 + embeddings fused by reciprocal rank, index refreshed on change."],
            ["CAG (C1)", "For a small, stable corpus, load everything into the context window instead of retrieving.", "Follow-up chat (“Ask the brief”) loads the whole brief and its abstracts into context."],
            ["Reliability and error handling (C1)", "Retries for transient errors, fallbacks around failing components, and a global error handler: three layers of failure handling.", "Model fallback chain, per-model cool-down, retries, Crossref fallback, graceful degradation of failed researchers, run-level error capture."],
            ["Choosing the model (C1)", "No single best model: small fast models for volume, stronger ones for reasoning.", "Fast tier for researchers, smart tier for planner/writer/critic."],
            ["Multi-agent orchestration", "Workflows of specialised agents with routing, parallelisation and evaluator–optimizer (reflection) loops.", "LangGraph state machine with fan-out of researchers and a writer–critic loop."],
            ["Model Context Protocol (MCP)", "An open standard that separates tools from the agent: a server exposes tools, resources and prompts; any client can discover and call them.", "All research tools live in an MCP server; the agents use an MCP client."],
            ["Human-in-the-loop, guardrails, evaluation", "Agents need oversight, input/output safety checks and measurable evaluation.", "Plan review (interrupt/resume), injection and PII guardrails, a benchmark and red-team suite."],
        ],
        [1.25, 2.5, 2.4], caption="Key concepts and their application in the project"))

    # ---------------- 2. Understanding
    a(H1("2. Understanding of Agentic AI"))
    a(H2("What is an AI Agent?"))
    a(P("An **AI agent** is a system in which a large language model (LLM) directs its own process to achieve a goal: it decides which step to take next, uses tools to act on or observe the outside world, and continues until the task is done or a stopping condition is met. A plain LLM call maps one prompt to one response. It cannot look anything up, cannot check its own output and only knows what was in its training data. An agent wraps the model in a loop of **reason → act (tool call) → observe (tool result) → reason again**. This pattern was formalised as ReAct (Yao et al., 2023)."))
    a(P("Every agent built in the course and in this project has the same building blocks:"))
    a(bullets([
        "**Model:** the reasoning engine (here Google Gemini). It decides, plans and writes.",
        "**Instructions:** a system prompt defining the agent's role, constraints, available tools, procedure and output format.",
        "**Tools:** functions the agent may call (APIs, databases, search). The model emits a structured function call, the application executes it and returns the result.",
        "**Memory:** the working state of the current task plus longer-term knowledge (stored documents, past interactions).",
        "**Control loop / orchestration:** the code that runs the loop, enforces limits (maximum steps, budgets) and connects several agents.",
    ]))
    a(P("Agentic systems lie on a spectrum of autonomy. Table 2 compares the three points I worked with: the n8n automations and the Calendar agent in Course 1, and the multi-agent system built for this project."))
    a(table(
        ["", "Single LLM call", "Workflow (automation)", "Agent / multi-agent system"],
        [
            ["Who decides the steps", "Nobody: one prompt, one answer", "The designer, in advance", "The model, at run time (within limits)"],
            ["Access to external data", "None (training data only)", "Fixed integrations", "Chooses tools and queries dynamically"],
            ["Handles the unexpected", "No", "Only anticipated branches", "Yes: re-plans, retries, refines"],
            ["Predictability / cost", "High / very low", "High / low", "Lower / higher (needs limits)"],
            ["Example", "Ask a chatbot for references", "Gmail → PDF parse → Airtable (Course 1)", "Calendar agent (Course 1); Veritas"],
        ],
        [1.3, 1.4, 1.6, 1.9], caption="Single LLM call vs. workflow vs. agent"))
    a(P("The most useful lesson was that **an agent should not be used where a workflow is enough**. Reliable agentic systems combine both: deterministic code wherever the path is known, and model-driven decisions only where flexibility is genuinely needed. Veritas follows this rule. Its guardrail, citation verifier and reference builder are plain code, and only planning, researching, writing and critiquing are delegated to LLM agents."))
    a(H3("Agentic design patterns"))
    a(bullets([
        "**Prompt chaining:** the output of one step feeds the next (planner → researchers → writer).",
        "**Routing:** a model classifies the input and sends it to the right handler (the planner routes each sub-question to the right databases).",
        "**Parallelisation:** independent sub-tasks run concurrently (researchers fan out, one per sub-question).",
        "**Orchestrator–workers / plan-and-execute:** a planner decomposes the task and workers execute the parts.",
        "**Evaluator–optimizer (reflection):** a critic evaluates the output and the producer revises it (writer ↔ critic loop; compare Self-Refine, Madaan et al., 2023).",
        "**Human-in-the-loop:** the system pauses at a checkpoint for human approval (plan review).",
    ]))
    a(H3("Risks of agents and how they are controlled"))
    a(table(
        ["Risk", "Why it happens", "Control used in Veritas"],
        [
            ["Hallucinated facts and references", "LLMs generate plausible text, not verified facts.", "Agents may cite only IDs of retrieved records; code removes unknown IDs; references are built from metadata."],
            ["Prompt injection (direct and indirect)", "Instructions hidden in user input or in tool results can hijack the model (Greshake et al., 2023).", "Input screening before any model call; tool output wrapped in delimiters and treated as data."],
            ["Runaway loops and cost", "Agents may keep calling tools or revising.", "Bounded tool rounds, bounded revisions, rate limits per client, concurrency limits."],
            ["Unreliable dependencies", "APIs time out, hit quotas or change.", "Fallback chains, cool-downs, retries, caches, graceful degradation."],
            ["Inconsistent judgement", "Model decisions vary between runs.", "Decision rules that must be consistent (accept or revise) are applied in code."],
            ["Privacy", "Users may paste personal data.", "E-mails, phone numbers and IDs are redacted before the model sees the question."],
        ],
        [1.4, 2.0, 2.8], caption="Risks of agentic systems and the controls implemented"))

    # ---------------- 3. Courses
    a(H1("3. Courses Completed (multiple courses as applicable)"))
    a(H2("Course Details"))
    a(para(run("Course Title: ", b=True) + rich("AI Agents & AI Automation – The Practical Agentic AI Guide"), style="ListParagraph", num=10, after=60))
    a(field("Platform / Provider", "Udemy (accessed through Infosys Springboard); instructors Nikolai Schuler and Simon Coton"))
    a(field("Course Duration (minimum 45 hours)", "8 hours as stated on the certificate (8 h 12 min of video, 91 lectures in 6 sections)"))
    a(field("Completion Evidence", "Udemy Certificate of Completion no. UC-f4f4fa13-b3b1-4c37-9d2d-80a375f3e7eb, dated 11 Sept 2026, verifiable at ude.my/UC-f4f4fa13-b3b1-4c37-9d2d-80a375f3e7eb (Appendix A)"))
    a(P("**What the course covered and how I applied it.** I grouped the 91 lectures into six themes, in course order:", after=80))
    a(table(
        ["Theme", "Lectures covered", "Applied in Veritas"],
        [
            ["Workflow automation foundations", "What an automation is; n8n cloud set-up and self-hosting; creating workflows; key categories of workflow actions; Airtable; connecting and retrieving data; data formats; static vs. dynamic data; retry logic; naming consistency; workflow planning.", "Deterministic steps are kept as code; retry logic and consistent naming in the tool layer."],
            ["E-mail and document pipeline", "Gmail trigger and credentials; HTML vs. Markdown; sending e-mail data to Airtable; handling multiple data formats; extracting data from PDFs; cleaning and formatting; conditional logic; looping over items.", "PDF/Markdown ingestion and cleaning for the library; conditional routing edges in the graph."],
            ["AI agents in workflows", "AI agent basics; AI agents vs. LLMs; agents and agent nodes in n8n; agentic applications for business; prompting; OpenRouter and consistent outputs; handling data types and errors.", "Agent roles with structured prompts and JSON-schema outputs."],
            ["APIs, scale and reliability", "API and HTTP basics; reading API documentation; uploading files to APIs; processing responses; scalable and clear workflows; error-handling strategies; automated backups; an error handler; three layers of failure handling.", "OpenAlex/arXiv/Wikipedia/Crossref clients; layered failure handling (retries, model and tool fallbacks, run-level error capture)."],
            ["Case study: Calendar agent", "Planning an agent; choosing the agent type, LLM and memory; tools and Google OAuth; create/update/delete/get events; checking availability; prompt critique; role, constraints, tools, instructions and I/O; first run and fixes; time zones; prompt iteration.", "The same plan → build → run → fix → iterate method drove the prompt design of each Veritas agent."],
            ["RAG and CAG", "RAG for business; data management; chunking and splitting; vectors and embeddings; ingestion and retrieval phases; retrieval limitations; contextual retrieval; updating data and handling new documents; cache-augmented generation; choosing the best model.", "Hybrid RAG over the user's library (contextual chunk headers, index refresh on change) and CAG for follow-up questions."],
        ],
        [1.3, 3.0, 2.0], caption="Course 1 content and its application"))
    a(para(run("Course Title: ", b=True) + rich("[[Further agentic-AI courses – details to be added on completion]]"), style="ListParagraph", num=10, after=60))
    a(field("Platform / Provider", "[[__________]]"))
    a(field("Course Duration (minimum 45 hours)", "[[__________]]"))
    a(field("Completion Evidence", "[[__________]]"))
    a(table(
        ["#", "Course", "Provider", "Hours", "Status"],
        [
            ["1", "AI Agents & AI Automation – The Practical Agentic AI Guide", "Udemy (via Infosys Springboard)", "8", "Completed, certified"],
            ["2+", "[[Remaining courses to be added]]", "[[–]]", "[[≈ 37]]", "[[In progress]]"],
            ["", "**Total**", "", "[[≥ 45]]", ""],
        ],
        [0.3, 2.6, 1.6, 0.6, 1.1], caption="Course duration summary"))

    # ---------------- 4. Problem statement
    a(H1("4. Problem Statement"))
    a(H2("Problem Statement"))
    a(para(run("Problem Statement: ", b=True) + rich(
        "Students and early-career researchers increasingly start literature reviews by asking general-purpose LLM chatbots, but these chatbots produce references that do not exist or carry wrong details, and they give no way to check where a claim came from. Searching several scholarly databases by hand, reading abstracts and assembling a cited summary takes hours. **This project builds an agentic system that, for any research question, autonomously searches real scholarly sources and produces a structured research brief in which every claim is traceable to a publication that was actually retrieved, while staying transparent, open to human oversight and cheap enough to run on free-tier infrastructure.**"), after=140))
    a(H3("Why the problem is real"))
    a(bullets([
        "**Published evidence.** Walters and Wilder (2023) asked ChatGPT to write short literature reviews on 42 topics and checked all 636 citations: **55% of GPT-3.5 and 18% of GPT-4 citations were fabricated**, and many of the real ones contained substantive errors.",
        f"**Our own measurement in 2026.** The same check was repeated on a current model in this project's benchmark (Section 7). A single call to Gemini, asked for a literature review with references, produced {BT} references, of which only **{BV} ({pc(BV, BT)}) could be verified to exist** in Crossref/OpenAlex. **{BN} ({pc(BN, BT)}) were not found anywhere**, {BP} ({pc(BP, BT)}) matched a real paper only partially (wrong title or year), and only **{B_DOI_C} of {B_DOI_G} DOIs ({pc(B_DOI_C, B_DOI_G)})** pointed to the cited paper. Newer models are better, but still not trustworthy for scholarly work.",
        "**Cost to the user.** A fabricated reference is costly because it looks real. Every reference has to be checked by hand, which removes most of the time the chatbot saved.",
    ]))
    a(H3("Why an agentic solution"))
    a(P("A single LLM call cannot solve this: the model has no access to the databases, its knowledge stops at its training cut-off, and it cannot check its own citations. A fixed workflow is not enough either, because research questions vary widely in field and structure. Choosing what to search, where, and whether the results are sufficient needs judgement at run time. The problem therefore calls for agents that **plan, use tools, evaluate evidence and revise**, under deterministic safeguards that **guarantee** citation integrity rather than merely encouraging it."))
    a(H3("Scope, constraints and success criteria"))
    a(bullets([
        "**In scope:** research questions in any discipline; peer-reviewed metadata and abstracts (OpenAlex, Crossref), preprints (arXiv), encyclopaedic background (Wikipedia) and the user's own uploaded documents.",
        "**Out of scope:** reading paywalled full texts, systematic-review-grade screening, and medical, legal or financial advice.",
        "**Constraints:** free-tier Gemini API (rate and daily limits), free public APIs, a single developer, deployment on my own VM (backend) and Vercel (frontend).",
    ]))
    a(table(
        ["Success criterion", "Target", "Achieved (Section 7)"],
        [
            ["References that verifiably exist", "≥ 95%", f"{pc(VV, VT)} ({VV}/{VT})"],
            ["Substantive sentences that carry a citation", "≥ 75%", f"{round(V['avg_citation_coverage'] * 100)}% average"],
            ["Prompt-injection and invalid inputs blocked", "All test cases", f"{GR['passed']}/{GR['total']} guardrail cases, {SC['passed']}/{SC['total']} scope cases"],
            ["Time for a standard-depth brief", "< 2 minutes", f"{round(V['avg_latency_s'])} s average"],
            ["Runs on free tier", "≤ 15 model calls per run", f"{round(V['avg_llm_calls'], 1)} calls on average"],
            ["Human oversight and transparency", "Plan review + full trace", "Implemented (Figures 5, 12)"],
        ],
        [2.6, 1.4, 2.2], caption="Success criteria"))

    # ---------------- 5. Proposed solution
    a(H1("5. Proposed Solution"))
    a(H2("Project Title"))
    a(P("**Veritas: A Multi-Agent Research Assistant with Verifiable Citations**"))
    a(H2("Project Description"))
    a(P("Veritas is a web application in which a team of specialised AI agents researches a question the way a careful research assistant would. The user types a question and chooses the depth and the sources (OpenAlex, arXiv, Wikipedia and/or their own uploaded documents). They can ask to review the plan first. They then watch the agents work in real time: the planner splits the question into sub-questions, researcher agents search the databases in parallel, a writer drafts a brief, and a verifier and a critic check it and send it back for revision when needed. The result is a structured brief (executive summary, key findings, debates and limitations, gaps, conclusion) in which every citation opens a catalogue card with the source's real metadata and a link to it. Afterwards the user can ask follow-up questions about the brief."))
    a(P("The central design principle is that **the language model is never allowed to write a reference**. Citation integrity is enforced by three independent mechanisms:"))
    a(numbered([
        "**Registry.** Every record returned by a tool is registered with a stable ID (S1, S2, ...), and agents may only cite these IDs.",
        "**Code verification.** A deterministic verifier removes any cited ID that was never retrieved, measures how many sentences carry a citation, and flags sentences their cited source does not appear to support.",
        "**Metadata references.** The reference list is assembled by code from the metadata the databases returned (title, authors, year, venue, DOI/URL), so it cannot contain an invented paper.",
    ]))
    a(P("Other features: optional **human review of the plan** (edit, add or remove sub-questions before anything is searched); a **private library** searched with hybrid RAG; **episodic memory** of earlier research; **guardrails** against prompt injection with PII redaction; a full **trace** of every step, model call and tool call, streamed live and exportable; and resilience to rate limits and API outages."))
    a(H2("Agent Workflow"))
    a(figure(FIG / "fig_workflow.png", "Veritas agent workflow (LangGraph state machine). Rounded red boxes are LLM agents, square boxes are deterministic code, and the dashed box is the optional human checkpoint.", 6.4, border=False))
    a(P("The workflow is implemented as a LangGraph state machine (`backend/app/agents/graph.py`). Each box in Figure 1 is a graph node that reads and updates a shared, typed state. The edges encode the control flow:"))
    a(numbered([
        "**Guardrail (code).** Validates length, screens for prompt-injection and jailbreak patterns, and redacts personal data. A blocked request exits immediately, __before any model call__.",
        "**Memory (embeddings).** Embeds the question and recalls up to two earlier runs with similarity ≥ 0.70, so the planner can build on previous work.",
        "**Planner (Gemini, smart tier).** Decides whether the request is in scope (out-of-scope or harmful requests exit with a reason), writes 2–4 non-overlapping sub-questions depending on depth, and **routes** each one to the 1–2 most suitable tools with 2 keyword queries. Output is JSON validated against a schema.",
        "**Plan review (human, optional).** If the user asked to review the plan, the graph **interrupts** and the run waits. The user can edit, add or remove sub-questions, change their sources, give guidance or reject the plan. The graph then resumes from its checkpoint.",
        "**Researchers (Gemini, fast tier, in parallel).** One researcher per sub-question is launched with LangGraph's `Send` API. Each first runs the planner's searches in parallel without a model call (plan-and-execute). It then reads the evidence and either reports findings or calls tools again to refine its search (ReAct), up to a depth-dependent limit. Findings are claims tied to source IDs, each with a confidence level and noted gaps. If a researcher fails, it reports the failure instead of crashing the run.",
        "**Writer (Gemini, smart tier).** Synthesises all findings into a structured brief, citing source IDs after each claim and using only the findings provided.",
        "**Verifier (code).** Normalises citations, removes invalid IDs, computes citation coverage and flags uncited and weakly supported sentences.",
        "**Critic (Gemini, smart tier).** Scores the draft from 1 to 10 on coverage, groundedness, clarity and balance, and lists specific fixes. **A code rule decides the outcome:** a score below 7, a high-severity issue or citation coverage below 75% sends the draft back to the writer with the critic's feedback and the verifier's report (reflection loop), up to the configured number of revisions.",
        "**Publisher (code).** Renumbers citations [1], [2]... in order of first use, builds the reference list from metadata, stores the run with its metrics, and saves a summary to episodic memory.",
    ]))
    a(H2("Agent and Tool Roles"))
    a(table(
        ["Component", "Type / model", "Input → Output", "Tools", "Why it is a separate role"],
        [
            ["Guardrail", "Code", "Question → sanitised question or rejection", "–", "Cheap, instant, auditable; no model budget spent on malicious input."],
            ["Memory", "Embedding model", "Question → related past runs", "SQLite vectors", "Reuses earlier work; independent of planning."],
            ["Planner", "Gemini smart", "Question (+ memories) → scoped plan (JSON)", "–", "Planning quality determines everything downstream, so it gets the stronger model and a schema."],
            ["Plan review", "Human", "Plan → approved / edited / rejected plan", "UI editor", "Oversight at the cheapest point, before any search."],
            ["Researcher ×N", "Gemini fast", "Sub-question → findings with source IDs", "MCP: OpenAlex, arXiv, Wikipedia, library", "Narrow context per sub-question keeps prompts small; runs in parallel."],
            ["Writer", "Gemini smart", "Findings + catalogue (+ critique) → cited draft", "–", "One voice and structure across sub-questions."],
            ["Verifier", "Code", "Draft → cleaned draft + coverage report", "–", "What can be checked exactly is checked by code."],
            ["Critic", "Gemini smart", "Draft + findings + report → scores and fixes", "–", "Separating producer and evaluator gives an honest, specific review."],
            ["Publisher", "Code", "Draft + sources → final brief, references, metrics", "SQLite", "Guarantees references come from metadata."],
        ],
        [1.0, 0.9, 1.6, 1.2, 1.9], caption="Agent roles", sz=17))
    a(table(
        ["MCP tool", "Data source", "Used for", "Why chosen"],
        [
            ["search_openalex", "OpenAlex (250M+ works); automatic Crossref fallback", "Peer-reviewed literature in any discipline, with citation counts and abstracts", "Free, open, multidisciplinary; the Crossref fallback keeps it working under quota limits."],
            ["search_arxiv", "arXiv API", "Recent preprints in CS, AI, physics and maths", "Newest technical work appears there first."],
            ["search_wikipedia", "Wikipedia search + summaries", "Definitions and background only", "Context for non-specialists; flagged as non-primary evidence."],
            ["search_knowledge_base", "User's uploaded PDFs/notes (hybrid RAG)", "Private sources cited by page", "Brings the user's own material into the analysis."],
        ],
        [1.3, 1.6, 1.7, 1.9], caption="Tools exposed by the MCP server"))
    a(H2("Design Decisions and Justification"))
    a(bullets([
        "**Multi-agent instead of one big agent.** Each role gets a short, focused prompt and only the tools it needs. Smaller contexts are cheaper and more reliable, and each step can be inspected and tested on its own.",
        "**Plan-and-execute first, ReAct second.** Researchers start by running the planner's queries without asking the model, and the model is consulted only to judge and refine. This cut the number of model calls to about 7–10 per run, which matters on a free tier with tight quotas.",
        "**Parallel researchers.** Sub-questions are independent, so they run concurrently. A test confirms three researchers finish in less than half the sequential time.",
        "**Two model tiers with a fallback chain.** Following the course's advice to choose the model per job, the stronger model plans, writes and critiques while a faster model handles the research loops. When a model is rate-limited or unavailable, the gateway moves to the next model automatically.",
        "**Code-based acceptance rule.** The critic once returned “revise” while all its scores were 8 or above. Moving the accept-or-revise decision into code made the reflection loop deterministic and explainable.",
        "**Tools behind MCP.** Separating tools from agents follows the Model Context Protocol standard (Anthropic, 2024a). The same server can be used by any MCP host (for example Claude Desktop), and tools can be tested on their own.",
        "**Human review at the plan stage.** Reviewing the plan costs one model call; reviewing a finished brief would waste the whole run.",
        "**RAG for the library, CAG for follow-ups.** This applies Course 1's RAG-versus-CAG lesson directly. The library can grow, so it uses retrieval. A single brief plus its abstracts is small and fixed, so follow-up questions load it entirely into context.",
    ]))

    # ---------------- 6. Implementation
    a(H1("6. Implementation"))
    a(H2("Technologies Used"))
    a(field("Programming Language", "Python 3.12 (backend, agents, MCP server, tests, evaluation); TypeScript (frontend)"))
    a(field("LLM / AI Model", "Google Gemini, free tier, via the google-genai SDK. Smart tier: gemini-3-flash-preview; fast tier: gemini-3.5-flash-lite; automatic fallbacks: gemini-3.1-flash-lite, gemini-3.8-flash, gemini-flash-lite-latest; embeddings: gemini-embedding-001 (768 dimensions)"))
    a(field("Agent Framework", "LangGraph 1.2 (StateGraph, conditional edges, Send fan-out, interrupt/Command, checkpointer); Model Context Protocol Python SDK 2.3 (MCPServer and Client over stdio)"))
    a(field("Tools / APIs", "OpenAlex, arXiv, Wikipedia (MediaWiki + REST) and Crossref APIs; custom hybrid retrieval (rank-bm25, NumPy); pypdf; httpx"))
    a(field("Database / Storage", "SQLite in WAL mode: runs, trace events, document chunks and embeddings, episodic memory, LLM and tool caches (a single database file on the VM)"))
    a(field("Frontend", "React 19, Vite 7, TypeScript, react-markdown; live updates over Server-Sent Events with polling fallback; deployed on Vercel (API address set by the VITE_API_BASE environment variable)"))
    a(field("Backend", "FastAPI + Uvicorn (REST + Server-Sent Events) on a Linux VM, served on port 8000 in a tmux session behind Nginx with an SSL certificate on my own domain; pytest for testing"))
    a(H2("System Architecture"))
    a(figure(FIG / "fig_architecture.png", "Deployment architecture: React frontend on Vercel, FastAPI and the agent workflow on a VM behind Nginx (HTTPS), tools in a separate MCP server process.", 6.2, border=False))
    a(P("The browser loads the React app from Vercel and calls the API on the backend's own HTTPS domain, configured at build time through `VITE_API_BASE`. CORS allows only the app's origin. On the VM, Nginx terminates TLS with the domain's certificate and forwards requests to Uvicorn on port 8000, which runs in a tmux session with a single worker. A request to start research creates a run record and executes the LangGraph workflow as a background task. Every node, model call and tool call emits a trace event that is stored in SQLite and pushed to the browser over Server-Sent Events. If a proxy buffers the stream, the UI switches to polling automatically. The agents reach their tools through an MCP client connected over stdio to the MCP tool server, which runs as a separate process."))
    a(H2("Implementation Steps"))
    a(numbered([
        "**Probing the platform.** Listing the models available to the API key showed that the Gemini 2.5 models are closed to new users. The 3.x Flash and Flash-Lite models were tested for function calling, JSON-schema output and latency before any design decision was made. The MCP 2.x SDK and LangGraph 1.2 APIs were checked with small spike programs, because both had changed from earlier versions (for example FastMCP was renamed MCPServer).",
        "**Scholarly tool layer** (`tools/scholarly.py`). Async clients for OpenAlex, arXiv, Wikipedia and Crossref normalise every result into one source schema (key, title, authors, year, venue, URL, DOI, abstract, citations). Details: arXiv is rate-limited to one request per 3 seconds with a lock, results are cached for 24 hours, and OpenAlex falls back to Crossref on HTTP 429/5xx.",
        "**MCP server and client** (`mcp_server.py`, `toolbox.py`). Four tools, a `kb://documents` resource and a `literature_review` prompt are exposed by an MCPServer. The backend starts it as a subprocess, lists its tools, converts their JSON schemas into Gemini function declarations and calls them over MCP. Tool errors are returned to the agent as data, not exceptions.",
        "**LLM gateway** (`llm.py`). One `generate()` function adds a model fallback chain, a per-model cool-down that honours the server's retry delay, retries with back-off for 5xx errors, a concurrency semaphore, a response cache keyed on the full request, explicit handling of safety blocks, and token, latency and model accounting for every call.",
        "**Library RAG** (`rag/`). Text is extracted from PDF, TXT or Markdown, chunked page by page (about 900 characters with 150 characters of overlap, split at paragraph, sentence and word boundaries), prefixed with the document title and page (lightweight contextual retrieval) and embedded. Retrieval fuses BM25 and cosine rankings with reciprocal rank fusion (k = 60, Cormack et al., 2009). It falls back to BM25 alone if embeddings are unavailable, and the in-memory index refreshes when the library changes.",
        "**Agent workflow** (`agents/graph.py`, `agents/prompts.py`). Typed state with reducers so parallel researchers can merge their findings; a run-wide source registry that de-duplicates the same paper found on both arXiv and OpenAlex; structured prompts and JSON schemas for each role; conditional edges for early exits; and a bounded reflection loop.",
        "**Guardrails and verification** (`guardrails.py`, `citations.py`). Regex-based screening for injection, jailbreak, exfiltration and tool-abuse patterns; PII redaction (e-mail, phone, Aadhaar, PAN, card numbers) tuned so that years are not redacted; a citation verifier and renumbering.",
        "**Memory and follow-up chat** (`memory.py`, `/api/runs/{id}/chat`). Run summaries are embedded and recalled by cosine similarity. Follow-up questions use cache-augmented generation over the brief and its abstracts.",
        "**API, run manager and observability** (`main.py`, `runs.py`, `events.py`). Background execution with a concurrency limit; pause and resume for plan approval; cancellation; trace persistence; SSE streaming with replay from the last event ID; per-client rate limiting; an optional access code to protect the free-tier quota on a public deployment.",
        "**Frontend** (`frontend/`). An Ask page; a run page with a live agent-team pipeline, activity log and metrics, plus tabs for the brief (citations open catalogue cards), sources, plan (editor during review), review rounds, follow-up chat and trace; a library page with a retrieval tester; an evaluation page; and a “How it works” page.",
        f"**Testing and evaluation** (`tests/`, `evals/`). {N_TESTS} automated tests run offline with a scripted fake LLM and fake tools. A benchmark compares Veritas with a single-call baseline and verifies every reference externally (Section 7).",
        "**Packaging and deployment** (`backend/run.sh`, `frontend/vercel.json`, `deploy/DEPLOY.md`). On the VM, `run.sh` creates the virtual environment, installs dependencies and starts Uvicorn on port 8000 inside tmux behind the existing Nginx/SSL set-up. On Vercel only the `VITE_API_BASE` variable is needed. The cross-origin set-up (CORS allow/deny, the event stream through a proxy, the production build) was verified locally before deployment. A Docker image was also built and smoke-tested as an alternative.",
    ]))
    a(code([
        "g = StateGraph(ResearchState)",
        "for name, fn in [(\"guardrail\", guardrail_node), (\"memory\", memory_node), (\"planner\", planner_node),",
        "                 (\"human_review\", human_review_node), (\"researcher\", researcher_node), (\"writer\", writer_node),",
        "                 (\"verifier\", verifier_node), (\"critic\", critic_node), (\"finalize\", finalize_node), (\"finish\", finish_node)]:",
        "    g.add_node(name, fn)",
        "g.add_edge(START, \"guardrail\")",
        "g.add_conditional_edges(\"guardrail\", after_guardrail, [\"memory\", \"finish\"])      # blocked -> stop",
        "g.add_edge(\"memory\", \"planner\")",
        "g.add_conditional_edges(\"planner\", after_planner, [\"human_review\", \"finish\"])    # out of scope -> stop",
        "g.add_conditional_edges(\"human_review\", fan_out, [\"researcher\", \"finish\"])      # Send() per sub-question",
        "g.add_edge(\"researcher\", \"writer\")                                                # join after all researchers",
        "g.add_edge(\"writer\", \"verifier\"); g.add_edge(\"verifier\", \"critic\")",
        "g.add_conditional_edges(\"critic\", after_critic, [\"writer\", \"finalize\"])          # reflection loop",
        "graph = g.compile(checkpointer=InMemorySaver())                                     # enables interrupt/resume",
    ], caption="Listing 1: Wiring of the agent workflow (simplified from backend/app/agents/graph.py)"))
    a(H2("Project Structure"))
    a(code([
        "backend/app/agents/graph.py    agent nodes, routing, fan-out, reflection loop, human-in-the-loop",
        "backend/app/agents/prompts.py  role prompts (role / constraints / tools / output) + JSON schemas",
        "backend/app/llm.py             Gemini gateway: fallback chain, cool-down, retries, cache, accounting",
        "backend/app/mcp_server.py      MCP tool server (4 tools, 1 resource, 1 prompt)",
        "backend/app/toolbox.py         MCP client -> Gemini function declarations",
        "backend/app/tools/scholarly.py OpenAlex, arXiv, Wikipedia, Crossref clients",
        "backend/app/rag/               chunking, ingestion, hybrid BM25 + embedding retrieval",
        "backend/app/guardrails.py      injection screening, PII redaction",
        "backend/app/citations.py       citation verification and renumbering",
        "backend/app/memory.py          episodic memory",
        "backend/app/main.py, runs.py, events.py, db.py, config.py   API, run manager, tracing, storage",
        f"backend/tests/                 {N_TESTS} automated tests     backend/evals/   benchmark + results",
        "frontend/src/                  React app (pages, live agent team, catalogue-card UI)",
        "deploy/                        deployment guide, HTTPS (Caddy) override, systemd unit",
    ], caption="Listing 2: Repository layout"))

    # ---------------- 7. Demonstration
    a(H1("7. Final Project Demonstration"))
    a(H2("Prototype / Application Link"))
    a(P("Web application: [[https://<project-name>.vercel.app – add after deployment]]"))
    a(P("Backend health check: [[https://<backend-domain>/api/health – add after deployment]]"))
    a(H2("GitHub Repository"))
    a(P("https://github.com/Faheem219/Agentic-AI-Flexi"))
    a(H2("Demonstration"))
    a(P("The screenshots below were taken from the running application with real research questions. Together they show the full user journey and the agentic behaviour behind it."))
    a(figure(FIG / "crops" / "ui_ask.jpg", "Ask page: the user writes a question, chooses depth and sources, and can ask to review the plan first.", 5.6, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_live_run.jpg", "A run in progress. Guardrail, memory, planner and plan review are done; three researchers work in parallel (two finished, one still searching arXiv); live metrics at the top.", 6.4, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_hitl_plan.jpg", "Human in the loop: the run is paused after planning. The user can edit sub-questions, change their sources, add guidance, approve or reject. Nothing is searched before approval.", 6.4, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_run_completed.jpg", "A completed run: every stage done, with the final metrics (time, model calls, tokens, tool calls, sources cited, citation coverage and critic score).", 6.4, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_citation_card.jpg", "Every citation in the brief opens a catalogue card built from database metadata. Here citation [1] is a passage from the user's own uploaded notes (page 2), retrieved by hybrid RAG.", 4.6, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_sources.jpg", "Sources tab: all records retrieved during the run with their call numbers (S-IDs); cited sources show their reference number.", 4.8, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_review.jpg", "Reflection loop: the critic sent draft 1 back (high-severity issue, citation coverage 57% < 75%) with specific fixes; draft 2 reached 94% coverage and was accepted.", 4.8, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_library.jpg", "Your library: uploaded documents and a retrieval tester showing the fused score with keyword (BM25) rank, semantic rank and cosine similarity for each passage.", 6.4, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_chat_cag.jpg", "Follow-up questions use cache-augmented generation: the brief and its abstracts are loaded in full, and the answer cites [1], the user's own notes.", 4.8, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_trace.jpg", "Trace tab: per-agent cost (model calls, tokens, tool calls, model time) and every trace event, downloadable as JSON.", 4.8, max_h_in=4.05))
    a(figure(FIG / "crops" / "ui_guardrail_block.jpg", "A prompt-injection attempt is blocked by the guardrail before any model call (0 model calls, 0 tokens).", 6.0, max_h_in=4.05))

    # ---------------- Testing and evidence
    a(H2("Testing and Evidence"))
    a(P("Veritas was tested at four levels: automated unit and integration tests, live end-to-end runs, a benchmark against a single-call baseline, and red-team cases for the guardrails. Table 9 summarises the strategy."))
    a(table(
        ["Level", "What is checked", "How", "Result"],
        [
            ["Unit and integration tests", "Guardrails, chunking, retrieval, citations, parsers, LLM gateway reliability, full workflow, API, MCP", f"pytest, {N_TESTS} tests, offline: scripted fake LLM and fake tools (deterministic, no quota)", f"{N_TESTS}/{N_TESTS} passed (Appendix B)"],
            ["End-to-end runs", "Real models, real APIs, real UI", "Manual runs with trace inspection", "Completed; behaviours in Table 14"],
            ["Benchmark", "Citation integrity and answer quality vs. a single-call baseline", f"{len(QS)} questions in {len(QS)} fields; external reference verification; blind LLM judge", f"References that exist: {pc(VV, VT)} vs. {pc(BV, BT)}"],
            ["Red-team suite", "Injection, jailbreak, exfiltration, tool abuse, PII, scope", "Guardrail cases + planner scope cases", f"{GR['passed']}/{GR['total']} and {SC['passed']}/{SC['total']}"],
        ],
        [1.2, 2.0, 2.0, 1.4], caption="Test strategy"))
    a(H3("Automated tests"))
    a(table(
        ["Test file", "Tests", "What is verified"],
        [
            ["test_guardrails.py", "13", "5 injection/jailbreak attacks blocked; 3 benign questions that mention “instructions” or “system prompts” pass (no false positives); e-mail, phone and PAN redacted; years not mistaken for phone numbers; length limits."],
            ["test_rag.py", "5", "Chunk size and overlap; page numbers kept; hybrid search ranks the CAG section first for a CAG query with both BM25 and dense ranks present; degrades to BM25 without embeddings; index refreshes after deletion."],
            ["test_citations_and_parsers.py", "9", "Invalid citation IDs removed; coverage counts uncited sentences; weak support flagged; grouped citations normalised and renumbered; JSON extraction from noisy output; arXiv, OpenAlex and Crossref parsers; OpenAlex → Crossref fallback on HTTP 429."],
            ["test_llm_gateway.py", "5", "Fallback to the next model on 429 and cool-down of the rate-limited model; retry on 503; error when every model fails; cache hit avoids a second call; no duplicates in the model chain."],
            ["test_graph.py", "8", "Full pipeline with the reflection loop (draft 1 rejected, draft 2 accepted, hallucinated [S99] removed, references only from real sources); injection rejected with zero model calls; out-of-scope rejection; plan edit then resume (only the human-written sub-question is researched); plan rejection; graceful degradation when one researcher fails; tool outage reported to the agent; researchers run in parallel."],
            ["test_api_and_mcp.py", "8", "Health and tool listing; full run lifecycle over HTTP (events, trace export, follow-up chat, stats); plan approval over HTTP and double-approval refused; SSE stream replays and ends; library upload, search, type check and delete; access-code protection; MCP server tools/resources/prompts; MCP tool round trip."],
        ],
        [1.5, 0.5, 4.5], caption=f"Automated test suite ({N_TESTS} tests, all passing)", sz=17))
    a(H3("Benchmark: Veritas vs. a single LLM call"))
    a(P(f"**Method.** For each of {len(QS)} research questions in different fields (computer science, education, health, AI, management, sustainability), Veritas ran at standard depth with no human review. The **baseline** made one call to the same Gemini model tier, asking for a ~400-word literature review with 6–8 real references in JSON. Every reference from both systems was then checked **by code**: a DOI is resolved through OpenAlex/Crossref and its registered title compared with the cited title; an arXiv ID is looked up on arXiv; otherwise the title is searched in Crossref and OpenAlex and fuzzy-matched (similarity ≥ 0.88 with year ± 1 = verified; 0.65–0.88 = partial; below = not found). Finally a separate LLM judge scored both answers blind and in random order from 1 to 5 on relevance, depth, evidence and transparency."))
    a(table(
        ["Measure", "Veritas (multi-agent)", "Baseline (single call)"],
        [
            ["References that exist (verified)", f"**{VV}/{VT} ({pc(VV, VT)})**", f"{BV}/{BT} ({pc(BV, BT)})"],
            ["References not found anywhere (likely fabricated)", "**0**", f"{BN} ({pc(BN, BT)})"],
            ["References matching a real paper only partially", "0", f"{BP} ({pc(BP, BT)})"],
            ["DOIs that resolve to the cited paper", f"**{V_DOI_C}/{V_DOI_G} ({pc(V_DOI_C, V_DOI_G)})**", f"{B_DOI_C}/{B_DOI_G} ({pc(B_DOI_C, B_DOI_G)})"],
            ["References per answer", f"{V['avg_references']:.1f}", f"{BL['avg_references']:.1f}"],
            ["Sentences carrying a citation", f"{round(V['avg_citation_coverage'] * 100)}%", "not measured"],
            ["Blind judge: relevance / depth / evidence / transparency (1–5)", " / ".join(f"{V['judge'][k]:.2f}" for k in ("relevance", "depth", "evidence", "transparency")), " / ".join(f"{BL['judge'][k]:.2f}" for k in ("relevance", "depth", "evidence", "transparency"))],
            ["Judge preferred", f"**{S['judge_wins']['veritas']} of {len(QS)}**", f"{S['judge_wins']['baseline']} of {len(QS)}"],
            ["Average time per answer", f"{V['avg_latency_s']:.1f} s", f"{BL['avg_latency_s']:.1f} s"],
            ["Model calls / tool calls per answer", f"{V['avg_llm_calls']:.1f} / {V['avg_tool_calls']:.0f}", "1 / 0"],
            ["Tokens per answer", f"{V['avg_tokens']:,.0f}", f"{BL['avg_tokens']:,.0f}"],
            ["Critic score; runs revised by the critic", f"{V['avg_critic_score']:.2f}/10; {V['runs_revised']} of {len(QS)}", "n/a"],
        ],
        [3.0, 1.6, 1.6], caption="Benchmark results (generated " + EVAL["generated_at"] + ")"))
    a(table(
        ["Question (field)", "Veritas verified", "Baseline verified", "Baseline not found", "Critic", "Time (s)", "Judge"],
        [[f"{q['question']} ({q['domain']})", f"{q['veritas_verification']['verified']}/{q['veritas_verification']['total']}",
          f"{q['baseline_verification']['verified']}/{q['baseline_verification']['total']}", str(q['baseline_verification']['not_found']),
          str(q["veritas"]["metrics"].get("critic_score")), str(q["veritas"]["latency_s"]),
          "Veritas" if q["judge"]["winner"] == "veritas" else q["judge"]["winner"]] for q in QS],
        [3.2, 0.8, 0.8, 0.8, 0.6, 0.6, 0.7], caption="Benchmark results per question", sz=16))
    a(P("**Interpretation.**", after=60))
    a(bullets([
        f"**Citation integrity is guaranteed by design, not by model quality.** All {VT} Veritas references were confirmed to exist, because the model can only cite records the tools returned and code builds the reference list. The baseline's integrity depended on the model: on the first question, served by the stronger gemini-3-flash-preview, all 7 references were real. On the other five, served by gemini-3.1-flash-lite after the stronger model's free daily quota ran out, only {BV - 7} of {BT - 7} were verified. Veritas stayed at 100% with the same weaker model, which shows the architecture removes the dependence on model strength.",
        f"**DOIs are a sensitive signal.** The baseline often cited a real-sounding paper with a wrong DOI; only {pc(B_DOI_C, B_DOI_G)} of its DOIs pointed to the cited work. Veritas DOIs come from the databases. The one mismatch was a record whose title differs slightly between OpenAlex and Crossref, so it is a verifier limitation rather than an error in the brief.",
        f"**Quality also improved.** The blind judge preferred Veritas on every question, with the largest gains in depth ({V['judge']['depth']:.1f} vs. {BL['judge']['depth']:.1f}) and transparency ({V['judge']['transparency']:.1f} vs. {BL['judge']['transparency']:.1f}). The Debates & Limitations and Research Gaps sections, enforced by the writer prompt and checked by the critic, are visible to the judge.",
        f"**The reflection loop did real work.** With the 75% coverage threshold, all {V['runs_revised']} first drafts were sent back. The writer tends to leave synthesis sentences in the summary and conclusion uncited, and the revisions raised the average coverage to {round(V['avg_citation_coverage'] * 100)}%.",
        f"**The price is time and tokens.** Veritas took about {V['avg_latency_s'] / BL['avg_latency_s']:.0f}× longer and used about {V['avg_tokens'] / BL['avg_tokens']:.0f}× more tokens than the single call. For a literature brief where each fabricated reference would cost the reader minutes to discover, this trade-off is justified. For quick factual questions it would not be, which is why a Quick depth setting exists.",
    ]))
    a(H3("Guardrail and scope tests"))
    a(table(
        ["Input", "Category", "Expected", "Result"],
        [[c["input"], c["category"], c["expected"], ("Pass: " if c["passed"] else "Fail: ") + c["got"]] for c in GR["cases"]]
        + [[c["input"], "planner scope check", c["expected"], ("Pass: " if c["passed"] else "Fail: ") + c["got"]] for c in SC["cases"]],
        [3.3, 1.6, 0.8, 1.0], caption=f"Red-team cases ({GR['passed'] + SC['passed']}/{GR['total'] + SC['total']} handled correctly)", sz=16))
    a(P("The benign cases matter as much as the attacks. Questions that legitimately mention “instructions” or “system prompts” are not blocked, because the patterns require an override verb and a target together. The weapons-synthesis request passed the keyword screen but was declined by the planner's model-based scope check: two layers, each catching what the other cannot."))
    a(H3("Behaviour observed in live runs"))
    a(table(
        ["Scenario", "What happened", "Evidence"],
        [
            ["Model outage", "On the first live run gemini-3-flash-preview returned HTTP 503 to the writer. The gateway switched to gemini-3.1-flash-lite and the run completed.", "llm_fallback event in the trace"],
            ["Free-tier quota exhausted", "During the benchmark the smart model's daily quota ran out (HTTP 429). The model was put in cool-down and the remaining calls went to the fallback chain; all 6 runs completed.", "models_used in run metrics"],
            ["Reflection improving the brief", "First live run: citation coverage went from 50% to 95% and the critic score from 8.25 to 9.75 after one revision.", "Review tab, Figure 9"],
            ["External API quota", "The anonymous OpenAlex budget (about 100 searches per day) ran out mid-run. Researchers continued with arXiv and the library and the brief still reached 94% coverage. A Crossref fallback was then added.", "tool_result errors in the trace; unit test"],
            ["Human edits the plan", "A reviewer replaced a planned sub-question with “How effective is spaced repetition software (e.g. Anki) for medical students?”; only the approved sub-questions were researched.", "plan_approved event with edited = true"],
            ["Injection / out-of-scope inputs", "Blocked with 0 model calls / declined by the planner with a reason.", "Figure 13, Table 13"],
            ["Duplicate sources", "The same paper returned by arXiv and OpenAlex was registered once after title-based de-duplication was added.", "Source registry, unit tests"],
            ["Using the user's library", f"For a CAG-vs-RAG question, the user's uploaded notes were cited as [1] alongside 11 arXiv papers ({SM['sources_cited']} of {SM['sources_found']} sources cited, coverage {round(SM['citation_coverage'] * 100)}%, critic {SM['critic_score']}).", "Figures 7, 11; Appendix C"],
        ],
        [1.3, 3.6, 1.4], caption="Behaviour observed during live operation", sz=17))
    a(H3("Limitations of the evaluation"))
    a(bullets([
        f"**Small sample.** {len(QS)} questions show a clear direction but are not a statistically powered study.",
        "**Existence is not support.** Reference verification proves that a cited paper exists, not that it supports the sentence. The verifier's lexical support check and the critic's groundedness score only approximate this.",
        "**LLM judge bias.** The judge belongs to the same model family as both systems. Blind labelling and random order reduce position bias but not self-preference, so the judge results should be read as supporting evidence only.",
        "**Title matching thresholds.** Fuzzy matching may misclassify papers with very similar titles. DOI and arXiv-ID checks are used first for this reason.",
    ]))

    # ---------------- 8. Conclusion
    a(H1("8. Conclusion"))
    a(P(f"This project set out to apply agentic AI to a specific, measurable problem: references that cannot be trusted in AI-generated literature summaries. Veritas meets every success criterion in Table 6. In the benchmark all {VT} of its references existed, compared with {pc(BV, BT)} for a single call to the same model; {round(V['avg_citation_coverage'] * 100)}% of its sentences were cited; all red-team cases were handled; and a standard brief took about a minute on free-tier infrastructure. It also shows how the course's concepts fit together in one working system: structured prompting, tool use, multi-agent orchestration with routing and parallelisation, reflection, human oversight, MCP, RAG and CAG, memory, guardrails, layered error handling and evaluation."))
    a(H2("Individual Reflection"))
    a(P("**From workflows to agents.** Course 1 taught agents inside n8n, where the canvas makes the control flow visible. Writing the same ideas as a LangGraph state machine in code showed me that an agentic system is mostly ordinary software engineering around a few model calls. Out of nine workflow steps, only four call an LLM. Deciding which steps really need a model was the most important design decision, and it came straight from the course's “AI agents vs. LLMs” and automation lectures."))
    a(P("**Prompting is iterative, as the Calendar agent case study showed.** My first researcher prompt let the model answer with prose instead of JSON, and the planner sometimes produced overlapping sub-questions. Following the course's Role, Constraints, Tools, Instructions and I/O structure, enforcing JSON schemas, and adding a final schema-only step to the researcher loop removed these failures. I learnt to treat prompts like code: change one thing, run, inspect the trace."))
    a(P("**Explaining behaviour I did not expect.**"))
    a(bullets([
        "The critic once scored a draft 9/8/10/9 but still answered “revise”. The model's verdict and its scores were inconsistent, which is a known weakness of using an LLM as a judge. I moved the decision into code, and since then the loop's behaviour can be predicted from the numbers.",
        "Every benchmark run was revised once. This was not a bug: the 75% coverage threshold catches the writer's habit of leaving summary and conclusion sentences uncited. The trade-off is roughly two extra model calls per run, in exchange for raising citation coverage by about 30 percentage points.",
        f"I expected the baseline to fabricate about half its references, as in the 2023 study. With the stronger model it fabricated none on the first question, but with the fallback model the fabrication rate rose sharply. This taught me that model quality is a moving target, and that guarantees should come from architecture rather than from model choice.",
        "External services were the least reliable part of the system: a 503 from Gemini, a daily quota on the preferred model and a daily budget on OpenAlex. Course 1's three layers of failure handling (retry, fallback, global handler) turned out to be the difference between a demo and a usable system.",
        "Working within the free tier shaped the architecture. Running the planned searches before asking the model (plan-and-execute), rather than letting the model decide every search (pure ReAct), reduced model calls by about a third with no visible loss in quality.",
    ]))
    a(P("**What I would do differently.** I would add the evaluation harness earlier, because several improvements (DOI checks, title de-duplication, the code-based verdict) only came from measuring. I would also store graph checkpoints in SQLite, so a run waiting for approval could survive a server restart."))
    a(H2("Limitations and Future Work"))
    a(bullets([
        "Use open-access full texts (not only abstracts) for deeper, better-grounded findings, and use entailment checking instead of lexical overlap to verify that a source supports a claim.",
        "Use persistent checkpoints (SQLite/Postgres) for durable human-in-the-loop pauses and multi-worker deployment.",
        "Add user accounts and per-user libraries, and export briefs to Word/BibTeX.",
        "Run a larger evaluation with human expert ratings to complement the LLM judge.",
        "Add support for paid or self-hosted models to remove free-tier quota limits.",
    ]))

    # ---------------- References
    a(H1("References", page_break=True))
    refs = [
        "Anthropic. (2024a). Introducing the Model Context Protocol. https://www.anthropic.com/news/model-context-protocol",
        "Anthropic. (2024b). Introducing contextual retrieval. https://www.anthropic.com/news/contextual-retrieval",
        "Chan, B. J., Chen, C.-T., Cheng, J.-H., & Huang, H.-H. (2024). Don't do RAG: When cache-augmented generation is all you need for knowledge tasks. arXiv:2412.15605.",
        "Cormack, G. V., Clarke, C. L. A., & Büttcher, S. (2009). Reciprocal rank fusion outperforms Condorcet and individual rank learning methods. Proceedings of SIGIR 2009, 758–759.",
        "Greshake, K., Abdelnabi, S., Mishra, S., Endres, C., Holz, T., & Fritz, M. (2023). Not what you've signed up for: Compromising real-world LLM-integrated applications with indirect prompt injection. Proceedings of AISec '23. arXiv:2302.12173.",
        "LangChain. (2026). LangGraph documentation. https://langchain-ai.github.io/langgraph/",
        "Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. Advances in Neural Information Processing Systems 33. arXiv:2005.11401.",
        "Madaan, A., Tandon, N., Gupta, P., et al. (2023). Self-Refine: Iterative refinement with self-feedback. Advances in Neural Information Processing Systems 36. arXiv:2303.17651.",
        "Priem, J., Piwowar, H., & Orr, R. (2022). OpenAlex: A fully-open index of scholarly works, authors, venues, institutions, and concepts. arXiv:2205.01833.",
        "Schuler, N., & Coton, S. (2026). AI Agents & AI Automation – The Practical Agentic AI Guide [Online course]. Udemy.",
        "Schluntz, E., & Zhang, B. (2024). Building effective agents. Anthropic. https://www.anthropic.com/engineering/building-effective-agents",
        "Walters, W. H., & Wilder, E. I. (2023). Fabrication and errors in the bibliographic citations generated by ChatGPT. Scientific Reports, 13, 14045. https://doi.org/10.1038/s41598-023-41032-5",
        "Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., & Cao, Y. (2023). ReAct: Synergizing reasoning and acting in language models. International Conference on Learning Representations. arXiv:2210.03629.",
        "Zheng, L., Chiang, W.-L., Sheng, Y., et al. (2023). Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. Advances in Neural Information Processing Systems 36. arXiv:2306.05685.",
    ]
    for ref in refs:
        a(para(rich(ref, sz=19), after=80))

    # ---------------- Appendices
    a(H1("Appendix A: Course Completion Certificates", page_break=True))
    a(figure(FIG / "cert_course1.png", "Certificate of completion: AI Agents & AI Automation – The Practical Agentic AI Guide (Udemy, 8 total hours).", 6.2))
    a(P("[[Certificates of the remaining courses to be added here.]]"))
    a(H1("Appendix B: Automated Test Output", page_break=True))
    a(P(f"Output of `python -m pytest -v` in `backend/` ({N_TESTS} passed, run offline with the scripted fake LLM):", after=60))
    lines = [l for l in PYTEST.splitlines() if "::" in l or "passed" in l]
    lines = [re.sub(r"\[(.{0,40}).*\]( PASSED)", r"[\1...]\2", l) if len(l) > 110 else l for l in lines]
    a(code([l.replace("tests/", "").replace("=" * 30, "=" * 6) for l in lines]))
    a(H1("Appendix C: Sample Research Brief Produced by Veritas", page_break=True))
    a(P(f"**Question:** {SAMPLE['question']}  **Settings:** standard depth; OpenAlex, arXiv and the user's library; plan reviewed by the user. **Run metrics:** {SM['duration_s']:.0f} s, {SM['llm_calls']} model calls, {SM['tool_calls']} tool calls, {SM['sources_found']} sources retrieved, {SM['sources_cited']} cited, citation coverage {round(SM['citation_coverage'] * 100)}%, critic score {SM['critic_score']}, {SM['revisions']} revision. The brief is reproduced below without edits (first sections and the full reference list).", after=100))
    report = SAMPLE["report"]
    cut = report.find("## Debates")
    excerpt = report[:cut] if cut > 0 else report[:4000]
    for block in excerpt.split("\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            a(para(run(block[3:], b=True, sz=22, color="365F91"), before=120, after=60, keep_next=True))
        elif block.startswith("### "):
            a(para(run(block[4:], b=True, sz=20), before=80, after=40, keep_next=True))
        else:
            if block.startswith(("* ", "- ")):
                block = "\u2022 " + block[2:]
            a(para(run(block.replace("**", ""), sz=19), after=80))
    a(para(run("[Debates & Limitations, Research Gaps and Conclusion sections omitted for length]", i=True, sz=19, color="5B6577"), after=100))
    a(para(run("References", b=True, sz=22, color="365F91"), before=120, after=60))
    for r in SAMPLE["references"]:
        authors = ", ".join(r.get("authors", [])[:3]) + (" et al." if len(r.get("authors", [])) > 3 else "")
        title = re.sub(r"\s+", " ", r.get("title", ""))
        line = f"[{r['n']}] {authors + '. ' if authors else ''}({r.get('year') or 'n.d.'}). {title}. {r.get('venue') or ''}. {r.get('url') or ''}"
        a(para(run(line, sz=17), after=40))
    return "".join(x)


# ------------------------------------------------------------------ packaging
def build() -> None:
    zin = zipfile.ZipFile(TEMPLATE)
    files = {n: zin.read(n) for n in zin.namelist()}
    doc = files["word/document.xml"].decode("utf-8")
    body = build_body()
    sect = re.search(r"<w:sectPr\b.*?</w:sectPr>", doc, re.S).group(0)
    start = doc.index("<w:body>") + len("<w:body>")
    end = doc.index("</w:body>")
    doc = doc[:start] + body + sect + doc[end:]
    files["word/document.xml"] = doc.encode("utf-8")

    # numbering: a clean bullet list and a restartable decimal list
    num = files["word/numbering.xml"].decode("utf-8")
    bullet_abs = (
        f'<w:abstractNum w:abstractNumId="{BULLET}"><w:nsid w:val="5A1B2C3D"/><w:multiLevelType w:val="hybridMultilevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val=""/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="o"/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="1440" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New" w:hint="default"/></w:rPr></w:lvl>'
        "</w:abstractNum>"
    )
    num_abs = (
        f'<w:abstractNum w:abstractNumId="{NUM_ABS}"><w:nsid w:val="6B2C3D4E"/><w:multiLevelType w:val="hybridMultilevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
    )
    first_num = num.index("<w:num ")
    num = num[:first_num] + bullet_abs + num_abs + num[first_num:]
    insts = f'<w:num w:numId="{BULLET}"><w:abstractNumId w:val="{BULLET}"/></w:num>'
    for nid in range(60, _num_counter[0]):
        insts += (f'<w:num w:numId="{nid}"><w:abstractNumId w:val="{NUM_ABS}"/>'
                  '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>')
    num = num.replace("</w:numbering>", insts + "</w:numbering>")
    files["word/numbering.xml"] = num.encode("utf-8")

    # images: media files, relationships, content types
    rels = files["word/_rels/document.xml.rels"].decode("utf-8")
    ct = files["[Content_Types].xml"].decode("utf-8")
    for i, (rid, data, ext) in enumerate(_media, start=1):
        name = f"media/report_image{i}.{'png' if ext == 'png' else 'jpeg'}"
        files[f"word/{name}"] = data
        rels = rels.replace("</Relationships>",
                            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{name}"/></Relationships>')
    for ext, mime in (("png", "image/png"), ("jpeg", "image/jpeg")):
        if f'Extension="{ext}"' not in ct:
            ct = ct.replace("<Default Extension=\"xml\"", f'<Default Extension="{ext}" ContentType="{mime}"/><Default Extension="xml"')
    files["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    files["[Content_Types].xml"] = ct.encode("utf-8")

    # document properties
    core = files["docProps/core.xml"].decode("utf-8")
    core = re.sub(r"<dc:title>.*?</dc:title>|<dc:title/>", "<dc:title>CA3 Agentic AI Report – Veritas</dc:title>", core)
    files["docProps/core.xml"] = core.encode("utf-8")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            zout.writestr(n, files[n])
        for n in files:
            if n not in zin.namelist():
                zout.writestr(n, files[n])
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(_media)} images, {_fig[0]} figures, {_tab[0]} tables)")


if __name__ == "__main__":
    build()
