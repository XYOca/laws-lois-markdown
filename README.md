# 🇨🇦 Canadian Law → Markdown

A developer-focused pipeline that converts official Canadian legislation into clean, structured Markdown — optimized for readability, versioning, and AI consumption.

---

## 📦 Data Source

Primary source:

- justicecanada/laws-lois-xml (Government of Canada legislation XML repo)

This repository contains consolidated federal laws and regulations in XML format, maintained by the Government of Canada.

## ⚙️ Features

- Clone + sync official legislation repo  
- Parse XML into structured hierarchy  
- Convert to clean Markdown:
  - `#` Act title  
  - `##` Sections  
  - `###` Subsections  
- Preserve numbering + structure  
- Add frontmatter metadata  
- Slugified, organized file output  
- Optional combined output per Act  

---

## 🧠 Why This Matters

Legal data in Canada is already open — but not developer-friendly.

This project acts as a **translation layer**:

| Problem | Solution |
|--------|---------|
| XML is hard to read | Convert to Markdown |
| No clean diffs | Use Git versioning |
| Not AI-ready | Normalize + structure content |

---

## 📂 Project Structure

- `data/` — cloned XML repo  
- `output/` — generated Markdown files  
- `scripts/convert.py` — main conversion script  
- `logs/` — runtime logs  
- `requirements.txt` — dependencies  
- `README.md` — project documentation  

---

## 🛠️ Setup

```bash
git clone <your-repo>
cd <your-repo>

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

## ▶️ Usage
```bash
python scripts/convert.py \
  --repo-url https://github.com/justicecanada/laws-lois-xml \
  --output-dir ./output \
  --pull-latest
```

## 💡 Vision
Laws as code
Amendments as commits
Legal diffs as pull requests
