#!/usr/bin/env python3
"""
Canadian Law XML → Markdown Converter

Pulls legislation XML from justicecanada/laws-lois-xml and converts
to clean, structured Markdown files.

Usage:
    python scripts/convert.py [--output-dir ./output] [--data-dir ./data]
"""

import argparse
import io
import os
import re
import subprocess
import sys
from pathlib import Path

# Ensure stdout handles unicode on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from lxml import etree


SOURCE_REPO = "https://github.com/justicecanada/laws-lois-xml.git"

# Mapping of source directories to output paths
LANG_MAP = {
    "eng/acts": ("en", "acts"),
    "eng/regulations": ("en", "regulations"),
    "fra/lois": ("fr", "lois"),
    "fra/reglements": ("fr", "reglements"),
}


def clone_or_pull(data_dir: Path) -> str:
    """Clone the source repo or pull latest changes. Returns current commit hash."""
    repo_dir = data_dir / "laws-lois-xml"

    if (repo_dir / ".git").exists():
        print(f"Pulling latest changes in {repo_dir}...")
        subprocess.run(
            ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", "main"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "reset", "--hard", "origin/main"],
            check=True,
        )
    else:
        print(f"Cloning {SOURCE_REPO} into {repo_dir}...")
        data_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", SOURCE_REPO, str(repo_dir)],
            check=True,
        )

    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_previous_commit(data_dir: Path) -> str | None:
    """Get the previously processed source commit hash."""
    commit_file = data_dir / ".source-commit"
    if commit_file.exists():
        return commit_file.read_text().strip()
    return None


def get_changed_xml_files(data_dir: Path, previous_commit: str, current_commit: str) -> set[str] | None:
    """Get list of changed XML files between two commits.

    Returns a set of relative paths (e.g. 'eng/acts/A-1.xml'), or None if
    we can't determine changes (e.g. first run, shallow clone missing history).
    """
    repo_dir = data_dir / "laws-lois-xml"

    # Fetch enough history to diff. The previous commit may have been pruned
    # by the shallow clone, so we deepen just enough.
    try:
        subprocess.run(
            ["git", "-C", str(repo_dir), "fetch", "--deepen", "50", "origin", "main"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None

    # Check if the previous commit is reachable
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "cat-file", "-t", previous_commit],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Previous commit {previous_commit[:7]} not reachable, will do full conversion.")
        return None

    # Get changed files
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "diff", "--name-only", previous_commit, current_commit],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    changed = set()
    for line in result.stdout.strip().splitlines():
        if line.endswith(".xml"):
            changed.add(line)
    return changed


def save_commit(data_dir: Path, commit: str):
    """Save the current source commit hash."""
    commit_file = data_dir / ".source-commit"
    data_dir.mkdir(parents=True, exist_ok=True)
    commit_file.write_text(commit)


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[''`]", "", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# ---------------------------------------------------------------------------
# XML text extraction helpers
# ---------------------------------------------------------------------------

def get_text(elem) -> str:
    """Recursively extract all text from an element, handling inline children."""
    if elem is None:
        return ""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        tag = child.tag
        if tag == "DefinedTermEn" or tag == "DefinedTermFr":
            parts.append(f"**{get_text(child)}**")
        elif tag == "XRefExternal":
            parts.append(f"*{get_text(child)}*")
        elif tag == "XRefInternal":
            parts.append(get_text(child))
        elif tag == "DefinitionRef":
            parts.append(f"**{get_text(child)}**")
        elif tag == "Emphasis":
            parts.append(f"*{get_text(child)}*")
        elif tag == "Sup":
            parts.append(f"^{get_text(child)}^")
        elif tag == "Sub":
            parts.append(f"~{get_text(child)}~")
        elif tag == "FootnoteRef":
            ref = child.get("idRef", "")
            parts.append(f"[^{ref}]")
        elif tag == "Footnote":
            fid = child.get("id", "")
            parts.append(f"[^{fid}]: {get_text(child)}")
        elif tag == "FormBlank":
            parts.append("____________")
        elif tag == "Leader" or tag == "LeaderRightJustified":
            parts.append(" ... ")
        elif tag == "Repealed":
            parts.append(get_text(child))
        elif tag == "Ins":
            parts.append(get_text(child))
        elif tag == "QuotedText":
            parts.append(f'"{get_text(child)}"')
        elif tag == "IBR":
            parts.append(get_text(child))
        else:
            # For any unrecognized inline element, just grab text
            parts.append(get_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def get_label(elem) -> str:
    """Get the label text from a Label child element."""
    label_elem = elem.find("Label")
    if label_elem is not None:
        return get_text(label_elem)
    return ""


def get_marginal_note(elem) -> str:
    """Get marginal note text."""
    mn = elem.find("MarginalNote")
    if mn is not None:
        return get_text(mn)
    return ""


# ---------------------------------------------------------------------------
# Element converters
# ---------------------------------------------------------------------------

def convert_table(elem) -> str:
    """Convert a TableGroup/table element to markdown."""
    lines = []
    table = elem.find(".//table") if elem.tag == "TableGroup" else elem
    if table is None:
        table = elem

    for tgroup in table.findall("tgroup"):
        cols = int(tgroup.get("cols", "2"))
        thead = tgroup.find("thead")
        tbody = tgroup.find("tbody")

        if thead is not None:
            for row in thead.findall("row"):
                cells = [get_text(e) for e in row.findall("entry")]
                while len(cells) < cols:
                    cells.append("")
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("| " + " | ".join(["---"] * cols) + " |")
        else:
            lines.append("| " + " | ".join(["---"] * cols) + " |")
            lines.append("| " + " | ".join(["---"] * cols) + " |")

        if tbody is not None:
            for row in tbody.findall("row"):
                cells = [get_text(e) for e in row.findall("entry")]
                while len(cells) < cols:
                    cells.append("")
                lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def convert_formula(elem) -> str:
    """Convert a Formula/FormulaGroup to markdown code block."""
    lines = []
    for child in elem:
        tag = child.tag
        if tag == "FormulaText":
            lines.append(get_text(child))
        elif tag == "FormulaTerm":
            lines.append(get_text(child))
        elif tag == "FormulaDefinition":
            lines.append(get_text(child))
        elif tag == "FormulaConnector":
            lines.append(get_text(child))
        elif tag == "FormulaGroup":
            lines.append(convert_formula(child))
        else:
            t = get_text(child)
            if t:
                lines.append(t)
    text = "\n".join(lines)
    if text:
        return f"\n```\n{text}\n```\n"
    return ""


def convert_definition(elem, indent: str = "") -> str:
    """Convert a Definition element."""
    lines = []
    text_elem = elem.find("Text")
    if text_elem is not None:
        lines.append(f"{indent}{get_text(text_elem)}")

    for child in elem:
        tag = child.tag
        if tag == "Paragraph":
            label = get_label(child)
            text = get_text(child.find("Text")) if child.find("Text") is not None else ""
            lines.append(f"{indent}  - {label} {text}".rstrip())
            for sub in child.findall("Subparagraph"):
                slabel = get_label(sub)
                stext = get_text(sub.find("Text")) if sub.find("Text") is not None else ""
                lines.append(f"{indent}    - {slabel} {stext}".rstrip())
        elif tag == "ContinuedDefinition":
            lines.append(f"{indent}{get_text(child)}")

    return "\n".join(lines)


def convert_section(elem) -> str:
    """Convert a Section element to markdown."""
    lines = []
    label = get_label(elem)
    mn = get_marginal_note(elem)

    if mn:
        lines.append(f"### {label} {mn}" if label else f"### {mn}")
    elif label:
        lines.append(f"### {label}")

    # Process direct Text child
    text_elem = elem.find("Text")
    if text_elem is not None:
        lines.append("")
        lines.append(get_text(text_elem))

    # Process subsections, paragraphs, definitions, etc.
    for child in elem:
        tag = child.tag
        if tag in ("Label", "MarginalNote", "Text", "HistoricalNote"):
            continue
        elif tag == "Subsection":
            lines.append(convert_subsection(child))
        elif tag == "Paragraph":
            lines.append(convert_paragraph(child, indent=""))
        elif tag == "Definition":
            lines.append("")
            lines.append(convert_definition(child))
        elif tag == "ContinuedSectionSubsection":
            lines.append("")
            lines.append(get_text(child))
        elif tag == "TableGroup":
            lines.append("")
            lines.append(convert_table(child))
        elif tag == "Formula" or tag == "FormulaGroup":
            lines.append(convert_formula(child))
        elif tag == "FormGroup":
            lines.append("")
            lines.append(get_text(child))
        elif tag == "Repealed":
            lines.append("")
            lines.append(get_text(child))
        elif tag == "Provision":
            lines.append("")
            lines.append(get_text(child))

    # Historical note
    hn = elem.find("HistoricalNote")
    if hn is not None:
        lines.append(convert_historical_note(hn))

    return "\n".join(lines)


def convert_subsection(elem) -> str:
    """Convert a Subsection element."""
    lines = []
    label = get_label(elem)
    mn = get_marginal_note(elem)

    if mn:
        prefix = f"**{mn}**"
        lines.append("")
        lines.append(prefix)

    text_elem = elem.find("Text")
    text = get_text(text_elem) if text_elem is not None else ""
    if text:
        lines.append("")
        lines.append(f"{label} {text}" if label else text)

    for child in elem:
        tag = child.tag
        if tag in ("Label", "MarginalNote", "Text", "HistoricalNote"):
            continue
        elif tag == "Paragraph":
            lines.append(convert_paragraph(child, indent=""))
        elif tag == "Definition":
            lines.append("")
            lines.append(convert_definition(child))
        elif tag == "ContinuedSectionSubsection":
            lines.append("")
            lines.append(get_text(child))
        elif tag == "ContinuedParagraph":
            lines.append("")
            lines.append(get_text(child))
        elif tag == "TableGroup":
            lines.append("")
            lines.append(convert_table(child))
        elif tag == "Formula" or tag == "FormulaGroup":
            lines.append(convert_formula(child))
        elif tag == "FormGroup":
            lines.append("")
            lines.append(get_text(child))
        elif tag == "Repealed":
            lines.append("")
            lines.append(get_text(child))

    hn = elem.find("HistoricalNote")
    if hn is not None:
        lines.append(convert_historical_note(hn))

    return "\n".join(lines)


def convert_paragraph(elem, indent: str = "") -> str:
    """Convert a Paragraph element."""
    lines = []
    label = get_label(elem)
    text_elem = elem.find("Text")
    text = get_text(text_elem) if text_elem is not None else ""

    lines.append(f"\n{indent}- {label} {text}".rstrip())

    for child in elem:
        tag = child.tag
        if tag in ("Label", "Text", "MarginalNote", "HistoricalNote"):
            continue
        elif tag == "Subparagraph":
            lines.append(convert_subparagraph(child, indent=indent + "  "))
        elif tag == "ContinuedParagraph":
            lines.append(f"{indent}  {get_text(child)}")
        elif tag == "Definition":
            lines.append(convert_definition(child, indent=indent + "  "))
        elif tag == "TableGroup":
            lines.append(convert_table(child))
        elif tag == "Formula" or tag == "FormulaGroup":
            lines.append(convert_formula(child))
        elif tag == "FormGroup":
            lines.append(f"{indent}  {get_text(child)}")

    return "\n".join(lines)


def convert_subparagraph(elem, indent: str = "  ") -> str:
    """Convert a Subparagraph element."""
    lines = []
    label = get_label(elem)
    text_elem = elem.find("Text")
    text = get_text(text_elem) if text_elem is not None else ""

    lines.append(f"{indent}- {label} {text}".rstrip())

    for child in elem:
        tag = child.tag
        if tag in ("Label", "Text", "MarginalNote", "HistoricalNote"):
            continue
        elif tag == "Clause":
            lines.append(convert_clause(child, indent=indent + "  "))
        elif tag == "ContinuedParagraph":
            lines.append(f"{indent}  {get_text(child)}")

    return "\n".join(lines)


def convert_clause(elem, indent: str = "    ") -> str:
    """Convert a Clause element."""
    label = get_label(elem)
    text_elem = elem.find("Text")
    text = get_text(text_elem) if text_elem is not None else ""
    return f"{indent}- {label} {text}".rstrip()


def convert_historical_note(elem) -> str:
    """Convert a HistoricalNote to a collapsible details block."""
    items = []
    for sub in elem.findall("HistoricalNoteSubItem"):
        items.append(get_text(sub))

    if not items:
        return ""

    lines = [
        "",
        "<details>",
        "<summary>Historical Note</summary>",
        "",
    ]
    for item in items:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("</details>")
    return "\n".join(lines)


def convert_schedule(elem) -> str:
    """Convert a Schedule element."""
    lines = []
    heading = elem.find("ScheduleFormHeading")
    if heading is not None:
        label = heading.find("Label")
        title = heading.find("TitleText")
        origin = heading.find("OriginatingRef")

        parts = []
        if label is not None:
            parts.append(get_text(label))
        if title is not None:
            parts.append(get_text(title))
        lines.append(f"\n## {'  '.join(parts)}")
        if origin is not None:
            lines.append(f"\n{get_text(origin)}")

    for child in elem:
        tag = child.tag
        if tag == "ScheduleFormHeading":
            continue
        elif tag == "Section":
            lines.append("")
            lines.append(convert_section(child))
        elif tag == "TableGroup":
            lines.append("")
            lines.append(convert_table(child))
        elif tag == "BilingualGroup":
            lines.append(convert_bilingual_group(child))
        elif tag == "FormGroup":
            lines.append("")
            lines.append(get_text(child))
        elif tag == "Heading":
            title_text = child.find("TitleText")
            if title_text is not None:
                lines.append(f"\n### {get_text(title_text)}")
        elif tag == "HistoricalNote":
            lines.append(convert_historical_note(child))
        elif tag == "BillPiece" or tag == "RegulationPiece":
            lines.append(convert_bill_piece(child))
        elif tag == "ImageGroup":
            caption = child.find("Caption")
            if caption is not None:
                lines.append(f"\n*{get_text(caption)}*")
            else:
                lines.append("\n*[Image]*")
        elif tag == "Item":
            label = get_label(child)
            text_elem = child.find("Text")
            text = get_text(text_elem) if text_elem is not None else ""
            lines.append(f"\n- {label} {text}".rstrip())

    return "\n".join(lines)


def convert_bilingual_group(elem) -> str:
    """Convert a BilingualGroup element."""
    lines = []
    title = elem.find("TitleText")
    if title is not None:
        lines.append(f"\n### {get_text(title)}")

    en_items = elem.findall("BilingualItemEn")
    fr_items = elem.findall("BilingualItemFr")

    if en_items and fr_items and len(en_items) == len(fr_items):
        lines.append("")
        lines.append("| English | French |")
        lines.append("| --- | --- |")
        for en, fr in zip(en_items, fr_items):
            lines.append(f"| {get_text(en)} | {get_text(fr)} |")
    else:
        for child in elem:
            tag = child.tag
            if tag == "TitleText":
                continue
            elif tag in ("BilingualItemEn", "BilingualItemFr"):
                lines.append(f"- {get_text(child)}")

    return "\n".join(lines)


def convert_bill_piece(elem) -> str:
    """Convert a BillPiece or RegulationPiece."""
    lines = []
    for child in elem:
        tag = child.tag
        if tag == "RelatedOrNotInForce":
            for sub in child:
                if sub.tag == "Heading":
                    title = sub.find("TitleText")
                    if title is not None:
                        lines.append(f"\n### {get_text(title)}")
                elif sub.tag == "Section":
                    lines.append("")
                    lines.append(convert_section(sub))
        elif tag == "Section":
            lines.append("")
            lines.append(convert_section(child))
        elif tag == "Heading":
            title = child.find("TitleText")
            if title is not None:
                lines.append(f"\n### {get_text(title)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main document converter
# ---------------------------------------------------------------------------

def extract_metadata(root) -> dict:
    """Extract frontmatter metadata from the document."""
    meta = {}
    ident = root.find("Identification")

    if ident is not None:
        short_title = ident.find("ShortTitle")
        if short_title is not None:
            meta["title"] = get_text(short_title)

        long_title = ident.find("LongTitle")
        if long_title is not None:
            meta["long_title"] = get_text(long_title)

        chapter = ident.find("Chapter")
        if chapter is not None:
            cn = chapter.find("ConsolidatedNumber")
            if cn is not None:
                meta["chapter"] = get_text(cn)

        instr = ident.find("InstrumentNumber")
        if instr is not None:
            meta["instrument_number"] = get_text(instr)

    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang", "")
    if not lang:
        lang_elem = ident.find("Language") if ident is not None else None
        if lang_elem is not None:
            lang = get_text(lang_elem)
    meta["language"] = lang or "en"

    last_amended = root.get("{http://justice.gc.ca/lims}lastAmendedDate", "")
    if last_amended:
        meta["last_amended"] = last_amended

    pit_date = root.get("{http://justice.gc.ca/lims}pit-date", "")
    if pit_date:
        meta["point_in_time"] = pit_date

    meta["type"] = "act" if root.tag == "Statute" else "regulation"

    return meta


def format_frontmatter(meta: dict) -> str:
    """Format metadata as YAML frontmatter."""
    lines = ["---"]
    for key, value in meta.items():
        # Escape quotes in values
        value = str(value).replace('"', '\\"')
        lines.append(f'{key}: "{value}"')
    lines.append("---")
    return "\n".join(lines)


def convert_document(xml_path: Path) -> tuple[str, dict]:
    """Convert a single XML document to Markdown. Returns (markdown, metadata)."""
    try:
        tree = etree.parse(str(xml_path))
    except etree.XMLSyntaxError as e:
        print(f"  WARNING: XML parse error in {xml_path}: {e}")
        return "", {}

    root = tree.getroot()
    # Strip namespace prefixes for easier tag matching
    for elem in root.iter():
        # Skip comments and processing instructions (tag is a callable, not str)
        if not isinstance(elem.tag, str):
            continue
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
        # Also strip namespace from attributes
        new_attrib = {}
        for k, v in elem.attrib.items():
            if "}" in k:
                k = k.split("}", 1)[1]
            new_attrib[k] = v
        elem.attrib.clear()
        elem.attrib.update(new_attrib)

    meta = extract_metadata(root)
    lines = [format_frontmatter(meta), ""]

    # Title
    title = meta.get("title", "")
    if title:
        lines.append(f"# {title}")
        lines.append("")

    long_title = meta.get("long_title", "")
    if long_title and long_title != title:
        lines.append(f"> {long_title}")
        lines.append("")

    # Body
    body = root.find("Body")
    if body is not None:
        for child in body:
            tag = child.tag
            if tag == "Heading":
                level = child.get("level", "1")
                title_text = child.find("TitleText")
                if title_text is not None:
                    heading = get_text(title_text)
                    if level == "1":
                        lines.append(f"\n## {heading}")
                    elif level == "2":
                        lines.append(f"\n### {heading}")
                    else:
                        lines.append(f"\n#### {heading}")
            elif tag == "Section":
                lines.append("")
                lines.append(convert_section(child))
            elif tag == "TableGroup":
                lines.append("")
                lines.append(convert_table(child))
            elif tag == "Formula" or tag == "FormulaGroup":
                lines.append(convert_formula(child))

    # Order (some regulations have an Order element before Body)
    order = root.find("Order")
    if order is not None:
        provision = order.find("Provision")
        if provision is not None:
            text = get_text(provision.find("Text")) if provision.find("Text") is not None else get_text(provision)
            if text:
                lines.append("")
                lines.append(f"> {text}")
                lines.append("")

    # Schedules
    for schedule in root.findall("Schedule"):
        lines.append("")
        lines.append(convert_schedule(schedule))

    return "\n".join(lines) + "\n", meta


def make_filename(meta: dict, xml_filename: str) -> str:
    """Generate a clean filename from metadata."""
    # Get identifier
    ident = meta.get("chapter") or meta.get("instrument_number") or ""
    ident = ident.replace("/", "-").replace("\\", "-").replace(",", "").replace(" ", "-")
    ident = re.sub(r"-+", "-", ident).strip("-")

    title = meta.get("title", "")
    slug = slugify(title) if title else Path(xml_filename).stem

    # Truncate slug to avoid filesystem path length limits
    max_slug = 80
    if len(slug) > max_slug:
        slug = slug[:max_slug].rstrip("-")

    if ident:
        return f"{ident}-{slug}.md" if slug else f"{ident}.md"
    return f"{slug}.md"


def convert_all(data_dir: Path, output_dir: Path, changed_files: set[str] | None = None):
    """Convert XML files to Markdown.

    If changed_files is provided, only those files are converted (incremental mode).
    If None, all files are converted (full mode).
    """
    repo_dir = data_dir / "laws-lois-xml"
    total = 0
    errors = 0

    for source_subdir, (lang, category) in LANG_MAP.items():
        source_path = repo_dir / source_subdir
        if not source_path.exists():
            print(f"  Skipping {source_subdir} (not found)")
            continue

        out_path = output_dir / lang / category
        out_path.mkdir(parents=True, exist_ok=True)

        if changed_files is not None:
            # Incremental: only convert changed files in this subdirectory
            xml_files = []
            for rel_path in changed_files:
                if rel_path.startswith(source_subdir + "/"):
                    full = repo_dir / rel_path
                    if full.exists():
                        xml_files.append(full)
            xml_files.sort()
            if not xml_files:
                continue
            print(f"  Converting {len(xml_files)} changed files from {source_subdir}...")
        else:
            xml_files = sorted(source_path.glob("*.xml"))
            print(f"  Converting {len(xml_files)} files from {source_subdir}...")

        for xml_file in xml_files:
            try:
                md_content, meta = convert_document(xml_file)
                if not md_content:
                    errors += 1
                    continue

                filename = make_filename(meta, xml_file.name)

                out_file = out_path / filename
                out_file.write_text(md_content, encoding="utf-8")
                total += 1
            except Exception as e:
                print(f"  ERROR processing {xml_file.name}: {e}")
                errors += 1

    print(f"\nDone: {total} files converted, {errors} errors")
    return total


def main():
    parser = argparse.ArgumentParser(description="Convert Canadian legislation XML to Markdown")
    parser.add_argument("--output-dir", default="./laws", help="Output directory for Markdown files")
    parser.add_argument("--data-dir", default="./data", help="Directory for cloned source repo")
    parser.add_argument("--force", action="store_true", help="Force full reconversion of all files")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    data_dir = Path(args.data_dir).resolve()

    print("=== Canadian Law XML -> Markdown Converter ===\n")

    # Step 1: Clone or pull source
    commit = clone_or_pull(data_dir)
    print(f"Source commit: {commit}\n")

    # Step 2: Determine what to convert
    previous_commit = get_previous_commit(data_dir)
    changed_files = None

    needs_conversion = True
    if args.force:
        print("Force mode: converting all files.\n")
    elif previous_commit is None:
        print("First run: converting all files.\n")
    elif previous_commit == commit:
        print("Source repo unchanged since last run. Skipping conversion.")
        needs_conversion = False
    else:
        print(f"Previous commit: {previous_commit[:7]}")
        print(f"Current commit:  {commit[:7]}")
        changed_files = get_changed_xml_files(data_dir, previous_commit, commit)
        if changed_files is not None:
            if not changed_files:
                print("No XML files changed. Skipping conversion.")
                needs_conversion = False
            else:
                print(f"Incremental mode: {len(changed_files)} XML files changed.\n")
        else:
            print("Could not determine changes, converting all files.\n")

    # Step 3: Convert (if needed)
    if needs_conversion:
        print("Converting XML to Markdown...\n")
        total = convert_all(data_dir, output_dir, changed_files)

        if total > 0:
            save_commit(data_dir, commit)
            print(f"\nSource commit saved: {commit}")
        else:
            print("\nNo files converted.")
            sys.exit(1)

    # Step 4: Always regenerate index
    print("\nGenerating index...")
    generate_index(output_dir)


def parse_frontmatter(file_path: Path) -> dict:
    """Parse YAML frontmatter from a markdown file."""
    meta = {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            if first_line != "---":
                return meta
            for line in f:
                line = line.strip()
                if line == "---":
                    break
                if ": " in line:
                    key, value = line.split(": ", 1)
                    meta[key] = value.strip('"')
    except Exception:
        pass
    return meta


def generate_index(output_dir: Path):
    """Generate INDEX.md files listing all laws with titles and descriptions."""
    # Category display names
    category_labels = {
        ("en", "acts"): ("English", "Federal Acts"),
        ("en", "regulations"): ("English", "Federal Regulations"),
        ("fr", "lois"): ("Français", "Lois fédérales"),
        ("fr", "reglements"): ("Français", "Règlements fédéraux"),
    }

    all_entries = {}

    for (lang, category), (lang_label, cat_label) in category_labels.items():
        cat_dir = output_dir / lang / category
        if not cat_dir.exists():
            continue

        entries = []
        for md_file in sorted(cat_dir.glob("*.md")):
            if md_file.name == "INDEX.md":
                continue
            meta = parse_frontmatter(md_file)
            title = meta.get("title", md_file.stem)
            chapter = meta.get("chapter", meta.get("instrument_number", ""))
            long_title = meta.get("long_title", "")
            rel_path = f"{lang}/{category}/{md_file.name}"
            entries.append({
                "file": md_file.name,
                "rel_path": rel_path,
                "title": title,
                "chapter": chapter,
                "long_title": long_title,
            })

        all_entries[(lang, category)] = entries

        # Write per-category INDEX.md
        lines = [f"# {cat_label}\n"]
        lines.append(f"_{len(entries)} documents_\n")
        lines.append("| # | Title | Description |")
        lines.append("| --- | --- | --- |")
        for e in entries:
            desc = e["long_title"] if e["long_title"] and e["long_title"] != e["title"] else ""
            # Escape pipes in table cells
            title_cell = e["title"].replace("|", "\\|")
            desc_cell = desc.replace("|", "\\|")
            ident = e["chapter"]
            lines.append(f"| {ident} | [{title_cell}]({e['file']}) | {desc_cell} |")

        index_path = cat_dir / "INDEX.md"
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {lang}/{category}/INDEX.md ({len(entries)} entries)")

    # Write root INDEX.md
    root_lines = ["# Canadian Federal Legislation / Législation fédérale du Canada\n"]
    root_lines.append("Complete collection of Canadian federal laws and regulations in Markdown format.\n")
    root_lines.append("Collection complète des lois et règlements fédéraux du Canada en format Markdown.\n")

    total = sum(len(v) for v in all_entries.values())
    root_lines.append(f"**{total:,} documents total**\n")

    root_lines.append("## Contents / Table des matières\n")
    root_lines.append("| Category | Count | Index |")
    root_lines.append("| --- | --- | --- |")

    for (lang, category), (lang_label, cat_label) in category_labels.items():
        entries = all_entries.get((lang, category), [])
        root_lines.append(f"| {cat_label} ({lang_label}) | {len(entries):,} | [{lang}/{category}/INDEX.md]({lang}/{category}/INDEX.md) |")

    root_index = output_dir / "INDEX.md"
    root_index.write_text("\n".join(root_lines) + "\n", encoding="utf-8")
    print(f"  INDEX.md (root, {total:,} total)")


if __name__ == "__main__":
    main()
