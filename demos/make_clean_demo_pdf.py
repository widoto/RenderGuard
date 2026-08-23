"""Generate a clean academic paper PDF with NO hidden text.

Creates a 2-page PDF that looks like a real NLP research paper.
No payload, no injection — a completely normal document.
Used to demonstrate that RenderGuard correctly returns PASS
on benign documents (zero false positives).

Usage::

    python demos/make_clean_demo_pdf.py
    # verify: should show PASS
    streamlit run app/dashboard.py  # upload demos/demo_clean_paper.pdf
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT_PATH = Path(__file__).resolve().parent / "demo_clean_paper.pdf"

# ── Page geometry ───────────────────────────────────────────────────────

PAGE_W, PAGE_H = A4
MARGIN_TOP = 2.2 * cm
MARGIN_BOTTOM = 2.2 * cm
MARGIN_LEFT = 1.8 * cm
MARGIN_RIGHT = 1.8 * cm
COL_GAP = 0.7 * cm

_content_w = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT
_col_w = (_content_w - COL_GAP) / 2.0
_content_h = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM

# ── Styles ──────────────────────────────────────────────────────────────


def _build_styles():
    ss = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "PaperTitle",
        parent=ss["Title"],
        fontName="Times-Bold",
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=4 * mm,
    )
    author_style = ParagraphStyle(
        "PaperAuthor",
        parent=ss["Normal"],
        fontName="Times-Roman",
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        spaceAfter=2 * mm,
    )
    affiliation_style = ParagraphStyle(
        "Affiliation",
        parent=ss["Normal"],
        fontName="Times-Italic",
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
        spaceAfter=6 * mm,
    )
    section_style = ParagraphStyle(
        "SectionHead",
        parent=ss["Heading2"],
        fontName="Times-Bold",
        fontSize=11,
        leading=14,
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "PaperBody",
        parent=ss["Normal"],
        fontName="Times-Roman",
        fontSize=9.5,
        leading=12,
        alignment=TA_JUSTIFY,
        spaceAfter=2 * mm,
    )
    abstract_label = ParagraphStyle(
        "AbstractLabel",
        parent=body_style,
        fontName="Times-Bold",
        fontSize=9.5,
        alignment=TA_LEFT,
        spaceAfter=1 * mm,
    )
    abstract_body = ParagraphStyle(
        "AbstractBody",
        parent=body_style,
        fontName="Times-Italic",
        fontSize=9,
        leading=11.5,
    )
    caption_style = ParagraphStyle(
        "Caption",
        parent=body_style,
        fontName="Times-Italic",
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER,
        spaceBefore=2 * mm,
        spaceAfter=3 * mm,
    )

    return {
        "title": title_style,
        "author": author_style,
        "affiliation": affiliation_style,
        "section": section_style,
        "body": body_style,
        "abstract_label": abstract_label,
        "abstract": abstract_body,
        "caption": caption_style,
    }


# ── Templates ───────────────────────────────────────────────────────────


def _make_doc(path: str) -> BaseDocTemplate:
    doc = BaseDocTemplate(
        path,
        pagesize=A4,
        leftMargin=MARGIN_LEFT,
        rightMargin=MARGIN_RIGHT,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
    )

    full_frame = Frame(
        MARGIN_LEFT, MARGIN_BOTTOM, _content_w, _content_h, id="full",
    )
    left_frame = Frame(
        MARGIN_LEFT, MARGIN_BOTTOM, _col_w, _content_h, id="col_left",
    )
    right_frame = Frame(
        MARGIN_LEFT + _col_w + COL_GAP,
        MARGIN_BOTTOM, _col_w, _content_h, id="col_right",
    )

    doc.addPageTemplates([
        PageTemplate(id="title_page", frames=[full_frame]),
        PageTemplate(id="two_col", frames=[left_frame, right_frame]),
    ])
    return doc


# ── Content ─────────────────────────────────────────────────────────────

_TITLE = (
    "Cross-Lingual Transfer Learning for Low-Resource"
    " Sentiment Analysis"
)

_AUTHORS = (
    "Seonghwan Kim<super>1</super>, "
    "Hyejin Park<super>1</super>, "
    "Taewoo Yoon<super>2</super>"
)

_AFFILIATION = (
    "<super>1</super>Department of Computer Science,"
    " Seoul National University &nbsp;&nbsp; "
    "<super>2</super>Language Intelligence Lab, KAIST"
)

_ABSTRACT = (
    "We address the challenge of sentiment analysis in low-resource "
    "languages where labeled training data is scarce. Our approach, "
    "CrossSent, leverages multilingual pretrained language models with "
    "a novel alignment-then-transfer framework: we first align "
    "sentiment-bearing representations across languages using parallel "
    "corpora, then fine-tune on high-resource English data and transfer "
    "to 12 target languages. CrossSent achieves an average F1-score of "
    "82.4% across target languages, outperforming zero-shot multilingual "
    "BERT by 6.8 percentage points and few-shot approaches using 100 "
    "labeled examples by 3.1 points. Analysis reveals that alignment "
    "quality correlates strongly with typological similarity (r=0.73), "
    "suggesting that morphologically rich languages benefit most from "
    "explicit representation alignment."
)

_INTRO_P1 = (
    "Sentiment analysis has achieved remarkable accuracy in high-resource "
    "languages such as English and Chinese, driven by large annotated "
    "datasets and powerful pretrained models [1, 2]. However, extending "
    "these advances to the vast majority of the world\u2019s 7,000+ "
    "languages remains a significant challenge. For most languages, "
    "labeled sentiment data is either nonexistent or limited to a few "
    "hundred examples, making supervised fine-tuning unreliable."
)

_INTRO_P2 = (
    "Cross-lingual transfer learning offers a promising path forward. "
    "Multilingual pretrained models such as mBERT [3] and XLM-R [4] "
    "learn shared representations across languages during pretraining, "
    "enabling zero-shot transfer: a model fine-tuned on English can be "
    "applied directly to other languages. However, zero-shot performance "
    "degrades significantly for languages distant from English, "
    "particularly for sentiment-specific nuances that may not align "
    "across cultural and linguistic boundaries."
)

_INTRO_P3 = (
    "We propose CrossSent, a two-stage framework that explicitly aligns "
    "sentiment representations before transfer. In the alignment stage, "
    "we use parallel sentence pairs to learn a projection that maps "
    "sentiment-bearing features into a shared space. In the transfer "
    "stage, we fine-tune on English sentiment data in this aligned space "
    "and apply the model to target languages without additional labeled "
    "data. This decoupling allows each stage to be optimized "
    "independently and provides interpretable diagnostics of transfer "
    "quality."
)

_METHOD_P1 = (
    "CrossSent builds on XLM-R-Base (270M parameters) as the backbone "
    "encoder. The alignment stage uses 50K parallel sentence pairs from "
    "OPUS [5] for each language pair. We add a lightweight projection "
    "head (2-layer MLP, hidden size 256) on top of the [CLS] "
    "representation and train with a contrastive loss that pulls "
    "parallel sentences together while pushing non-parallel pairs apart "
    "in the sentiment-relevant subspace."
)

_METHOD_P2 = (
    "The transfer stage fine-tunes the aligned encoder on the SST-2 "
    "training set (67K English examples) with a standard cross-entropy "
    "loss. At inference time, target-language inputs are encoded by the "
    "same aligned encoder and classified by the English-trained head. "
    "We optionally apply a temperature-scaled calibration step using 20 "
    "unlabeled target-language examples to correct for distribution "
    "shift in the output logits."
)

_RESULTS_P1 = (
    "We evaluate on the multilingual Amazon Reviews dataset [6] covering "
    "12 languages: French, German, Spanish, Japanese, Korean, Arabic, "
    "Hindi, Thai, Vietnamese, Turkish, Swahili, and Yoruba. We report "
    "binary sentiment classification F1-scores on the standard test "
    "splits. Baselines include zero-shot mBERT, zero-shot XLM-R, and "
    "few-shot XLM-R with 100 target-language labeled examples. "
    "Table 1 summarizes the main results."
)

_CONCLUSION = (
    "We presented CrossSent, a cross-lingual transfer framework that "
    "achieves strong sentiment analysis performance in low-resource "
    "languages through explicit representation alignment. Our analysis "
    "highlights the importance of typological similarity in transfer "
    "success and suggests that targeted alignment is most beneficial "
    "for morphologically divergent language pairs. Future work will "
    "explore extending CrossSent to aspect-based sentiment analysis and "
    "investigating alignment strategies for code-switched text."
)

_REF_TEXT = (
    "[1] Socher, R. et al. Recursive deep models for semantic "
    "compositionality over a sentiment treebank. EMNLP, 2013.<br/>"
    "[2] Sun, C. et al. How to fine-tune BERT for text classification. "
    "CCL, 2019.<br/>"
    "[3] Devlin, J. et al. BERT: Pre-training of deep bidirectional "
    "transformers for language understanding. NAACL, 2019.<br/>"
    "[4] Conneau, A. et al. Unsupervised cross-lingual representation "
    "learning at scale. ACL, 2020.<br/>"
    "[5] Tiedemann, J. Parallel data, tools and interfaces in OPUS. "
    "LREC, 2012.<br/>"
    "[6] Keung, P. et al. The multilingual Amazon reviews corpus. "
    "EMNLP, 2020."
)


def _make_table_1(styles) -> Table:
    """Results table."""
    data = [
        ["Method", "High", "Mid", "Low", "Avg"],
        ["mBERT (zero)", "78.3", "72.1", "64.5", "71.6"],
        ["XLM-R (zero)", "81.5", "76.8", "69.2", "75.8"],
        ["XLM-R (100-shot)", "84.1", "79.6", "73.8", "79.2"],
        ["CrossSent (ours)", "87.2", "83.5", "76.4", "82.4"],
    ]
    t = Table(data, colWidths=[80, 40, 40, 40, 40])
    t.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Times-Roman"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("LEADING", (0, 0), (-1, -1), 11),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, black),
            ("LINEBELOW", (0, -1), (-1, -1), 0.8, black),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("FONTNAME", (0, -1), (-1, -1), "Times-Bold"),
        ])
    )
    return t


def _build_story(styles) -> list:
    """Build the full document story — clean, no hidden content."""
    story = []

    # ── Page 1: Title page (single column) ──
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(_TITLE, styles["title"]))
    story.append(Paragraph(_AUTHORS, styles["author"]))
    story.append(Paragraph(_AFFILIATION, styles["affiliation"]))

    story.append(Paragraph("Abstract", styles["abstract_label"]))
    story.append(Paragraph(_ABSTRACT, styles["abstract"]))

    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("1. Introduction", styles["section"]))
    story.append(Paragraph(_INTRO_P1, styles["body"]))
    story.append(Paragraph(_INTRO_P2, styles["body"]))
    story.append(Paragraph(_INTRO_P3, styles["body"]))

    story.append(Paragraph("2. Method", styles["section"]))
    story.append(Paragraph(_METHOD_P1, styles["body"]))
    story.append(Paragraph(_METHOD_P2, styles["body"]))

    # Switch to two-column for page 2+
    story.append(NextPageTemplate("two_col"))
    story.append(PageBreak())

    # ── Page 2: Results (two-column) ──
    story.append(Paragraph("3. Experiments", styles["section"]))
    story.append(Paragraph(_RESULTS_P1, styles["body"]))

    story.append(
        Paragraph(
            "<b>Table 1.</b> F1-score (%) by language resource level "
            "(High / Mid / Low) and overall average.",
            styles["caption"],
        )
    )
    story.append(_make_table_1(styles))
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph("4. Conclusion", styles["section"]))
    story.append(Paragraph(_CONCLUSION, styles["body"]))

    story.append(FrameBreak())

    story.append(Paragraph("References", styles["section"]))
    ref_style = ParagraphStyle(
        "Ref", parent=styles["body"], fontSize=8, leading=10,
    )
    story.append(Paragraph(_REF_TEXT, ref_style))

    return story


# ── Main ────────────────────────────────────────────────────────────────


def main() -> None:
    styles = _build_styles()
    doc = _make_doc(str(OUTPUT_PATH))
    story = _build_story(styles)
    doc.build(story)

    print(f"Generated: {OUTPUT_PATH}")
    print(f"  Pages: 2")
    print(f"  Hidden text: NONE (clean document)")
    print(f"  Purpose: Demonstrate RenderGuard PASS on benign PDF")


if __name__ == "__main__":
    main()
