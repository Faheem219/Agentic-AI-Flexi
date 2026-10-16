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


def cell(text: str, width: int, fill=None, sz=18, bold=False, keep=False) -> str:
    shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    paras = "".join(
        para(rich(line, sz=sz, b=bold), after=0, line=252, keep_next=keep) for line in str(text).split("\n"))
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
        '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(cell(h, w, "F2F2F2", sz, True, keep=True) for h, w in zip(header, ws)) + "</w:tr>",
    ]
    for ri, row in enumerate(rows):
        keep = ri < len(rows) - 1
        tbl.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(cell(c, w, None, sz, keep=keep) for c, w in zip(row, ws)) + "</w:tr>")
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
BT, BV = _pool("baseline", "total"), _pool("baseline", "verified")
BN, BP = _pool("baseline", "not_found"), _pool("baseline", "partial")
B_DOI_G, B_DOI_C = _pool("baseline", "dois_given"), _pool("baseline", "doi_correct")
V_DOI_G, V_DOI_C = _pool("veritas", "dois_given"), _pool("veritas", "doi_correct")
pc = lambda a, b: f"{round(100 * a / b)}%" if b else "n/a"  # noqa: E731
GR, SC = EVAL["guardrails"], EVAL["scope"]
N_TESTS = int(re.search(r"(\d+) passed", PYTEST).group(1))

# Course hours: full courses use the course length; partial ones use the Infosys Springboard
# listing shown in the progress screenshots multiplied by the completed share.
COURSES = [
    # title, duration text, completed minutes, evidence
    ("AI Agents & AI Automation – The Practical Agentic AI Guide", "8 h 12 min (complete)", 492,
     "Completion certificate, 11 Sept 2026 (Figure 12)"),
    ("AI Engineer Agentic Track: The Complete Agent & MCP Course", "20 h 52 min (complete)", 1252,
     "Completion certificate, 21 Sept 2026 (Figure 13)"),
    ("Agentic AI – Private Agentic RAG with LangGraph and Ollama", "17 h 37 min; 80% completed (14 h 6 min)", 0.8 * 1057,
     "Progress screenshot (Figure 14)"),
    ("Agentic AI for Beginners: Build AI Agents with LangGraph", "5 h 51 min; 90% completed (5 h 16 min)", 0.9 * 351,
     "Progress screenshot (Figure 15)"),
]
TOTAL_MIN = int(sum(c[2] for c in COURSES))
TOTAL_TXT = f"{TOTAL_MIN // 60} h {TOTAL_MIN % 60} min"


# ------------------------------------------------------------------ content
COURSE_LEARNING = [
    "n8n workflows and agents, structured prompting, error handling, a calendar agent, RAG and CAG.",
    "agent loops, design patterns, guardrails, LLM-as-judge, Agents SDK, CrewAI, LangGraph and MCP servers.",
    "(first 80%) Ollama, LangGraph, a SQL agent, ChromaDB ingestion, BM25 re-ranking, corrective and reflexion RAG.",
    "(first 90%) agent fundamentals, multi-agent systems, LangGraph agents with knowledge bases and tools, Bedrock agents.",
]


def build_body() -> str:
    x: list[str] = []
    a = x.append
    shot = lambda name, cap, w=6.4: a(figure(FIG / "crops" / name, cap, w, max_h_in=4.05))  # noqa: E731

    # ---------------- cover (template fields)
    a(para(run("CA3 – AGENTIC AI", b=True, sz=40), jc="center"))
    a(field("Course / Subject", "Agentic AI"))
    a(field("Course Title", "Four agentic-AI courses (Section 3)"))
    a(field("Course Duration (minimum 45 hours)", f"{TOTAL_TXT} completed"))
    a(field("Faculty", "________________________"))
    a(field("Division", "CSE C"))
    a(field("Name", "Faheemuddin Sayyed"))
    a(field("PRN", "23070122196"))

    # ---------------- 1
    a(H1("1. Introduction to Agentic AI"))
    a(H2("Objective"))
    a(P("This report documents what I learnt in four agentic-AI courses (C1–C4, Section 3) and how I applied it to design, build, test and deploy **Veritas**, a multi-agent research assistant whose citations can be verified. The aims were to understand agents, apply them to a measurable problem and evaluate the result."))
    a(H2("Key Concepts Understood"))
    a(table(
        ["Concept", "Course", "Used in Veritas"],
        [
            ["Agents vs. workflows", "C1, C2, C4", "Code for exact checks; LLMs only where judgement is needed"],
            ["Tool calling, agent loop", "C2, C4", "Researchers search until evidence suffices"],
            ["Structured outputs", "C1, C2", "Schema-validated JSON from agents"],
            ["Design patterns", "C2", "Routing, parallel fan-out, evaluator–optimizer loop"],
            ["LangGraph state and checkpointers", "C2–C4", "Workflow graph; interrupt and resume for plan review"],
            ["Model Context Protocol", "C2", "Search tools run in a separate MCP server"],
            ["RAG, re-ranking, CAG", "C1, C3", "Hybrid library search; CAG follow-up chat"],
            ["Corrective / reflexion RAG", "C3", "Critic returns weak drafts for revision"],
            ["Guardrails, evaluation", "C1, C2", "Injection screening, LLM-as-judge benchmark"],
            ["Reliability", "C1, C2", "Retries, model fallback, cool-downs, caching"],
        ],
        [1.7, 0.9, 3.4], caption="Key concepts, their source course and their use in the project"))

    # ---------------- 2
    a(H1("2. Understanding of Agentic AI"))
    a(H2("What is an AI Agent?"))
    a(P("An AI agent is an LLM pursuing a goal in a loop: reason, call a tool, observe the result, decide the next step (the ReAct pattern). It combines a model, instructions, tools and memory, with control code enforcing limits."))
    a(P("A single LLM call cannot use live data or check itself; a fixed workflow cannot adapt. Agents do both at the cost of predictability, so workflows should be used where the path is known."))
    a(P("Multi-agent systems divide work among specialised agents through chaining, routing, parallelisation, orchestrator–workers and evaluator–optimizer patterns. Agents can hallucinate, loop or be hijacked by prompt injection, so they need guardrails, bounded loops, human checkpoints and evaluation."))

    # ---------------- 3
    a(H1("3. Courses Completed (multiple courses as applicable)"))
    a(H2("Course Details"))
    for (title, dur, _, ev), learn in zip(COURSES, COURSE_LEARNING):
        a(para(run("Course Title: ", b=True) + rich(title), style="ListParagraph", num=10, after=40))
        a(field("Platform / Provider", "Infosys Springboard (Udemy)"))
        a(field("Course Duration (minimum 45 hours)", dur))
        a(field("Completion Evidence", ev))
        a(field("Key Learning", learn[0].upper() + learn[1:] if not learn.startswith("(") else learn))
    a(P(f"**Total:** {TOTAL_TXT}."))

    # ---------------- 4
    a(H1("4. Problem Statement"))
    a(H2("Problem Statement"))
    a(para(run("Problem Statement: ", b=True) + rich(
        f"Chatbots used for literature reviews invent references: Walters and Wilder (2023) found 55% of GPT-3.5 and 18% of GPT-4 citations fabricated, and in my benchmark only {pc(BV, BT)} of a current Gemini model's references existed. Checking them costs students hours. Goal: an agentic system that researches any question in real scholarly databases and returns a brief where every claim cites a retrieved publication, transparently, with human oversight, on free-tier infrastructure."), after=140))
    a(H3("Why an agentic solution"))
    a(P("A single call cannot query databases or verify citations, and a fixed pipeline cannot decide what to search for varied questions. The task needs agents that plan, search, judge and revise, inside deterministic safeguards that guarantee citation integrity."))
    a(H3("Scope and success criteria"))
    a(P("Scope: research questions in any field, answered from scholarly metadata, abstracts and the user's documents; constraints: free-tier Gemini and public APIs."))
    a(table(
        ["Criterion", "Target", "Result (Section 7)"],
        [
            ["References that exist", "≥ 95%", pc(VV, VT)],
            ["Sentences carrying a citation", "≥ 75%", f"{round(V['avg_citation_coverage'] * 100)}%"],
            ["Attacks and invalid inputs handled", "All cases", f"{GR['passed'] + SC['passed']}/{GR['total'] + SC['total']}"],
            ["Time per standard brief", "< 2 min", f"{V['avg_latency_s']:.0f} s"],
            ["Model calls per run (free tier)", "≤ 15", f"{V['avg_llm_calls']:.1f}"],
            ["Human oversight", "Plan review and trace", "Implemented"],
        ],
        [2.6, 1.6, 1.6], caption="Success criteria"))

    # ---------------- 5
    a(H1("5. Proposed Solution"))
    a(H2("Project Title"))
    a(P("Veritas: a multi-agent research assistant with verifiable citations"))
    a(H2("Project Description"))
    a(P("Agents plan, search OpenAlex, arXiv, Wikipedia and the user's documents, write a brief and critique it. The model never writes references: agents cite only IDs of retrieved records, code removes unknown IDs, and references are built from database metadata. Users can review the plan, add their own documents, watch agents live and ask follow-up questions."))
    a(H2("Agent Workflow"))
    a(figure(FIG / "fig_workflow.png", "Agent workflow (LangGraph). Rounded boxes are LLM agents, square boxes deterministic code, the dashed box the optional human checkpoint; red arrows are early exits and the reflection loop.", 6.3, border=False))
    a(table(
        ["Component", "Type", "Responsibility", "Why a separate role"],
        [
            ["Guardrail", "Code", "Blocks injection, redacts personal data", "Cheap, auditable"],
            ["Memory", "Embeddings", "Recalls similar past runs", "Builds on earlier work"],
            ["Planner", "LLM (smart)", "Checks scope, writes sub-questions, routes them to tools", "Quality drives later steps"],
            ["Plan review", "Human", "Edit, approve or reject the plan", "Oversight before any cost is spent"],
            ["Researchers", "LLM (fast), parallel", "Search via MCP, then refine; findings tied to source IDs", "Focused contexts; parallel speed"],
            ["Writer", "LLM (smart)", "Cited brief from findings only", "One consistent voice"],
            ["Verifier", "Code", "Removes invalid citations, measures coverage", "Exact checks belong in code"],
            ["Critic", "LLM (smart)", "Scores coverage, groundedness, clarity, balance", "Independent evaluator"],
            ["Publisher", "Code", "Builds references from metadata", "References can never be invented"],
        ],
        [1.0, 1.0, 2.2, 2.0], caption="Agent and tool roles", sz=17))
    a(table(
        ["MCP tool", "Source", "Purpose"],
        [
            ["search_openalex", "OpenAlex (Crossref fallback)", "Peer-reviewed papers in any field"],
            ["search_arxiv", "arXiv", "Recent preprints"],
            ["search_wikipedia", "Wikipedia", "Background only"],
            ["search_knowledge_base", "User's documents", "Hybrid RAG, cited by page"],
        ],
        [1.6, 1.8, 2.4], caption="Tools exposed by the MCP server"))
    a(H3("Design decisions"))
    a(bullets([
        "**Plan-and-execute, then ReAct:** planned queries run without a model call; the model only decides whether to search again, keeping runs to about eight calls.",
        "**Two model tiers with fallback:** stronger model for planning, writing and critique; faster model for research.",
        "**Code-based acceptance rule:** revise if any score is below 7, a serious issue exists or coverage is below 75%.",
        "**MCP for tools:** tools are decoupled from agents and reusable by any MCP host.",
        "**RAG for the library, CAG for follow-ups:** the library grows, while one brief fits entirely in context.",
    ]))

    # ---------------- 6
    a(H1("6. Implementation"))
    a(H2("Technologies Used"))
    a(field("Programming Language", "Python 3.12, TypeScript"))
    a(field("LLM / AI Model", "Google Gemini 3 Flash and Flash-Lite (free tier) with automatic fallback; gemini-embedding-001"))
    a(field("Agent Framework", "LangGraph; Model Context Protocol SDK"))
    a(field("Tools / APIs", "OpenAlex, arXiv, Wikipedia, Crossref; hybrid BM25 + embedding search"))
    a(field("Database / Storage", "SQLite"))
    a(field("Frontend", "React + Vite on Vercel"))
    a(field("Backend", "FastAPI with Server-Sent Events on a VM behind Nginx (HTTPS); pytest"))
    a(figure(FIG / "fig_architecture.png", "Deployment architecture: React app on Vercel; FastAPI, the LangGraph workflow and the MCP tool server on a VM behind Nginx.", 6.2, border=False))
    a(H2("Implementation Steps"))
    a(numbered([
        "**Platform probing:** tested which Gemini models the key could use, function calling and JSON output, and the MCP and LangGraph APIs.",
        "**Tools and MCP server:** API clients normalise results into one source format, with rate limiting, caching and a Crossref fallback, exposed as four MCP tools.",
        "**LLM gateway:** fallback chain, cool-downs, retries, response cache and token accounting.",
        "**Library RAG:** page-aware chunking with overlap, title-prefixed chunks, Gemini embeddings, and BM25 and cosine rankings fused by reciprocal rank.",
        "**Agent workflow:** typed state, a de-duplicating source registry, role prompts with JSON schemas, fan-out, interrupt/resume and a bounded reflection loop.",
        "**Safety:** regex guardrails, PII redaction and the citation verifier.",
        "**API and interface:** FastAPI with background runs, live streaming and rate limiting; a React interface.",
        f"**Testing and deployment:** {N_TESTS} tests and a benchmark; a start script on the VM and Vercel for the frontend.",
    ]))

    # ---------------- 7
    a(H1("7. Final Project Demonstration"))
    a(H2("Prototype / Application Link"))
    a(P("________________________________________"))
    a(H2("GitHub Repository"))
    a(P("________________________________________"))
    a(H2("Demonstration"))
    shot("ui_live_run.jpg", "Live run: guardrail, memory, planner and plan review are done; three researchers search in parallel while metrics update.")
    shot("ui_hitl_plan.jpg", "Human in the loop: after planning the run pauses; the user can edit sub-questions and sources, then approve or reject.")
    shot("ui_citation_card.jpg", "Published brief. Each citation opens a card built from database metadata; [1] is a page of the user's uploaded notes found by hybrid RAG.", 4.6)
    shot("ui_review.jpg", "Reflection loop: the critic returned draft 1 (high-severity issue, 57% citation coverage); draft 2 reached 94% and was accepted.", 4.8)
    shot("ui_library.jpg", "Library retrieval: each passage shows its fused score with keyword (BM25) and semantic ranks.")
    shot("ui_chat_cag.jpg", "Follow-up questions use cache-augmented generation over the brief and its abstracts.", 4.8)
    shot("ui_guardrail_block.jpg", "A prompt-injection attempt blocked before any model call (0 model calls, 0 tokens).", 6.0)
    a(H2("Testing and Evidence"))
    a(P(f"Testing covered automated tests, live runs, a benchmark and red-team cases. All {N_TESTS} automated tests run offline with a scripted LLM and pass (Table 5, Figure 10)."))
    a(table(
        ["Area", "Tests", "What is verified"],
        [
            ["Guardrails", "13", "Attacks blocked; benign questions mentioning “instructions” allowed; PII redacted"],
            ["RAG", "5", "Chunking, hybrid ranking, BM25-only fallback, index refresh"],
            ["Citations and parsers", "9", "Invalid IDs removed, coverage, renumbering, API parsers, Crossref fallback"],
            ["LLM gateway", "5", "Fallback on 429, retry on 503, cache hit"],
            ["Workflow", "8", "Reflection loop, rejections, plan edit and resume, graceful degradation, parallelism"],
            ["API and MCP", "8", "Run lifecycle, live stream, library, access code, MCP tools"],
        ],
        [1.4, 0.5, 4.2], caption="Automated test suite", sz=17))
    a(figure(FIG / "fig_tests.png", f"Automated test output ({N_TESTS} passed).", 5.4, max_h_in=5.0))
    a(P(f"**Benchmark.** {len(QS)} questions from {len(QS)} fields were answered by Veritas and by one call to the same Gemini model. Every reference was checked in code against Crossref, OpenAlex and arXiv, and a blind LLM judge scored both answers (Table 6, Figure 11)."))
    a(table(
        ["Measure", "Veritas", "Single call"],
        [
            ["References that exist", pc(VV, VT), pc(BV, BT)],
            ["References not found anywhere", "0%", pc(BN, BT)],
            ["DOIs pointing to the cited paper", pc(V_DOI_C, V_DOI_G), pc(B_DOI_C, B_DOI_G)],
            ["Judge preference", f"{S['judge_wins']['veritas']} of {len(QS)}", f"{S['judge_wins']['baseline']} of {len(QS)}"],
            ["Time per answer", f"{V['avg_latency_s']:.0f} s", f"{BL['avg_latency_s']:.0f} s"],
            ["Tokens per answer", f"{V['avg_tokens']:,.0f}", f"{BL['avg_tokens']:,.0f}"],
        ],
        [2.8, 1.5, 1.5], caption="Benchmark results"))
    a(figure(FIG / "crops" / "ui_evals_top.jpg", "Evaluation page: headline comparison, cost of the extra rigour and per-question results.", 6.2, max_h_in=5.0))
    a(P("**Observed behaviour in live runs:**", after=60))
    a(bullets([
        "A 503 from the primary model was absorbed by the fallback chain and the run completed.",
        "When the stronger model's daily quota ran out, runs continued on a lighter model.",
        "On the first live run, revision raised citation coverage from 50% to 95%.",
        "When OpenAlex's free budget ran out, researchers used other tools; a Crossref fallback was added.",
        f"Red-team: {GR['passed']}/{GR['total']} guardrail and {SC['passed']}/{SC['total']} scope cases were correct, with injections blocked at zero model calls.",
    ]))
    a(P("**Interpretation.** On the lighter model the baseline's accuracy fell while Veritas stayed at 100%: integrity comes from the architecture. **Limitations:** a small sample; verification proves existence, not support; the judge shares a model family with both systems."))

    # ---------------- 8
    a(H1("8. Conclusion"))
    a(P(f"Veritas met every success criterion: all references were real, {round(V['avg_citation_coverage'] * 100)}% of sentences were cited and every attack was blocked, while a standard brief took about a minute on free-tier services. The cost is roughly {V['avg_latency_s'] / BL['avg_latency_s']:.0f} times the latency and {V['avg_tokens'] / BL['avg_tokens']:.0f} times the tokens of a single call, which is acceptable where trust matters."))
    a(H2("Individual Reflection"))
    a(P("Courses 1 and 4 taught agents inside n8n and AWS Bedrock. Rebuilding them in code showed me that most of an agentic system is ordinary engineering: only four of nine steps call an LLM, and choosing those steps was the key design decision. Course 2's deep-research and MCP projects shaped the planner–researcher–writer design and tool server; Course 3's corrective and reflexion RAG inspired the critic loop and BM25 re-ranking."))
    a(P("Observations changed the design. The critic once said “revise” while scoring everything 8 or higher, so the decision moved into code. Every benchmark draft was revised once because the writer leaves summary sentences uncited; two extra calls raised coverage by about 30 points. The stronger model gave real references until its quota ran out, so guarantees must come from architecture, not model choice. Quota errors outnumbered bugs, making Course 1's layered error handling the most valuable lesson."))
    a(H2("Limitations and Future Work"))
    a(bullets([
        "Use open-access full texts and entailment checks to verify that each source supports its claim.",
        "Persist graph checkpoints so plan reviews survive server restarts.",
        "Run a larger evaluation with human expert ratings.",
    ]))

    # ---------------- references + appendix
    a(H1("References"))
    a(P("Walters, W. H., & Wilder, E. I. (2023). Fabrication and errors in the bibliographic citations generated by ChatGPT. __Scientific Reports, 13__, 14045."))
    a(P("Yao, S., Zhao, J., Yu, D., et al. (2023). ReAct: Synergizing reasoning and acting in language models. __ICLR 2023__."))
    a(H1("Appendix A: Certificates and Progress"))
    a(figure(FIG / "cert_course1.png", "Course 1 completion certificate.", 5.6, max_h_in=4.0))
    a(figure(FIG / "cert_course2.png", "Course 2 completion certificate.", 5.6, max_h_in=4.0))
    a(figure(FIG / "progress_course3.jpg", "Course 3 progress (about 80%).", 6.4, max_h_in=4.0))
    a(figure(FIG / "progress_course4.jpg", "Course 4 progress (about 90%).", 6.4, max_h_in=4.0))
    return "".join(x)


def word_count(body_xml: str) -> int:
    """Words in the report, excluding figure captions (Caption-style paragraphs)."""
    total = 0
    for p in re.findall(r"<w:p>.*?</w:p>", body_xml, flags=re.S):
        if 'w:val="Caption"' in p:
            continue
        text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))
        total += len([w for w in re.split(r"\s+", text) if w])  # Word-style: every whitespace-separated token
    return total


# ------------------------------------------------------------------ packaging
def build() -> None:
    zin = zipfile.ZipFile(TEMPLATE)
    files = {n: zin.read(n) for n in zin.namelist()}
    doc = files["word/document.xml"].decode("utf-8")
    body = build_body()
    print(f"words (excluding figure captions): {word_count(body)}")
    caps = re.findall(r"<w:p>(?:(?!</w:p>).)*?w:val=\"Caption\".*?</w:p>", body, flags=re.S)
    cap_words = sum(len([w for w in re.split(r"\s+", " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", c))) if w]) for c in caps)
    print(f"caption words: {cap_words}")
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

    # black text only: every colour in styles (headings, captions, title) and the footer -> black
    for part in ("word/styles.xml", "word/footer1.xml"):
        xml = files[part].decode("utf-8")
        xml = re.sub(r"<w:color [^>]*/>", '<w:color w:val="000000"/>', xml)
        files[part] = xml.encode("utf-8")

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
