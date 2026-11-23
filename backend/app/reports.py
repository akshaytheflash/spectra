"""PDF evidence reports (reportlab) for an alert or a campaign.

Contents: case summary, evidence snapshots with SHA-256 hashes, identifier
table, entity-graph image, timeline and generation timestamp.  Every page is
stamped with the report case id so partial copies are traceable.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY = colors.HexColor("#0b1220")
ACCENT = colors.HexColor("#22d3ee")
RED = colors.HexColor("#ef4444")
AMBER = colors.HexColor("#f59e0b")


def _styles() -> dict[str, ParagraphStyle]:
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontSize=20, textColor=NAVY, spaceAfter=6,
                                alignment=TA_LEFT),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, textColor=NAVY, spaceBefore=10),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontSize=8.5, leading=11.5),
        "mono": ParagraphStyle("mono", parent=ss["BodyText"], fontName="Courier", fontSize=7.2, leading=9),
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontSize=7.5, textColor=colors.grey),
    }


def _header_footer(canvas, doc, case_id: str) -> None:
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 14 * mm, w, 14 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(15 * mm, h - 9 * mm, "SPECTRA - Evidence Report")
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(w - 15 * mm, h - 9 * mm, f"Case {case_id}  |  page {doc.page}")
    canvas.setFillColor(colors.grey)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(15 * mm, 8 * mm,
                      f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | "
                      "Public-channel OSINT | hashes are SHA-256")
    canvas.restoreState()


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_COLOURS = {"slang": "#e11d48", "emoji": "#d97706", "intent": "#7c3aed", "price": "#059669",
            "hard_negative": "#64748b"}


def _highlighted(markup: list[dict], fallback: str) -> str:
    """Render explanation spans as coloured text in the PDF."""
    if not markup:
        return _escape(fallback).replace("\n", "<br/>")
    parts = []
    for seg in markup:
        text = _escape(seg.get("text", "")).replace("\n", "<br/>")
        colour = _COLOURS.get(seg.get("kind", ""))
        parts.append(f'<font color="{colour}"><b>{text}</b></font>' if colour else text)
    return "".join(parts)


def graph_png(posts: list[dict], ident_nodes: list[str], title: str = "") -> bytes | None:
    """Render the entity subgraph to PNG with matplotlib (headless Agg)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        g = nx.MultiGraph()
        for p in posts:
            g.add_node(p["id"], kind="Post", label=p.get("channel", ""))
            for ident in p.get("identifiers", []):
                nid = f"{ident['type']}:{ident['value']}"
                g.add_node(nid, kind=ident["type"], label=ident["value"])
                g.add_edge(p["id"], nid)
            ch = p.get("channel") or ""
            g.add_node(f"ch:{ch}", kind="Channel", label=ch)
            g.add_edge(p["id"], f"ch:{ch}")
        if g.number_of_nodes() == 0 or g.number_of_nodes() > 60:
            # keep the most connected identifiers + their posts
            keep = set(ident_nodes)
            for n in list(g.nodes):
                if g.nodes[n].get("kind") in ("Channel",):
                    keep.add(n)
            for n in list(ident_nodes):
                if n in g:
                    keep |= set(g.neighbors(n))
            g = g.subgraph([n for n in keep if n in g]).copy()
        fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=170)
        ax.set_facecolor("#0b1220")
        fig.patch.set_facecolor("#0b1220")
        pos = nx.spring_layout(g, seed=7, k=0.9)
        palette = {"Post": "#475569", "Channel": "#22d3ee", "phone": "#f472b6", "upi": "#a3e635",
                   "wallet": "#fbbf24", "username": "#c084fc", "location": "#38bdf8"}
        for kind, color in palette.items():
            nodes = [n for n in g.nodes if g.nodes[n].get("kind") == kind]
            if nodes:
                sizes = [220 if kind != "Post" else 90 for _ in nodes]
                nx.draw_networkx_nodes(g, pos, nodelist=nodes, node_color=color, node_size=sizes, ax=ax,
                                       edgecolors="#0b1220", linewidths=1.2, label=kind)
        nx.draw_networkx_edges(g, pos, ax=ax, edge_color="#334155", width=0.9)
        labels = {n: g.nodes[n].get("label", "")[:22] for n in g.nodes if g.nodes[n].get("kind") != "Post"}
        nx.draw_networkx_labels(g, pos, labels=labels, font_size=5.6, font_color="#e2e8f0", ax=ax)
        ax.set_title(title or "Entity graph", color="#e2e8f0", fontsize=9)
        ax.legend(facecolor="#111827", edgecolor="#334155", labelcolor="#e2e8f0", fontsize=6, loc="lower left")
        ax.axis("off")
        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def _media_image(path: str | None, max_w: float = 60 * mm) -> Image | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists() or p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        return None
    try:
        from PIL import Image as PILImage

        with PILImage.open(p) as im:
            w, h = im.size
        return Image(str(p), width=max_w, height=max_w * h / max(w, 1))
    except Exception:
        return None


def build_alert_report(alert: dict, out_path: Path) -> Path:
    """Render a single-alert PDF report."""
    st = _styles()
    case_id = f"AL-{alert['id'][:8].upper()}"
    story: list = []
    p = alert["post"]
    ev = alert.get("evidence") or {}
    story.append(Paragraph(f"Case {case_id}", st["title"]))
    story.append(Paragraph(
        f"<b>Category:</b> {alert['category']} &nbsp;&nbsp; <b>Risk:</b> {alert['risk']} &nbsp;&nbsp; "
        f"<b>Confidence:</b> {alert['confidence']:.2f} &nbsp;&nbsp; <b>Status:</b> {alert['status']}", st["body"]))
    story.append(Paragraph(
        f"<b>Channel:</b> @{p['channel']} &nbsp;&nbsp; <b>Posted:</b> {p['timestamp']} &nbsp;&nbsp; "
        f"<b>Link:</b> {p.get('permalink') or '-'}", st["body"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Evidence snapshot", st["h2"]))
    markup = (alert.get("explanation") or {}).get("markup") or []
    story.append(Paragraph(_highlighted(markup, p.get("text") or ""), st["body"]))
    img = _media_image(p.get("media_path"))
    if img is not None:
        story.append(Spacer(1, 2 * mm))
        story.append(img)
    elif (p.get("media_type") == "audio" and (p.get("transcript") or {}).get("text")):
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("<b>Voice-note transcript</b> (" +
                               _escape(str((p.get("transcript") or {}).get("language", ""))) + "): " +
                               _escape(p["transcript"]["text"]), st["body"]))
        for seg in (p.get("transcript") or {}).get("segments", [])[:8]:
            story.append(Paragraph(f"[{seg['start']:.1f}-{seg['end']:.1f}s] {_escape(seg['text'])}", st["small"]))
    qr = (p.get("qr") or {}).get("codes") or []
    for q in qr[:2]:
        story.append(Paragraph(f"QR ({_escape(q.get('kind', ''))}): {_escape(str(q.get('data'))[:180])}", st["mono"]))
        if q.get("kind") == "upi":
            parsed = q.get("parsed") or {}
            story.append(Paragraph(
                f"UPI VPA {_escape(parsed.get('vpa', ''))} · payee {_escape(parsed.get('payee_name', ''))} · "
                f"amount {_escape(str(parsed.get('amount') or 'open'))}", st["small"]))
    ocr_text = (p.get("ocr") or {}).get("text")
    if ocr_text:
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph("<b>OCR text</b>", st["small"]))
        story.append(Paragraph(_escape(ocr_text[:700]).replace("\n", "<br/>"), st["mono"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(f"SHA-256: {ev.get('sha256')}", st["mono"]))
    story.append(Paragraph(f"Captured at: {ev.get('captured_at')}", st["small"]))

    expl = alert.get("explanation") or {}
    story.append(Paragraph("Why it was flagged", st["h2"]))
    for reason in expl.get("reasons", [])[:8]:
        story.append(Paragraph(f"- {_escape(reason)}", st["body"]))
    if expl.get("matched_terms"):
        story.append(Paragraph("Matched terms: " + _escape(", ".join(expl["matched_terms"][:25])), st["body"]))

    story.append(Paragraph("Identifiers", st["h2"]))
    rows = [["Type", "Value", "Source", "Confidence"]]
    for i in alert.get("identifiers", [])[:40]:
        rows.append([i["type"], i["value"], i.get("source", ""), f"{float(i.get('confidence') or 0):.2f}"])
    if len(rows) == 1:
        rows.append(["-", "no identifiers extracted", "-", "-"])
    t = Table(rows, colWidths=[25 * mm, 95 * mm, 30 * mm, 25 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
    ]))
    story.append(t)

    if alert.get("audit"):
        story.append(Paragraph("Audit trail", st["h2"]))
        for a in alert["audit"]:
            story.append(Paragraph(
                f"{a['at']} - {a['actor']} - {a['action']} {a.get('from_status') or ''}->{a.get('to_status') or ''} "
                f"{_escape(a.get('notes') or '')}", st["small"]))

    png = graph_png([{"id": p["id"], "channel": p["channel"], "identifiers": alert.get("identifiers", [])}],
                    [f"{i['type']}:{i['value']}" for i in alert.get("identifiers", [])], "Entity links")
    if png:
        story.append(PageBreak())
        story.append(Paragraph("Entity graph", st["h2"]))
        story.append(Image(io.BytesIO(png), width=165 * mm, height=105 * mm))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=20 * mm, bottomMargin=15 * mm, title=f"SPECTRA {case_id}")
    doc.build(story, onFirstPage=lambda c, d: _header_footer(c, d, case_id),
              onLaterPages=lambda c, d: _header_footer(c, d, case_id))
    return out_path


def build_campaign_report(campaign: dict, out_path: Path) -> Path:
    """Render a campaign-cluster PDF report with timeline and evidence gallery."""
    st = _styles()
    case_id = f"CM-{campaign['id'][:8].upper()}"
    story: list = []
    story.append(Paragraph(f"Campaign {case_id}", st["title"]))
    story.append(Paragraph(
        f"<b>{_escape(campaign['title'])}</b><br/><b>Category:</b> {campaign['category']} &nbsp; "
        f"<b>Channels:</b> {len(campaign['channels'])} &nbsp; <b>Ads:</b> {campaign['post_count']} &nbsp; "
        f"<b>Similarity:</b> {campaign['similarity']*100:.0f}% &nbsp; "
        f"<b>Frequency:</b> {campaign['posting_frequency']:.1f}/day", st["body"]))
    story.append(Paragraph(
        f"<b>Time span:</b> {campaign['time_span'].get('start')} to {campaign['time_span'].get('end')}", st["body"]))
    story.append(Paragraph("Shared identifiers: " + _escape(", ".join(campaign.get("shared_identifiers", []))),
                           st["body"]))
    members = campaign.get("members", [])
    story.append(Paragraph("Timeline", st["h2"]))
    rows = [["Time", "Channel", "Snippet"]]
    for m in members[:30]:
        rows.append([str(m["timestamp"])[:16], m["channel"], _escape(m["text"][:90])])
    t = Table(rows, colWidths=[30 * mm, 35 * mm, 110 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 6.8), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
    ]))
    story.append(t)

    # media gallery (dedupe by image content)
    story.append(Paragraph("Evidence gallery", st["h2"]))
    seen: set[str] = set()
    imgs = []
    for m in members:
        if m.get("media_type") == "image" and m.get("media_path") and m["media_path"] not in seen:
            seen.add(m["media_path"])
            im = _media_image(m["media_path"], max_w=48 * mm)
            if im is not None:
                imgs.append(im)
    for i in range(0, len(imgs), 3):
        story.append(Table([imgs[i:i + 3]], colWidths=[55 * mm] * len(imgs[i:i + 3])))

    png = graph_png(members, [s for s in campaign.get("shared_identifiers", [])], "Campaign graph")
    if png:
        story.append(PageBreak())
        story.append(Paragraph("Entity graph", st["h2"]))
        story.append(Image(io.BytesIO(png), width=165 * mm, height=105 * mm))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=20 * mm, bottomMargin=15 * mm, title=f"SPECTRA {case_id}")
    doc.build(story, onFirstPage=lambda c, d: _header_footer(c, d, case_id),
              onLaterPages=lambda c, d: _header_footer(c, d, case_id))
    return out_path


def report_id_for(kind: str, obj_id: str, payload_hash: str) -> str:
    """Deterministic file name so repeated downloads are identical."""
    digest = hashlib.sha1(f"{kind}:{obj_id}:{payload_hash}".encode()).hexdigest()[:12]
    return f"{kind}_{obj_id[:8]}_{digest}.pdf"
