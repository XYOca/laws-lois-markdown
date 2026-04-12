# Canadian Law / Lois canadiennes - Markdown

A pipeline that converts official Canadian federal legislation from XML to clean, structured Markdown — optimized for readability, versioning, and AI consumption.

## Data Source

Source: [justicecanada/laws-lois-xml](https://github.com/justicecanada/laws-lois-xml) — Government of Canada consolidated federal laws and regulations in XML format.

## Output Structure

```
output/
  en/
    acts/          # ~956 federal acts in English
    regulations/   # ~4845 federal regulations in English
  fr/
    lois/          # ~956 lois fédérales en français
    reglements/    # ~4845 règlements fédéraux en français
```

Files are named with their chapter/instrument number and a slugified title:
`A-1-access-to-information-act.md`, `SOR-2007-151-mv-sonia-remission-order-2007.md`

Each file includes:
- YAML frontmatter (title, chapter number, language, type, last amended date)
- Hierarchical heading structure (`#` title, `##` parts, `###` sections)
- Definitions with bold defined terms
- Numbered subsections, paragraphs, subparagraphs, and clauses
- Tables, formulas, schedules
- Historical notes in collapsible `<details>` blocks

## Automated Updates

A GitHub Actions workflow runs weekly (Sundays at midnight UTC) to:
1. Pull the latest changes from `justicecanada/laws-lois-xml`
2. Incrementally convert only changed XML files to Markdown
3. Commit and push any updates

The workflow can also be triggered manually from the Actions tab. Use the "Force full reconversion" option to regenerate all files.

## Local Usage

```bash
# Clone this repo
git clone https://github.com/XYOca/laws-lois-markdown.git
cd laws-lois-markdown

# Install dependencies
pip install -r requirements.txt

# Run conversion (incremental - only processes changes since last run)
python scripts/convert.py

# Force full reconversion
python scripts/convert.py --force

# Custom output directory
python scripts/convert.py --output-dir ./my-output --data-dir ./my-data
```

## Why Markdown?

| Problem | Solution |
|---------|----------|
| XML is hard to read | Clean, structured Markdown |
| No clean diffs | Git versioning shows exactly what changed |
| Not AI-ready | Normalized structure with frontmatter metadata |
| Hard to search | Plain text, grep-friendly |

## License

The source legislation data is published by the Government of Canada. This conversion tool is open source.
