#!/usr/bin/env python3
"""Generate a small, realistic, thematically-coherent document corpus for
demoing SYLTHARAE search: real sentences, real structure, deliberate
duplicates/near-duplicates, spread across multiple sources/sides/categories.

Scenario: a corporate investigation into "Meridian Aerospace" and its
supplier "Trident Logistics" -- contracts, invoices, emails, meeting notes,
spreadsheets, and a couple of reports -- split across two custodial sources
("Meridian Aerospace - Legal Hold" and "Trident Logistics - Email Archive")
and two sides ("Meridian Aerospace" and "Trident Logistics").
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches, Pt
from fpdf import FPDF

random.seed(42)

ROOT = Path("/home/user/syltharae_corpus/realistic")

PEOPLE = [
    "Elena Marsh", "David Okafor", "Priya Nair", "Tomas Reyes",
    "Grace Lindqvist", "Marcus Webb", "Sana Farid", "Liam O'Connell",
]
COMPANIES = ["Meridian Aerospace", "Trident Logistics", "Halcyon Freight Partners"]
PROJECTS = ["Project Falcon", "Project Sable", "the Q3 fuselage contract", "the Nairobi hub expansion"]
KEYWORDS = [
    "invoice", "contract", "amendment", "shipment", "delay", "penalty clause",
    "audit", "compliance review", "purchase order", "wire transfer",
    "non-disclosure agreement", "fuselage components", "customs clearance",
    "freight forwarding", "quality assurance", "root cause analysis",
]


def _rand_date(start_year=2024, end_year=2026):
    start = date(start_year, 1, 1)
    end = date(end_year, 9, 1)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def ensure_dirs():
    for sub in [
        "meridian_legal_hold/contracts",
        "meridian_legal_hold/emails",
        "meridian_legal_hold/financials",
        "meridian_legal_hold/reports",
        "trident_email_archive/emails",
        "trident_email_archive/invoices",
        "trident_email_archive/meeting_notes",
        "trident_email_archive/spreadsheets",
    ]:
        (ROOT / sub).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Content generators
# ---------------------------------------------------------------------------

def make_email_text(sender, recipient, subject, body_lines, sent_date):
    lines = [
        f"From: {sender}",
        f"To: {recipient}",
        f"Subject: {subject}",
        f"Date: {sent_date.isoformat()}",
        "",
    ]
    lines.extend(body_lines)
    return "\n".join(lines)


def write_txt(path: Path, text: str):
    path.write_text(text, encoding="utf-8")


def write_docx(path: Path, title: str, paragraphs: list[str]):
    doc = Document()
    doc.add_heading(title, level=1)
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))


def write_pdf(path: Path, title: str, paragraphs: list[str]):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, title)
    pdf.set_font("Helvetica", "", 11)
    pdf.ln(4)
    for p in paragraphs:
        pdf.multi_cell(0, 6, p)
        pdf.ln(2)
    pdf.output(str(path))


def write_xlsx(path: Path, sheet_name: str, header: list[str], rows: list[list]):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(header)
    for row in rows:
        ws.append(row)
    wb.save(str(path))


def write_pptx(path: Path, title: str, slides: list[tuple[str, str]]):
    prs = Presentation()
    title_slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(title_slide_layout)
    slide.shapes.title.text = title
    slide.placeholders[1].text = "Meridian Aerospace / Trident Logistics — Internal Review"
    bullet_layout = prs.slide_layouts[1]
    for heading, body in slides:
        s = prs.slides.add_slide(bullet_layout)
        s.shapes.title.text = heading
        tf = s.placeholders[1].text_frame
        tf.text = body
    prs.save(str(path))


def write_csv(path: Path, header: list[str], rows: list[list]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Document catalogue
# ---------------------------------------------------------------------------

def build_documents():
    docs = []  # list of (relative_path, kind, builder_fn)

    # --- Contracts (Meridian side) ---------------------------------------
    contract_body = [
        "This Supply and Services Agreement (\"Agreement\") is entered into between "
        "Meridian Aerospace, a manufacturer of precision fuselage components, and "
        "Trident Logistics, a freight forwarding and customs clearance provider, "
        "effective as of the date first written above.",
        "Trident Logistics shall be responsible for the freight forwarding and customs "
        "clearance of all fuselage components shipped under Project Falcon between the "
        "Toulouse fabrication facility and the Nairobi hub expansion site.",
        "Any shipment delay exceeding five (5) business days shall trigger a penalty "
        "clause of 1.5% of the invoice value per day of delay, up to a cap of 15%.",
        "Meridian Aerospace reserves the right to conduct a compliance review and "
        "quality assurance audit of Trident Logistics' warehousing facilities upon "
        "fourteen (14) days written notice.",
        "This Agreement is subject to a non-disclosure agreement executed concurrently "
        "by both parties, covering all technical specifications, purchase order "
        "volumes, and wire transfer details exchanged under this arrangement.",
    ]
    docs.append(("meridian_legal_hold/contracts/Supply_Agreement_Meridian_Trident_v1.docx",
                 "docx", lambda p: write_docx(p, "Supply and Services Agreement", contract_body)))
    docs.append(("meridian_legal_hold/contracts/Supply_Agreement_Meridian_Trident_v1.pdf",
                 "pdf", lambda p: write_pdf(p, "Supply and Services Agreement", contract_body)))

    amendment_body = [
        "Amendment No. 1 to the Supply and Services Agreement between Meridian "
        "Aerospace and Trident Logistics, dated " + _rand_date(2025, 2025).isoformat() + ".",
        "The penalty clause defined in Section 4 is hereby revised: any shipment delay "
        "exceeding three (3) business days shall trigger a penalty clause of 2% of the "
        "invoice value per day of delay, up to a cap of 20%.",
        "All other terms of the original Supply and Services Agreement, including the "
        "non-disclosure agreement and quality assurance provisions, remain in full force.",
    ]
    docs.append(("meridian_legal_hold/contracts/Amendment_1_Penalty_Clause.docx",
                 "docx", lambda p: write_docx(p, "Amendment No. 1 - Penalty Clause Revision", amendment_body)))

    nda_body = [
        "Non-Disclosure Agreement between Meridian Aerospace and Trident Logistics "
        "covering confidential technical specifications for fuselage components under "
        "the Q3 fuselage contract.",
        "Each party agrees not to disclose purchase order volumes, wire transfer "
        "instructions, or root cause analysis findings from any internal audit to any "
        "third party without prior written consent.",
    ]
    docs.append(("meridian_legal_hold/contracts/NDA_Meridian_Trident.pdf",
                 "pdf", lambda p: write_pdf(p, "Non-Disclosure Agreement", nda_body)))

    # --- Reports (Meridian side) ------------------------------------------
    audit_body = [
        "Internal Audit Report: Trident Logistics Warehousing Compliance Review, "
        "prepared for Meridian Aerospace legal and procurement teams.",
        "The audit identified three recurring shipment delay events during the Nairobi "
        "hub expansion, each linked to incomplete customs clearance documentation.",
        "A root cause analysis attributes the delays to a mismatch between the purchase "
        "order volumes recorded by Trident Logistics and the fuselage components "
        "manifest submitted to customs authorities.",
        "Recommendation: enforce the penalty clause defined in Amendment No. 1 for any "
        "future shipment delay exceeding three business days, and require Trident "
        "Logistics to submit weekly compliance review checkpoints.",
    ]
    docs.append(("meridian_legal_hold/reports/Audit_Report_Trident_Warehousing.pdf",
                 "pdf", lambda p: write_pdf(p, "Internal Audit Report", audit_body)))
    docs.append(("meridian_legal_hold/reports/Audit_Report_Trident_Warehousing_copy.pdf",
                 "pdf", lambda p: write_pdf(p, "Internal Audit Report", audit_body)))  # exact duplicate

    exec_summary = [
        "Executive Summary — Q3 Fuselage Contract Performance Review.",
        "Meridian Aerospace's procurement team reviewed the Q3 fuselage contract "
        "performance with supplier Trident Logistics. Overall on-time delivery for "
        "the quarter was 78%, below the 95% contractual target.",
        "The penalty clause has been invoked twice this quarter for shipment delay "
        "events tied to customs clearance backlogs at the Nairobi hub expansion.",
        "The compliance review board recommends renegotiating freight forwarding "
        "terms and considering Halcyon Freight Partners as a secondary supplier.",
    ]
    docs.append(("meridian_legal_hold/reports/Executive_Summary_Q3_Contract_Review.docx",
                 "docx", lambda p: write_docx(p, "Executive Summary", exec_summary)))

    # --- Financials (Meridian side, spreadsheets) --------------------------
    invoice_rows = []
    running = 0
    for i in range(1, 13):
        amt = round(random.uniform(45000, 210000), 2)
        d = date(2025, ((i - 1) % 12) + 1, min(28, 3 + i))
        status = random.choice(["Paid", "Paid", "Pending", "Disputed"])
        invoice_rows.append([f"INV-MER-{2025}{i:03d}", d.isoformat(), "Trident Logistics", amt, status])
        running += amt
    docs.append(("meridian_legal_hold/financials/Trident_Invoice_Ledger_2025.xlsx",
                 "xlsx", lambda p: write_xlsx(
                     p, "Invoice Ledger",
                     ["Invoice Number", "Date", "Vendor", "Amount (USD)", "Status"],
                     invoice_rows)))
    docs.append(("meridian_legal_hold/financials/Trident_Invoice_Ledger_2025.csv",
                 "csv", lambda p: write_csv(
                     p, ["Invoice Number", "Date", "Vendor", "Amount (USD)", "Status"],
                     invoice_rows)))

    # --- Emails (Meridian side) ---------------------------------------------
    email_scenarios = [
        ("Elena Marsh <elena.marsh@meridianaero.com>",
         "David Okafor <david.okafor@meridianaero.com>",
         "RE: Shipment delay on Nairobi hub expansion",
         [
             "David,",
             "",
             "Following up on the shipment delay affecting the fuselage components "
             "bound for the Nairobi hub expansion. Customs clearance paperwork from "
             "Trident Logistics was incomplete again this week.",
             "",
             "I think we need to invoke the penalty clause under Amendment No. 1 if "
             "this isn't resolved by Friday. Can legal confirm the invoice value we'd "
             "apply the 2% daily penalty to?",
             "",
             "Elena",
         ]),
        ("David Okafor <david.okafor@meridianaero.com>",
         "Elena Marsh <elena.marsh@meridianaero.com>",
         "RE: RE: Shipment delay on Nairobi hub expansion",
         [
             "Elena,",
             "",
             "Confirmed — legal has reviewed. The penalty clause applies to invoice "
             "INV-MER-2025008 at $2% per day of delay, capped at 20%. We're on day 4.",
             "",
             "I'll loop in procurement to also flag this for the Q3 fuselage contract "
             "compliance review.",
             "",
             "David",
         ]),
        ("Priya Nair <priya.nair@meridianaero.com>",
         "Legal Team <legal@meridianaero.com>",
         "Root cause analysis - Trident warehousing audit",
         [
             "Team,",
             "",
             "Attached (separately) is the root cause analysis from our internal audit "
             "of Trident Logistics' warehousing compliance. The mismatch between "
             "purchase order volumes and the customs manifest looks systemic, not a "
             "one-off.",
             "",
             "Recommend we escalate this in the next compliance review meeting.",
             "",
             "Priya",
         ]),
        ("Marcus Webb <marcus.webb@meridianaero.com>",
         "Grace Lindqvist <grace.lindqvist@meridianaero.com>",
         "Halcyon Freight Partners - backup supplier discussion",
         [
             "Grace,",
             "",
             "Given the repeated shipment delay issues with Trident Logistics, "
             "procurement wants to explore Halcyon Freight Partners as a secondary "
             "freight forwarding and customs clearance provider for Project Sable.",
             "",
             "Can we get a quote and a quality assurance questionnaire sent over?",
             "",
             "Marcus",
         ]),
    ]
    for idx, (sender, recipient, subject, body) in enumerate(email_scenarios, start=1):
        d = _rand_date(2025, 2025)
        text = make_email_text(sender, recipient, subject, body, d)
        docs.append((f"meridian_legal_hold/emails/Email_{idx:02d}_{subject[:24].replace(' ', '_').replace('/', '-')}.txt",
                     "txt", lambda p, t=text: write_txt(p, t)))

    # --- Trident side: emails, invoices, meeting notes, spreadsheets -------
    trident_email_scenarios = [
        ("Tomas Reyes <tomas.reyes@tridentlogistics.com>",
         "Sana Farid <sana.farid@tridentlogistics.com>",
         "Customs clearance backlog - Nairobi hub",
         [
             "Sana,",
             "",
             "We have a customs clearance backlog again on the Nairobi hub expansion "
             "shipments. Meridian Aerospace is threatening to invoke the penalty "
             "clause under Amendment No. 1.",
             "",
             "Can operations prioritize the fuselage components manifest paperwork "
             "this week to avoid another shipment delay?",
             "",
             "Tomas",
         ]),
        ("Sana Farid <sana.farid@tridentlogistics.com>",
         "Tomas Reyes <tomas.reyes@tridentlogistics.com>",
         "RE: Customs clearance backlog - Nairobi hub",
         [
             "Tomas,",
             "",
             "Understood. I've reassigned two staff to clear the backlog. We should "
             "have the customs clearance documentation submitted by Wednesday, which "
             "keeps us inside the three business day grace period.",
             "",
             "Sana",
         ]),
        ("Liam O'Connell <liam.oconnell@tridentlogistics.com>",
         "Finance <finance@tridentlogistics.com>",
         "Invoice dispute - Meridian Aerospace INV-MER-2025008",
         [
             "Finance team,",
             "",
             "Meridian Aerospace is disputing invoice INV-MER-2025008, citing the "
             "penalty clause under Amendment No. 1 for the shipment delay on the "
             "Nairobi hub expansion.",
             "",
             "Please hold collections on this invoice pending the compliance review "
             "outcome.",
             "",
             "Liam",
         ]),
    ]
    for idx, (sender, recipient, subject, body) in enumerate(trident_email_scenarios, start=1):
        d = _rand_date(2025, 2025)
        text = make_email_text(sender, recipient, subject, body, d)
        docs.append((f"trident_email_archive/emails/Email_{idx:02d}_{subject[:24].replace(' ', '_').replace('/', '-')}.txt",
                     "txt", lambda p, t=text: write_txt(p, t)))

    trident_invoice_rows = list(invoice_rows)  # near-duplicate financial content, different side
    docs.append(("trident_email_archive/invoices/Invoice_Ledger_Sent_To_Meridian.xlsx",
                 "xlsx", lambda p: write_xlsx(
                     p, "Invoices Sent",
                     ["Invoice Number", "Date", "Vendor", "Amount (USD)", "Status"],
                     trident_invoice_rows)))

    for i in range(1, 6):
        amt = round(random.uniform(2000, 9000), 2)
        d = _rand_date(2025, 2025)
        body_lines = [
            f"Invoice #{1000+i}",
            f"Date: {d.isoformat()}",
            "Bill To: Meridian Aerospace",
            "From: Trident Logistics",
            "",
            f"Freight forwarding and customs clearance services for fuselage "
            f"components shipment, Project Falcon batch {i}.",
            f"Amount due: ${amt:,.2f}",
            "Payment terms: Net 30. Wire transfer details on file per the NDA.",
        ]
        docs.append((f"trident_email_archive/invoices/Invoice_{1000+i}.txt",
                     "txt", lambda p, t="\n".join(body_lines): write_txt(p, t)))

    meeting_notes = [
        "Meeting Notes: Meridian Aerospace / Trident Logistics Quarterly Review",
        f"Date: {_rand_date(2025,2025).isoformat()}  Attendees: Elena Marsh, David Okafor, "
        "Tomas Reyes, Sana Farid",
        "",
        "1. Reviewed shipment delay incidents for Q3 fuselage contract — three events "
        "linked to customs clearance backlogs.",
        "2. Discussed penalty clause application under Amendment No. 1; Trident "
        "Logistics disputes the day count on INV-MER-2025008.",
        "3. Agreed to a joint root cause analysis and a follow-up compliance review "
        "in 30 days.",
        "4. Trident Logistics to provide weekly customs clearance status reports for "
        "the Nairobi hub expansion.",
    ]
    docs.append(("trident_email_archive/meeting_notes/Quarterly_Review_Notes.docx",
                 "docx", lambda p: write_docx(p, "Quarterly Review Notes", meeting_notes)))
    docs.append(("meridian_legal_hold/reports/Quarterly_Review_Notes_Meridian_Copy.docx",
                 "docx", lambda p: write_docx(p, "Quarterly Review Notes", meeting_notes)))  # cross-source duplicate

    shipment_rows = [["Shipment ID", "Origin", "Destination", "Planned Date", "Actual Date", "Delay (days)"]]
    rows = []
    for i in range(1, 16):
        planned = _rand_date(2025, 2025)
        delay = random.choice([0, 0, 0, 1, 2, 3, 4, 6, 8])
        actual = planned + timedelta(days=delay)
        rows.append([f"SHP-{2025}{i:03d}", "Toulouse", "Nairobi", planned.isoformat(),
                     actual.isoformat(), delay])
    docs.append(("trident_email_archive/spreadsheets/Shipment_Tracking_Nairobi_Hub.xlsx",
                 "xlsx", lambda p: write_xlsx(
                     p, "Shipments",
                     ["Shipment ID", "Origin", "Destination", "Planned Date", "Actual Date", "Delay (days)"],
                     rows)))

    # A presentation deck (Trident side) summarizing quality assurance
    qa_slides = [
        ("Quality Assurance Overview", "Warehousing compliance review findings, Q3 2025"),
        ("Root Cause Analysis", "Customs clearance documentation gaps drove most shipment delay events"),
        ("Remediation Plan", "Weekly compliance review checkpoints; dedicated customs clearance staff"),
    ]
    docs.append(("trident_email_archive/meeting_notes/QA_Review_Deck.pptx",
                 "pptx", lambda p: write_pptx(p, "Quality Assurance Review", qa_slides)))

    return docs


def main():
    ensure_dirs()
    docs = build_documents()
    for rel_path, kind, builder in docs:
        full = ROOT / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        builder(full)
    print(f"Generated {len(docs)} realistic documents under {ROOT}")


if __name__ == "__main__":
    main()
