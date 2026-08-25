# Third-Party Dependencies

RenderGuard uses the following open-source libraries.
PyMuPDF is AGPL-3.0 licensed, which is why this project is distributed under AGPL-3.0.

## Core (required)

| Library | Version | License | Purpose |
|---------|---------|---------|---------|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | 1.28.0 | AGPL-3.0 | PDF rendering, text extraction, content stream manipulation |
| [NumPy](https://github.com/numpy/numpy) | 2.3.1 | BSD-3-Clause | Pixel array operations, contrast/color distance computation |

## Optional

| Library | Version | License | Purpose |
|---------|---------|---------|---------|
| [langchain-core](https://github.com/langchain-ai/langchain) | 1.5.3 | MIT | LangChain document loader integration (`integrators/langchain_loader.py`) |
| [Streamlit](https://github.com/streamlit/streamlit) | 1.61.1 | Apache-2.0 | Interactive dashboard UI (`app/dashboard.py`) |
| [ReportLab](https://github.com/MrBitBucket/reportlab-mirror) | 5.0.0 | BSD-3-Clause | Demo/test PDF generation (`demos/`, `tools/`) |
