# Substack PDF Archiver

A small modular Python application for archiving selected Substack newsletters as PDF files.

The application is intentionally divided into three independent stages:

```text
Gmail → Substack → PDF
```

Each stage can be executed and debugged separately.

## Architecture

### 1. Gmail Scanner

`src/gmail_scan.py`

Connects to the Gmail API, searches for emails from selected Substack authors, extracts the corresponding article URLs, and stores them in:

```text
data/articles.json
```

No browser automation is performed in this stage.

---

### 2. Substack PDF Resolver

### 2. Substack PDF Resolver

`src/substack_pdf.py`

Reads the article URLs stored by the Gmail scanner.

Using Selenium with Firefox, it:

1. opens each Substack article;
2. dismisses optional Substack prompts or overlays;
3. opens the article menu;
4. selects `Open as PDF`;
5. captures the resulting PDF URL;
6. stores the result in:

```text
data/pdf_urls.json
```
The Selenium stage uses an isolated browser session and does not require
authentication for publicly accessible Substack articles.

During development, Firefox can run in visible debug mode. In normal
operation, it runs headless.

This stage does not download the final PDF file.

---

### 3. PDF Downloader

`src/download_pdfs.py`

Reads the PDF URLs produced by the Selenium stage and downloads the files using Python HTTP requests.

Files are organized by source:

```text
downloads/
├── rian-stone/
├── corporate-machiavelli/
└── nathan-baugh/
```

The downloader does not interact with Gmail or Selenium.

---

## Modularity

The three stages communicate through JSON files:

```text
gmail_scan.py
      ↓
data/articles.json
      ↓
substack_pdf.py
      ↓
data/pdf_urls.json
      ↓
download_pdfs.py
      ↓
downloads/
```

This architecture allows any failed stage to be restarted without repeating the previous stages.

For example:

* Gmail does not need to be queried again if Selenium fails.
* Selenium does not need to run again if a PDF download fails.
* A new downloader implementation can be tested using the existing `pdf_urls.json`.

---

## Configuration

Newsletter sources are defined in:

```text
config/sources.json
```

This keeps author selection separate from application logic.

Google OAuth credentials are stored locally in:

```text
credentials/
```

Credential files must never be committed to Git.

---

## Selected Sources

Initial sources:

* Rian Stone
* Machiavelli Bot / Corporate Machiavelli
* Nathan Baugh / Worldbuilders

Additional sources can later be added through configuration without changing the core pipeline.

---

## Project Goal

The goal is not to mirror an entire Substack inbox.

The application selectively preserves articles from chosen authors using Substack's own PDF representation while keeping discovery, browser automation, and file download as independent components.

Fase 1 — Execução manual
python gmail_scan.py
python substack_pdf.py
python download_pdfs.py

Fase 2 — Aprovação seletiva
gmail_scan.py
→ relatório
→ usuário escolhe artigos
→ restante do pipeline

Fase 3 — Scanner diário
systemd timer / cron
→ gmail_scan.py
→ notificação se houver novidades
→ nenhuma ação Selenium automática

Substack authentication:
- required only for resolving the PDF endpoint through the UI;
- handled through a dedicated persistent Firefox profile;
- no credentials are stored by the application.

PDF resolution:
- Selenium opens the authenticated article menu;
- selects Open as PDF;
- captures the resulting /api/v1/post/pdf endpoint.

PDF download:
- performed independently with standard HTTP requests;
- no Selenium cookies are required.

## Development environment

Activate the virtual environment:

```bash
cd ~/Projetos/substack-pdf-archiver
source .venv/bin/activate
```
Deactivate when finished:

```bash
deactivate
```

Current development environment uses Python 3.8.
Google API libraries already warn that this version is unsupported.
Upgrade to Python >= 3.10 before treating the application as stable.