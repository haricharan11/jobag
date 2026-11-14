"""Build an ATS-friendly, text-based, one-page PDF resume from the master resume + a plan."""
import io
import re
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

ACCENT = colors.HexColor("#1F4E79")
MUTED = colors.HexColor("#555555")


def _bold_keywords(text, keywords):
    out = escape(text)
    for kw in sorted(keywords, key=len, reverse=True):
        pat = re.compile(r"(?<![A-Za-z0-9>])(" + re.escape(escape(kw)) + r")(?![A-Za-z0-9<])", re.I)
        out = pat.sub(r"<b>\1</b>", out, count=1)
    return out


def _styles(base):
    lead = base * 1.28
    return {
        "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=base * 2.1,
                               leading=base * 2.4, textColor=ACCENT),
        "headline": ParagraphStyle("headline", fontName="Helvetica-Bold", fontSize=base * 1.05,
                                   leading=base * 1.4, textColor=colors.black),
        "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=base * 0.95,
                                  leading=lead, textColor=MUTED),
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=base * 1.1, leading=base * 1.4,
                            textColor=ACCENT, spaceBefore=base * 0.7),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=base, leading=lead,
                               alignment=TA_JUSTIFY),
        "role": ParagraphStyle("role", fontName="Helvetica-Bold", fontSize=base, leading=lead),
        "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=base, leading=lead,
                                 leftIndent=10, bulletIndent=2, spaceAfter=base * 0.12),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=base, leading=lead),
        "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=base, leading=lead,
                                textColor=ACCENT),
    }


def _section(title, st):
    return [Paragraph(title.upper(), st["h"]),
            HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#BBBBBB"),
                       spaceBefore=1, spaceAfter=3)]


def _story(resume, plan, base, max_bullets):
    st = _styles(base)
    kw = plan.get("k", [])
    c = resume["contact"]
    story = [
        Paragraph(escape(resume["name"]), st["name"]),
        Paragraph(escape(resume["headline"]), st["headline"]),
        Paragraph(escape(" | ".join([c["email"], c["phone"], c["linkedin"], c["location"]])), st["contact"]),
    ]
    story += _section("Professional Summary", st)
    story.append(Paragraph(_bold_keywords(plan.get("sum") or resume["summary"], kw), st["body"]))

    story += _section("Technical Skills", st)
    by_name = {s["name"]: s["items"] for s in resume["skills"]}
    rows = [[Paragraph(escape(n), st["cellb"]), Paragraph(escape(by_name[n]), st["cell"])]
            for n in list(dict.fromkeys([s for s in plan.get("sk", []) if s in by_name] + list(by_name)))]
    t = Table(rows, colWidths=[32 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5)]))
    story.append(t)

    story += _section("Professional Experience", st)
    order = plan.get("b") or []
    for exp in resume["experience"]:
        story.append(Paragraph(f"{escape(exp['company'])} — {escape(exp['location'])}", st["role"]))
        for role in exp["roles"]:
            bullets = {b["id"]: b["text"] for b in role["bullets"]}
            ids = [i for i in order if i in bullets] or list(bullets)
            ids = ids[:max_bullets.get(role["key"], len(ids))]
            head = Table([[Paragraph(f"<i>{escape(role['title'])}</i>", st["role"]),
                           Paragraph(escape(role["dates"]), ParagraphStyle(
                               "d", parent=st["cell"], alignment=2, textColor=MUTED))]],
                         colWidths=[None, 45 * mm])
            head.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                      ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                      ("TOPPADDING", (0, 0), (-1, -1), 1),
                                      ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            items = [Paragraph(_bold_keywords(bullets[i], kw), st["bullet"], bulletText="•") for i in ids]
            story.append(KeepTogether([head] + items[:1]))
            story += items[1:]

    story += _section("Education", st)
    for ed in resume["education"]:
        story.append(Paragraph(escape(ed["degree"]), st["role"]))
        story.append(Paragraph(f"{escape(ed['school'])} · {escape(ed['dates'])}", st["contact"]))
    return story


def _build(resume, plan, base, max_bullets):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=11 * mm, bottomMargin=10 * mm,
                            title=f"{resume['name']} - Resume", author=resume["name"])
    doc.build(_story(resume, plan, base, max_bullets))
    data = buf.getvalue()
    return data, len(PdfReader(io.BytesIO(data)).pages)


def render(resume, plan, path):
    """Write the PDF, shrinking font then trimming lowest-priority bullets to fit one page."""
    attempts = [(9.6, {}), (9.2, {}), (8.8, {}), (8.8, {"a": 9, "b": 3}),
                (8.6, {"a": 8, "b": 2}), (8.4, {"a": 7, "b": 2})]
    for base, caps in attempts:
        data, pages = _build(resume, plan, base, caps)
        if pages == 1:
            break
    with open(path, "wb") as f:
        f.write(data)
    return pages
